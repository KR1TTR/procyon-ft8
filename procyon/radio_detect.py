"""Find the radio on the computer's serial ports, and test a radio setup.

Detection asks each port "who are you?" in the radios' own CAT languages
(Yaesu/Kenwood/Elecraft "ID;" and Icom CI-V "read frequency"), at the baud
rates those radios ship with. Nothing here ever transmits: only ID and
frequency reads are sent, and RTS/DTR are dropped as soon as a port opens.
macOS and Linux use the system's serial calls (termios); Windows uses pyserial,
which is bundled with the Windows build."""

import logging
import os
import re
import subprocess

from .setup_help import hamlib_rigs
from .system import IS_WINDOWS, NO_WINDOW, find_tool, serial_access_problem, serial_ports

try:
    import fcntl
    import select
    import struct
    import termios
    SerialError = (OSError, termios.error)
except ImportError:      # Windows
    termios = None
    SerialError = OSError

log = logging.getLogger("procyon.detect")

# "ID;" answers: Yaesu "ID0670;" (FT-991A), Kenwood "ID021;" (TS-590SG).
# value: (manufacturer, Hamlib model name, what to call it)
YAESU = {
    "0670": ("FT-991", "FT-991A"), "0570": ("FT-991", "FT-991"), "0650": ("FT-891", "FT-891"),
    "0800": ("FT-710", "FT-710"), "0761": ("FTDX-10", "FTDX10"), "0681": ("FTDX-101D", "FTDX101D"),
    "0682": ("FTDX-101MP", "FTDX101MP"), "0460": ("FTDX-3000", "FTDX3000"),
    "0583": ("FTDX-1200", "FTDX1200"), "0310": ("FT-950", "FT-950"), "0251": ("FT-2000", "FT-2000"),
    "0244": ("FT-450D", "FT-450D"),
}
KENWOOD = {"020": ("TS-480", "TS-480"), "021": ("TS-590SG", "TS-590SG"), "024": ("TS-890S", "TS-890S")}
# CI-V default addresses
ICOM = {0x94: ("IC-7300", "IC-7300"), 0xA4: ("IC-705", "IC-705"), 0x98: ("IC-7610", "IC-7610"),
        0xA2: ("IC-9700", "IC-9700"), 0x88: ("IC-7100", "IC-7100"), 0x76: ("IC-7200", "IC-7200"),
        0x7A: ("IC-7600", "IC-7600"), 0x80: ("IC-7410", "IC-7410"), 0x70: ("IC-7000", "IC-7000")}

# Elecraft radios all answer "ID017;", so the user picks one of these
ELECRAFT = ("KX3", "KX2", "K3", "K3S", "K4")

# Most radios ship at one of these; the likeliest first.
ASCII_BAUDS = (38400, 9600, 19200, 115200, 4800)
CIV_BAUDS = (115200, 19200, 9600, 38400)


def _model(mfg: str, name: str):
    for r in hamlib_rigs():
        if r["mfg"].lower().startswith(mfg.lower()) and r["name"] == name:
            return str(r["model"])
    return ""


class _PosixPort:
    """A serial port through termios (Linux, macOS)."""
    def __init__(self, path: str, baud: int):
        self.fd = _open(path, baud)

    def exchange(self, data: bytes, wait: float, until: bytes = b"") -> bytes:
        return _exchange(self.fd, data, wait, until)

    def close(self):
        os.close(self.fd)


class _PySerialPort:
    """A serial port through pyserial (Windows; on Linux for testing with
    PROCYON_DETECT_PYSERIAL=1)."""
    def __init__(self, path: str, baud: int):
        import serial  # noqa: PLC0415 - only needed here
        s = serial.Serial()
        s.port, s.baudrate, s.timeout, s.write_timeout = path, baud, 0, 1
        # Lines low BEFORE opening: an interface that keys the transmitter from
        # RTS or DTR (e.g. a Digirig set up that way) must never be keyed.
        s.rts, s.dtr = False, False
        s.open()
        self.s = s

    def exchange(self, data: bytes, wait: float, until: bytes = b"") -> bytes:
        import time  # noqa: PLC0415
        self.s.reset_input_buffer()
        self.s.write(data)
        end, buf = time.time() + wait, b""
        while time.time() < end:
            chunk = self.s.read(256)
            if chunk:
                buf += chunk
                if until and until in buf:
                    break
            else:
                time.sleep(0.02)
        return buf

    def close(self):
        self.s.close()


def _use_pyserial() -> bool:
    return IS_WINDOWS or termios is None or os.environ.get("PROCYON_DETECT_PYSERIAL") == "1"


def _port(path: str, baud: int):
    return _PySerialPort(path, baud) if _use_pyserial() else _PosixPort(path, baud)


def _open(path: str, baud: int):
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        spd = getattr(termios, f"B{baud}")
        attrs = termios.tcgetattr(fd)
        cc = list(attrs[6])
        cc[termios.VMIN], cc[termios.VTIME] = 0, 0
        attrs = [0, 0, termios.CS8 | termios.CREAD | termios.CLOCAL, 0, spd, spd, cc]   # raw 8N1
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        # Opening a port raises RTS/DTR; drop them straight away so an interface
        # that keys the transmitter from those lines is released.
        fcntl.ioctl(fd, termios.TIOCMBIC, struct.pack("I", termios.TIOCM_RTS | termios.TIOCM_DTR))
    except Exception:
        os.close(fd)
        raise
    return fd


def _exchange(fd: int, data: bytes, wait: float, until: bytes = b"") -> bytes:
    import time
    termios.tcflush(fd, termios.TCIOFLUSH)
    os.write(fd, data)
    end, buf = time.time() + wait, b""
    while time.time() < end:
        if select.select([fd], [], [], 0.05)[0]:
            try:
                buf += os.read(fd, 256)
            except BlockingIOError:
                continue
            if until and until in buf:
                break
    return buf


def _identify(path: str, baud: int):
    """What radio answers on this port at this speed? None if nothing sensible."""
    try:
        port = _port(path, baud)
    except Exception as e:  # noqa: BLE001 - busy, missing, unsupported speed: not this one
        log.info("detect: can't open %s at %d: %s", path, baud, e)
        return None
    try:
        # Yaesu, Kenwood, Elecraft
        m = re.search(rb"ID(\d{3,4});", port.exchange(b"ID;", 0.5, b";"))
        if m:
            code = m.group(1).decode()
            if len(code) == 4 and code in YAESU:
                model, label = YAESU[code]
                return {"make": "Yaesu", "name": label, "model": _model("Yaesu", model)}
            if len(code) == 3 and code in KENWOOD:
                model, label = KENWOOD[code]
                return {"make": "Kenwood", "name": label, "model": _model("Kenwood", model)}
            if code == "017":
                # They all answer ID017: offer the short list to pick from
                choices = [m for m in (_model("Elecraft", n) for n in ELECRAFT) if m]
                return {"make": "Elecraft", "name": "K3 / KX3 / KX2 / K4", "model": "", "choices": choices,
                        "note": "An Elecraft radio answered - pick which one."}
            return {"make": "A radio", "name": f"(ID {code})", "model": "",
                    "note": f"A radio answered (ID {code}) but I don't know which. Pick it from the Radio list."}
        # Icom CI-V: ask every common address for its frequency in one go
        frames = b"".join(bytes([0xFE, 0xFE, a, 0xE0, 0x03, 0xFD]) for a in ICOM)
        reply = port.exchange(frames, 0.8)
        for a, (model, label) in ICOM.items():
            if bytes([0xFE, 0xFE, 0xE0, a, 0x03]) in reply:
                return {"make": "Icom", "name": label, "model": _model("Icom", model)}
    except Exception as e:  # noqa: BLE001 - a port that misbehaves just isn't a radio
        log.info("detect: %s at %d: %s", path, baud, e)
    finally:
        port.close()
    return None


def detect_radios():
    """[{path, baud, make, name, model, ptt, note?}] for every radio found, or
    None if this computer can't probe serial ports (Windows without pyserial)."""
    if _use_pyserial():
        try:
            import serial  # noqa: F401, PLC0415
        except ImportError:
            return None
    found = []
    for p in serial_ports():
        path = p["path"]
        # USB serial adapters only; skip Bluetooth, debug consoles, modems. On
        # Windows only COM ports found under USB devices have a description.
        if IS_WINDOWS:
            if " - " not in p["desc"]:
                continue
        elif not re.search(r"usb|ttyACM|ttyUSB", path, re.I):
            continue
        hit = None
        for baud in dict.fromkeys(ASCII_BAUDS + CIV_BAUDS):
            hit = _identify(path, baud)
            if hit:
                hit.update(path=path, baud=baud, ptt="cat")
                break
        if hit:
            log.info("detect: %s on %s at %d", hit["name"], path, hit["baud"])
            found.append(hit)
    return found


def blocked_ports() -> str:
    """After a search that found nothing: the plain-words reason if Linux wouldn't
    let us open the USB serial ports at all ('' otherwise)."""
    for p in serial_ports():
        if re.search(r"usb|ttyACM|ttyUSB", p["path"], re.I):
            why = serial_access_problem(p["path"])
            if why:
                return why
    return ""


def test_rig(model: str, device: str, baud: str = "") -> dict:
    """Read frequency, mode and PTT state once through Hamlib. Never keys the radio."""
    exe = find_tool("rigctl", "rigctl-wsjtx")
    if not exe:
        return {"ok": False, "error": "Hamlib (part of WSJT-X) wasn't found."}
    args = [exe, "-m", str(model)]
    if device:
        args += ["-r", device]
    if baud:
        args += ["-s", str(baud)]
    try:
        r = subprocess.run(args + ["f", "m", "t"], capture_output=True, text=True, timeout=25,
                           **NO_WINDOW)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "The radio didn't answer. Check the model, serial port and baud "
                                      "(on a Yaesu: the radio's CAT RATE menu must match)."}
    lines = r.stdout.split("\n")
    if r.returncode == 0 and len(lines) >= 3 and lines[0].isdigit():
        return {"ok": True, "freq": int(lines[0]), "mode": lines[1], "ptt": lines[3] if len(lines) > 3 else ""}
    err = (r.stderr or r.stdout).strip().splitlines()
    detail = (err[-1] if err else "").strip()
    # Hamlib's wording can mislead (a wrong baud rate shows up as "No such file"),
    # so explain in plain words what's most likely, and keep its detail for reference.
    if device and not os.path.exists(device):
        msg = "That serial port doesn't exist - is the radio plugged in and switched on?"
    elif serial_access_problem(device):
        return {"ok": False, "error": serial_access_problem(device)}
    elif re.search(r"busy|Permission denied", detail, re.I):
        msg = "The serial port is in use by another program (close WSJT-X or other radio software)."
    else:
        msg = ("The radio didn't answer - check the model, serial port and baud rate "
               "(the baud must match the radio's own CAT/RS232 setting).")
    return {"ok": False, "error": msg + (f" (Hamlib: {detail[:100]})" if detail else "")}

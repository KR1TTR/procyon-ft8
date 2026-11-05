"""Setup helpers: the list of radios Hamlib supports, serial ports, and
importing settings from an existing WSJT-X installation."""

import configparser
import os
import re
import subprocess
import sys

from .system import NO_WINDOW, find_tool, RIGCTL


_rigs: list = []


def hamlib_rigs():
    """[{model, mfg, name}] for every radio `rigctl -l` knows. Kept once found;
    an empty answer (no WSJT-X yet) is asked again next time."""
    global _rigs
    if not _rigs:
        _rigs = _read_rigs()
    return _rigs


hamlib_rigs.cache_clear = lambda: globals().update(_rigs=[])   # after installing WSJT-X


def _read_rigs():
    exe = find_tool(*RIGCTL)
    if not exe:
        return []
    lines = subprocess.run([exe, "-l"], capture_output=True, text=True, timeout=10, **NO_WINDOW).stdout.splitlines()
    if not lines:
        return []
    # Read by column position: some model names run into the version column.
    head = lines[0]
    c_mfg, c_model, c_ver = head.index("Mfg"), head.index("Model"), head.index("Version")
    rigs = []
    for line in lines[1:]:
        num = line[:c_mfg].strip()
        if num.isdigit():
            rigs.append({"model": int(num), "mfg": line[c_mfg:c_model].strip(),
                         "name": line[c_model:c_ver].strip()})
    return rigs


def find_rig(text: str):
    """Match a WSJT-X rig name such as 'Elecraft KX3' to a Hamlib model."""
    want = re.sub(r"\s+", " ", text or "").strip().lower()
    for r in hamlib_rigs():
        if f"{r['mfg']} {r['name']}".lower() == want:
            return r
    return None


def wsjtx_ini_path():
    home = os.path.expanduser("~")
    if sys.platform.startswith("win"):
        cands = [os.path.join(os.environ.get("LOCALAPPDATA", ""), "WSJT-X", "WSJT-X.ini")]
    elif sys.platform == "darwin":
        cands = [os.path.join(home, "Library", "Preferences", "WSJT-X.ini")]
    else:
        cands = [os.path.join(home, ".config", "WSJT-X.ini")]
    return next((p for p in cands if os.path.exists(p)), None)


def _variant(v: str) -> str:
    """WSJT-X stores some values as Qt variants: pull out the readable token."""
    m = re.findall(r"[A-Za-z_]{4,}", v or "")
    return m[-1] if m else (v or "")


def import_wsjtx():
    """Read WSJT-X's settings and translate them into Procyon settings.
    Returns (settings, notes) - notes explain anything that needs a look."""
    path = wsjtx_ini_path()
    if not path:
        return None, ["No WSJT-X settings found on this computer."]
    cp = configparser.RawConfigParser(strict=False)
    cp.optionxform = str
    cp.read(path, encoding="utf-8")
    c = cp["Configuration"] if cp.has_section("Configuration") else {}
    get = lambda k: (c.get(k, "") or "").strip()  # noqa: E731
    s, notes = {}, []

    if get("MyCall"):
        s["mycall"] = get("MyCall").upper()
    if get("MyGrid"):
        g = get("MyGrid")
        s["mygrid"] = g[:4].upper() + g[4:].lower()

    rig = get("Rig")
    ptt = _variant(get("PTTMethod"))
    if rig and rig.lower() != "none":
        s["rig_enabled"] = True
        if rig.lower().startswith("hamlib net rigctl"):
            host, _, port = get("CATNetworkPort").partition(":")
            s.update(rig_spawn=False, rig_host=host or "127.0.0.1", rig_port=int(port or 4532))
            notes.append("WSJT-X used a separate rigctld; Procyon will connect to the same one.")
        else:
            r = find_rig(rig)
            if r:
                s.update(rig_spawn=True, rig_model=str(r["model"]), rig_host="127.0.0.1", rig_port=4532)
            else:
                notes.append(f"Couldn't match the rig “{rig}” - please pick it from the list.")
            if get("CATSerialPort"):
                s["rig_device"] = get("CATSerialPort")
            if get("CATSerialRate"):
                s["rig_baud"] = get("CATSerialRate")
    else:
        s["rig_enabled"] = False
        notes.append("WSJT-X had no radio control set up.")

    if "VOX" in ptt:
        s["ptt"] = "vox"
    elif "RTS" in ptt:
        s["ptt"] = "rts"
    elif "DTR" in ptt:
        s["ptt"] = "dtr"
    else:
        s["ptt"] = "cat"
    if s["ptt"] in ("rts", "dtr") and get("PTTport") and get("PTTport") not in (get("CATSerialPort"), "CAT"):
        notes.append(f"WSJT-X keyed the radio on a separate port ({get('PTTport')}); "
                     "Procyon uses the CAT serial port for that.")

    # Stored like "...data_mode_data\0)" with part of the name escaped: search for it.
    found = re.findall(r"mode_(data|usb|none)", get("DataMode").lower())
    if found:
        s["rig_mode"] = found[-1]

    for key, ini in (("audio_in", "SoundInName"), ("audio_out", "SoundOutName")):
        if get(ini):
            s[key] = get(ini)
    watts = re.match(r"^\s*(\d+(?:\.\d+)?)", cp.get("LogQSO", "TxPower", fallback="") or get("TxPower"))
    if watts:
        s["tx_watts"] = watts.group(1)
    if get("UDPServer"):
        s.update(udp_enabled=True, udp_host=get("UDPServer"),
                 udp_port=int(get("UDPServerPort") or 2237),
                 udp_accept=get("AcceptUDPRequests").lower() == "true")
    dial = cp.get("Common", "DialFreq", fallback="") or get("DialFreq")
    if dial.isdigit():
        s["dial_hz"] = int(dial)
    return s, notes

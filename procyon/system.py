"""Where things live on each operating system: WSJT-X's tools, cty.dat,
Procyon's data folder, and serial ports."""

import glob
import os
import re
import shutil
import subprocess
import sys
import time

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
EXE = ".exe" if IS_WINDOWS else ""
# Procyon runs without a console window on Windows, so every program it starts
# (jt9, ft8code, rigctld...) would open a black window of its own - this stops
# that. Pass as **NO_WINDOW to subprocess.run / asyncio.create_subprocess_exec.
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW, "stdin": subprocess.DEVNULL} if IS_WINDOWS else {}


def data_dir() -> str:
    if IS_WINDOWS:
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Procyon")
    if IS_MAC:
        return os.path.expanduser("~/Library/Application Support/Procyon")
    return os.path.expanduser("~/.local/share/procyon")


def wsjtx_dirs() -> list[str]:
    """Folders where WSJT-X (or a close relative) keeps its programs."""
    dirs = []
    extra = os.environ.get("PROCYON_WSJTX")          # manual override
    if extra:
        dirs += [extra, os.path.join(extra, "bin")]
    if IS_WINDOWS:
        roots = [r"C:\WSJT", os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")]
        for root in roots:
            for name in ("wsjtx", "wsjtx-improved", "WSJT-X", "jtdx", "JTDX"):
                dirs.append(os.path.join(root, name, "bin"))
            dirs += glob.glob(os.path.join(root, "wsjtx*", "bin"))
    elif IS_MAC:
        # WSJT-X and its variants. Only the Applications folders are looked at
        # directly: poking at Desktop or Downloads would make macOS ask the user for
        # permission. Anywhere else, Spotlight finds it without asking.
        dirs += _mac_app_dirs()
        if not any(os.path.isfile(os.path.join(d, "jt9")) for d in dirs):
            dirs += _spotlight_wsjtx()
    return [d for d in dict.fromkeys(dirs) if d and os.path.isdir(d)]


MAC_APP_ROOTS = ("/Applications", "~/Applications")


def _mac_app_dirs() -> list[str]:
    """Programs folders (Contents/MacOS) of apps in Applications - straight in it
    or one folder down - that hold WSJT-X's decoder jt9, whatever their name
    (WSJT-X Improved is "ws.app"). WSJT-X, WSJT-X Improved and JTDX first."""
    found = []
    for root in MAC_APP_ROOTS:
        root = os.path.expanduser(root)
        for pattern in ("*.app", os.path.join("*", "*.app")):
            for app in glob.glob(os.path.join(root, pattern)):
                d = os.path.join(app, "Contents", "MacOS")
                if os.path.isfile(os.path.join(d, "jt9")):
                    found.append(d)
    known = lambda d: re.match(r"(wsjt|jtdx|ws\.app$)", os.path.basename(d[:-len("/Contents/MacOS")]), re.I)
    return sorted(found, key=lambda d: (not known(d), d.lower()))


_spot: tuple[float, list[str]] = (0.0, [])


def spotlight(*args: str) -> list[str]:
    """Paths Spotlight knows for `mdfind` arguments (macOS). Asking never triggers a permission prompt."""
    try:
        out = subprocess.run(["mdfind", *args], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line for line in out.splitlines() if line]


def _spotlight_wsjtx() -> list[str]:
    """wsjt*.app / ws.app / jtdx*.app folders anywhere on this Mac, remembered for a minute."""
    global _spot
    if time.time() - _spot[0] > 60:
        apps = spotlight("kMDItemContentType == 'com.apple.application-bundle' && "
                         "(kMDItemFSName == 'wsjt*.app'c || kMDItemFSName == 'ws.app'c || kMDItemFSName == 'jtdx*.app'c)")
        _spot = (time.time(), [os.path.join(a, "Contents", "MacOS") for a in apps])
    return _spot[1]


# Hamlib's programs under every name they come with: plain (Hamlib itself),
# "-wsjtx" (WSJT-X on Windows and Mac), "-ws" (WSJT-X Improved's ws.app on Mac).
RIGCTL = ("rigctl", "rigctl-wsjtx", "rigctl-ws")
RIGCTLD = ("rigctld", "rigctld-wsjtx", "rigctld-ws")


def find_tool(*names: str) -> str | None:
    """Find a program by any of its names: on the PATH first, then in the
    WSJT-X installation (which bundles jt9, ft8code and Hamlib's rigctld)."""
    for name in names:
        hit = shutil.which(name)
        if hit:
            return hit
    for d in wsjtx_dirs():
        for name in names:
            path = os.path.join(d, name + EXE)
            if os.path.isfile(path):
                return path
    return None


def tools() -> dict:
    return {
        "jt9": find_tool("jt9"),
        "ft8code": find_tool("ft8code"),
        "rigctld": find_tool(*RIGCTLD),
        "rigctl": find_tool(*RIGCTL),
    }


CTY_VERSION_RE = re.compile(rb"=VER(\d{8})")
BUNDLED_CTY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cty", "cty.dat")


def cty_version(path: str) -> str:
    """The release date AD1C writes into a cty.dat (as the pseudo-callsign
    '=VERyyyymmdd'), as 'yyyy-mm-dd' - or '' if the file has none."""
    try:
        with open(path, "rb") as f:
            m = CTY_VERSION_RE.search(f.read())
    except OSError:
        return ""
    if not m:
        return ""
    d = m.group(1).decode()
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def find_cty(own_dir: str | None = None) -> str | None:
    """The country file: the NEWEST of Procyon's own downloaded copy (in
    own_dir, from Settings → Advanced → Update country data) and the ones
    WSJT-X and friends install, plus the copy shipped inside Procyon
    (BUNDLED_CTY). On a tie the downloaded copy wins, then the shipped one."""
    home = os.path.expanduser("~")
    cands = [os.path.join(own_dir, "cty.dat")] if own_dir else []
    cands.append(BUNDLED_CTY)
    if IS_WINDOWS:
        cands.append(os.path.join(os.environ.get("LOCALAPPDATA", ""), "WSJT-X", "cty.dat"))
        for d in wsjtx_dirs():
            cands += [os.path.join(d, "cty.dat"), os.path.join(d, "..", "share", "wsjtx", "cty.dat")]
    elif IS_MAC:
        cands.append(os.path.join(home, "Library", "Application Support", "WSJT-X", "cty.dat"))
        for d in wsjtx_dirs():
            cands += [os.path.join(d, "..", "Resources", "wsjtx", "cty.dat"),
                      os.path.join(d, "..", "Resources", "cty.dat")]
    else:
        cands += [os.path.join(home, ".local", "share", "WSJT-X", "cty.dat"),
                  "/usr/share/wsjtx/cty.dat", "/usr/local/share/wsjtx/cty.dat",
                  "/usr/share/hamradio-files/cty.dat", "/usr/share/jtdx/cty.dat"]
    found = [os.path.normpath(p) for p in cands if p and os.path.isfile(p)]
    if not found:
        return None
    # max() keeps the first of equals, so the list order breaks ties.
    return max(found, key=cty_version)


def serial_access_problem(path: str) -> str:
    """Why this computer won't let Procyon open a serial port, in plain words
    ('' if it's allowed, or we can't tell). Linux only: there the port belongs
    to a group ('dialout', or 'uucp' on Arch) and you must be a member."""
    if IS_WINDOWS or IS_MAC or not path or not os.path.exists(path):
        return ""
    if os.access(path, os.R_OK | os.W_OK):
        return ""
    import grp  # noqa: PLC0415 - not on Windows
    import pwd  # noqa: PLC0415
    try:
        gid = os.stat(path).st_gid
        group = grp.getgrgid(gid).gr_name
        me = pwd.getpwuid(os.getuid())
        members = grp.getgrnam(group).gr_mem
    except (KeyError, OSError):
        return f"This computer won't let Procyon use the serial port {path} (permission denied)."
    if me.pw_gid == gid:   # in the group, so it's the port's own settings
        return f"This computer won't let Procyon use the serial port {path} (permission denied)."
    if me.pw_name in members:   # added already, but this login doesn't have it yet
        return (f"You're in the '{group}' group now, but that only takes effect after you "
                "log out and back in (or restart the computer). Then Procyon can use the radio's serial port.")
    return (f"This computer won't let Procyon use the serial port {path}: your user isn't in the "
            f"'{group}' group. Open a terminal, run:  sudo usermod -aG {group} $USER  "
            "- then log out and back in (or restart the computer).")


def serial_ports() -> list[dict]:
    """[{path, desc}] - USB serial adapters get a readable description."""
    if IS_WINDOWS:
        return _windows_ports()
    ports, seen = [], set()
    for link in sorted(glob.glob("/dev/serial/by-id/*")):
        path = os.path.realpath(link)
        name = os.path.basename(link)
        desc = re.sub(r"^usb-|-if\d+.*$|_[0-9a-f]{16,}", "", name).replace("_", " ")
        if "CP210" in name:
            desc += " (e.g. Digirig)"
        ports.append({"path": path, "desc": f"{path} - {desc}"})
        seen.add(path)
    pattern = ["/dev/cu.*"] if IS_MAC else ["/dev/ttyUSB*", "/dev/ttyACM*"]
    for path in sorted(sum((glob.glob(p) for p in pattern), [])):
        if IS_MAC and ("Bluetooth" in path or "debug-console" in path):
            continue   # never a radio interface
        if path not in seen:
            ports.append({"path": path, "desc": path})
    return ports


def _windows_ports() -> list[dict]:
    """COM ports from the registry, with friendly names where Windows has them."""
    import winreg  # noqa: PLC0415 - Windows only
    ports = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM") as key:
            i = 0
            while True:
                try:
                    _, port, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                ports.append(port)
                i += 1
    except OSError:
        pass
    names = _windows_port_names()
    out = []
    for port in sorted(set(ports), key=lambda p: int(re.sub(r"\D", "", p) or 0)):
        desc = names.get(port, "")
        if "CP210" in desc:
            desc += " (e.g. Digirig)"
        out.append({"path": port, "desc": f"{port} - {desc}" if desc else port})
    return out


def _windows_port_names() -> dict:
    """COMx -> 'Silicon Labs CP210x USB to UART Bridge' etc."""
    import winreg  # noqa: PLC0415
    names = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum") as enum:
            for bus in ("USB", "FTDIBUS"):
                try:
                    _walk_ports(winreg, enum, bus, names)
                except OSError:
                    pass
    except OSError:
        pass
    return names


def _walk_ports(winreg, parent, path, names, depth=0):
    with winreg.OpenKey(parent, path) as key:
        try:
            friendly = winreg.QueryValueEx(key, "FriendlyName")[0]
            m = re.search(r"\((COM\d+)\)", friendly)
            if m:
                names[m.group(1)] = friendly.replace(f" ({m.group(1)})", "")
        except OSError:
            pass
        if depth < 2:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(key, i)
                except OSError:
                    break
                _walk_ports(winreg, key, sub, names, depth + 1)
                i += 1

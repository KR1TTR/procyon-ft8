"""Install WSJT-X for the user when Procyon can't find it (macOS).

Procyon needs WSJT-X's decoder and encoder. This picks the right build for the
computer's CPU (Apple Silicon or Intel), uses a WSJT-X disk image already on
the Mac if Spotlight finds one, otherwise downloads the official release from
SourceForge, and puts wsjtx.app in ~/Applications. No administrator password is
needed. Because WSJT-X's Mac builds aren't signed by Apple, it clears macOS's
download quarantine flag on the installed app and tells the user it did so.
On Linux and Windows, Procyon tells the user how to install it instead."""

import glob
import logging
import os
import platform
import re
import shutil
import ssl
import subprocess
import tempfile
import urllib.request

from .system import IS_MAC, find_tool, spotlight

log = logging.getLogger("procyon.wsjtx_install")

SF = "https://downloads.sourceforge.net/project/wsjt/"
RSS = "https://sourceforge.net/projects/wsjt/rss?path=/"
# Used only if SourceForge's file list can't be read
FALLBACK = {"arm64": "wsjtx-3.0.0/wsjtx-3.0.0-ARM-Darwin.dmg", "x86_64": "wsjtx-3.0.0/wsjtx-3.0.0-Darwin.dmg"}
DEST = os.path.expanduser("~/Applications")


class InstallError(Exception):
    pass


def _ctx() -> ssl.SSLContext:
    """python.org's Python ships with no CA certificates; certifi (bundled) supplies them."""
    try:
        import certifi  # noqa: PLC0415
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def available() -> bool:
    return IS_MAC


def _machine() -> str:
    """The real CPU, even when Python itself is running under Rosetta."""
    try:
        if subprocess.run(["sysctl", "-n", "hw.optional.arm64"], capture_output=True,
                          text=True).stdout.strip() == "1":
            return "arm64"
    except OSError:
        pass
    return platform.machine()


def _local_dmgs(arch: str) -> list[str]:
    """WSJT-X disk images already in Downloads, best match for this CPU first."""
    found = [p for p in spotlight("-name", "wsjtx")
             if re.fullmatch(r"wsjtx.*\.dmg", os.path.basename(p), re.I)]
    arm = lambda p: bool(re.search(r"arm", os.path.basename(p), re.I))  # noqa: E731
    return sorted(found, key=lambda p: (arm(p) != (arch == "arm64"), "rc" in p.lower(), p))


def _remote(arch: str) -> tuple[str, str]:
    """(download URL, file name) of the newest release for this CPU."""
    want_arm = arch == "arm64"
    best = None
    try:
        req = urllib.request.Request(RSS, headers={"User-Agent": "Procyon-FT8"})
        text = urllib.request.urlopen(req, timeout=15, context=_ctx()).read().decode("utf-8", "replace")
        for path in set(re.findall(r"/(wsjtx-(\d+(?:\.\d+)*)/wsjtx-[\d.]+-(ARM-)?Darwin\.dmg)", text)):
            ver = tuple(int(x) for x in path[1].split("."))
            if bool(path[2]) == want_arm and (best is None or ver > best[0]):
                best = (ver, path[0])
    except Exception as e:  # noqa: BLE001
        log.info("couldn't read the WSJT-X file list: %s", e)
    rel = best[1] if best else FALLBACK.get(arch, FALLBACK["x86_64"])
    return SF + rel, os.path.basename(rel)


def _download(url: str, dest: str, progress):
    req = urllib.request.Request(url, headers={"User-Agent": "Procyon-FT8"})
    with urllib.request.urlopen(req, timeout=30, context=_ctx()) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            got += len(chunk)
            if total:
                progress(f"Downloading WSJT-X… {got * 100 // total}%", got / total)
    if os.path.getsize(dest) < 5_000_000:
        raise InstallError("The download was too small to be WSJT-X")


def _find_app(root: str) -> str | None:
    for app in glob.glob(os.path.join(root, "**", "*.app"), recursive=True):
        if os.path.isfile(os.path.join(app, "Contents", "MacOS", "jt9")):
            return app
    return None


def _app_from_dmg(dmg: str, work: str) -> str:
    """Mount the disk image and copy out the app (or unpack the .pkg inside it)."""
    mnt = os.path.join(work, "mnt")
    os.makedirs(mnt)
    r = subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noverify", "-quiet",
                        "-mountpoint", mnt, dmg], capture_output=True, text=True)
    if r.returncode:
        raise InstallError("Couldn't open the WSJT-X disk image: " + (r.stderr.strip()[:120] or "unknown error"))
    try:
        stage = os.path.join(work, "stage")
        os.makedirs(stage)
        app = _find_app(mnt)
        if app:
            out = os.path.join(stage, "wsjtx.app")
            subprocess.run(["ditto", app, out], check=True)
            return out
        for pkg in glob.glob(os.path.join(mnt, "**", "*.pkg"), recursive=True):
            exp = os.path.join(work, "pkg")
            if subprocess.run(["pkgutil", "--expand-full", pkg, exp], capture_output=True).returncode == 0:
                app = _find_app(exp)
                if app:
                    out = os.path.join(stage, "wsjtx.app")
                    subprocess.run(["ditto", app, out], check=True)
                    return out
        raise InstallError("That disk image doesn't contain WSJT-X's decoder (jt9)")
    finally:
        subprocess.run(["hdiutil", "detach", "-quiet", "-force", mnt], capture_output=True)


def install(progress=lambda text, frac=None: None) -> str:
    """Find or download WSJT-X and install it. Returns the installed app's path.
    `progress(text, fraction)` is called along the way. Raises InstallError."""
    if not IS_MAC:
        raise InstallError("Automatic install is only for macOS")
    arch = _machine()
    work = tempfile.mkdtemp(prefix="procyon-wsjtx-")
    try:
        local = _local_dmgs(arch)
        if local:
            dmg = local[0]
            progress(f"Using {os.path.basename(dmg)} from your Downloads folder…", 0)
        else:
            progress("Finding the latest WSJT-X…", 0)
            url, name = _remote(arch)
            dmg = os.path.join(work, name)
            log.info("downloading %s", url)
            _download(url, dmg, progress)
        progress("Installing WSJT-X…", 1)
        app = _app_from_dmg(dmg, work)
        os.makedirs(DEST, exist_ok=True)
        target = os.path.join(DEST, "wsjtx.app")
        if os.path.exists(target):       # keep the old one rather than delete it
            n = 1
            while os.path.exists(f"{target}.old{n}"):
                n += 1
            os.rename(target, f"{target}.old{n}")
        shutil.move(app, target)
        # WSJT-X's Mac builds aren't signed by Apple, so macOS would refuse to run its
        # decoder for Procyon. Clear the download "quarantine" flag - and say so.
        progress("Letting macOS run WSJT-X (its Mac builds aren't signed by Apple, "
                 "so the download check is switched off for it)…", 1)
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", target], capture_output=True)
        if not find_tool("jt9") or not find_tool("ft8code"):
            raise InstallError("WSJT-X installed but its decoder still wasn't found")
        return target
    except (OSError, subprocess.CalledProcessError) as e:
        raise InstallError(f"Couldn't install WSJT-X: {e}") from e
    finally:
        shutil.rmtree(work, ignore_errors=True)

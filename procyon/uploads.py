"""Upload QSOs to online logbooks: QRZ.com (with the user's Logbook API key)
and ARRL's LoTW (by handing a file to TQSL, which signs and uploads it).

Uploads happen only when the user clicks the button, so nothing clashes with
GridTracker and a portable station can send everything once it's back online.
Each QSO remembers which services have it, in the standard ADIF fields below,
so a QSO that couldn't be sent simply stays queued for the next click."""

import asyncio
import glob
import os
import shutil
import tempfile
import urllib.parse

import aiohttp

from . import __version__
from .system import IS_MAC, IS_WINDOWS

SERVICES = {
    # service: (name shown to the user, "sent" field, date field)
    "qrz": ("QRZ", "qrzcom_qso_upload_status", "qrzcom_qso_upload_date"),
    "lotw": ("LoTW", "lotw_qsl_sent", "lotw_qslsdate"),
}
TRACKING = {f for _, sent, date in SERVICES.values() for f in (sent, date)}

QRZ_URL = os.environ.get("PROCYON_QRZ_URL", "https://logbook.qrz.com/api")
USER_AGENT = f"Procyon/{__version__} (FT8; github.com/KR1TTR/procyon-ft8)"


def is_sent(q: dict, service: str) -> bool:
    return q.get(SERVICES[service][1], "").upper() == "Y"


def for_upload(q: dict) -> dict:
    """The QSO as the services should see it: without our own bookkeeping."""
    return {k: v for k, v in q.items() if k not in TRACKING}


class UploadError(Exception):
    """Something the user can act on, already in plain language."""


# ------------------------------------------------------------------ QRZ

def _qrz_reply(text: str) -> dict:
    return {k.upper(): v for k, v in urllib.parse.parse_qsl(text.strip(), keep_blank_values=True)}


async def _qrz(session, key: str, **fields) -> dict:
    try:
        async with session.post(QRZ_URL, data={"KEY": key, **fields},
                                timeout=aiohttp.ClientTimeout(total=30)) as r:
            if r.status != 200:
                raise UploadError(f"QRZ answered with an error (HTTP {r.status}) - try again later")
            return _qrz_reply(await r.text())
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
        raise UploadError("Couldn't reach QRZ - check the internet connection") from e


def _qrz_auth_problem(rep: dict) -> str:
    why = rep.get("REASON", "").strip()
    return ("QRZ didn't accept the API key - check it in Settings → Online logbooks"
            + (f" (QRZ says: {why})" if why else ""))


async def qrz_check(key: str) -> str:
    """Ask QRZ about the key. Returns a plain-language description of the logbook."""
    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as s:
        rep = await _qrz(s, key, ACTION="STATUS")
    if rep.get("RESULT") != "OK":
        raise UploadError(_qrz_auth_problem(rep))
    call = rep.get("CALLSIGN", "").upper()
    count = rep.get("COUNT", "")
    return (f"The key works: QRZ logbook for {call or 'your call'}"
            + (f", {count} QSOs in it" if count else ""))


async def qrz_upload(key: str, qsos: list[dict], record) -> tuple[list[int], str, int]:
    """Send QSOs one at a time (QRZ takes one per request). Returns the
    positions in `qsos` that QRZ now has (new or already there) and a problem
    to report if the upload stopped early, and how many QRZ already had."""
    done, dups = [], 0
    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as s:
        for i, q in enumerate(qsos):
            try:
                rep = await _qrz(s, key, ACTION="INSERT", ADIF=record(for_upload(q)))
            except UploadError as e:
                return done, str(e), dups
            result, why = rep.get("RESULT", ""), rep.get("REASON", "")
            if result in ("OK", "REPLACE") or "duplicate" in why.lower():
                done.append(i)
                dups += result not in ("OK", "REPLACE")
            elif result == "AUTH" or "invalid api key" in why.lower():
                return done, _qrz_auth_problem(rep), dups
            else:
                return done, f"QRZ wouldn't take the QSO with {q.get('call', '?')}: {why or 'no reason given'}", dups
    return done, "", dups


# ----------------------------------------------------------------- LoTW

def find_tqsl() -> str | None:
    """TQSL, ARRL's program that signs and uploads QSOs to LoTW."""
    override = os.environ.get("PROCYON_TQSL")
    if override:
        return override if os.path.isfile(override) else None
    hit = shutil.which("tqsl")
    if hit:
        return hit
    if IS_WINDOWS:
        for root in (os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     os.environ.get("ProgramFiles", r"C:\Program Files")):
            path = os.path.join(root, "TrustedQSL", "tqsl.exe")
            if os.path.isfile(path):
                return path
    elif IS_MAC:
        for root in ("/Applications", os.path.expanduser("~/Applications")):
            for path in glob.glob(os.path.join(root, "*", "tqsl.app", "Contents", "MacOS", "tqsl")) + \
                    glob.glob(os.path.join(root, "tqsl.app", "Contents", "MacOS", "tqsl")):
                return path
    return None


# TQSL's exit codes (its command-line documentation)
TQSL_EXIT = {
    1: "the upload was cancelled",
    2: "LoTW rejected the upload",
    3: "LoTW gave an unexpected answer - try again later",
    4: "TQSL had a problem signing the QSOs (is your callsign certificate set up?)",
    5: "TQSL had a problem signing the QSOs (is your callsign certificate set up?)",
    6: "TQSL couldn't read the QSO file",
    7: "TQSL couldn't write its output file",
    10: "TQSL didn't understand the request (please report this)",
    11: "couldn't reach LoTW - check the internet connection",
}
TQSL_OK = (0, 8, 9)    # 8: all were already uploaded, 9: some were


async def lotw_upload(tqsl: str, location: str, adif_text: str) -> str:
    """Sign and upload with TQSL. Returns a note for the user; raises UploadError."""
    fd, path = tempfile.mkstemp(prefix="procyon-lotw-", suffix=".adi")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(adif_text)
        try:
            proc = await asyncio.create_subprocess_exec(
                tqsl, "-x", "-d", "-u", "-a", "compliant", "-l", location, path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError as e:
            proc.kill()
            raise UploadError("TQSL didn't finish within 5 minutes - is it waiting for you in a window?") from e
        except OSError as e:
            raise UploadError(f"Couldn't start TQSL ({e})") from e
        text = out.decode(errors="replace")
        if proc.returncode in TQSL_OK:
            return {0: "", 8: "LoTW already had all of them", 9: "LoTW already had some of them"}[proc.returncode]
        if "location" in text.lower() and ("not found" in text.lower() or "invalid" in text.lower()):
            raise UploadError(f'TQSL has no station location called "{location}" - check the name '
                              f"in Settings → Online logbooks (it must match TQSL exactly)")
        why = TQSL_EXIT.get(proc.returncode, f"TQSL stopped with error {proc.returncode}")
        last = text.strip().splitlines()[-1:] if text.strip() else []
        raise UploadError(f"LoTW upload failed: {why}" + (f" ({last[0][:160]})" if last else ""))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

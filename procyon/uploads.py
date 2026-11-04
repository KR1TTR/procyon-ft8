"""Upload QSOs to online logbooks: QRZ.com (with the user's Logbook API key)
and ARRL's LoTW (by handing a file to TQSL, which signs and uploads it).

Uploads happen only when the user clicks the button, so nothing clashes with
GridTracker and a portable station can send everything once it's back online.
Each QSO remembers which services have it, in the standard ADIF fields below,
so a QSO that couldn't be sent simply stays queued for the next click."""

import asyncio
import glob
import html
import os
import re
import shutil
import tempfile
import urllib.parse

import aiohttp

from . import __version__
from .system import IS_MAC, IS_WINDOWS, NO_WINDOW

SERVICES = {
    # service: (name shown to the user, "sent" field, date field)
    "qrz": ("QRZ", "qrzcom_qso_upload_status", "qrzcom_qso_upload_date"),
    # LoTW upload switched off in 0.9.60 (user's choice: ARRL's certificate
    # process doesn't fit Procyon's easy setup). The code below (find_tqsl,
    # lotw_upload) is kept; to bring it back, un-comment this line, the
    # LoTW parts marked "LoTW off" in engine.py, index.html and app.js.
    # "lotw": ("LoTW", "lotw_qsl_sent", "lotw_qslsdate"),
}
# "Export only new QSOs" (Settings → Advanced) remembers what was exported in an
# app-defined ADIF field, which other programs ignore.
EXPORTED_FIELD = "app_procyon_exported"
TRACKING = {f for _, sent, date in SERVICES.values() for f in (sent, date)} | {EXPORTED_FIELD}
TRACKING |= {"lotw_qsl_sent", "lotw_qslsdate"}   # still ours to strip while LoTW is off

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
    """QRZ answers name=value&name=value. A FETCH answer's ADIF comes as plain
    text with < > written &lt; &gt; (checked against the real QRZ, 2026-10-25),
    not URL-encoded - so it's cut out whole before the rest is split at "&"."""
    text = text.strip()
    adif = None
    i = text.find("ADIF=")
    if i >= 0 and (i == 0 or text[i - 1] == "&"):
        rest = text[i + 5:]
        m = re.search(r"&[A-Z_]+=", rest)      # the next field (entities like &lt; never match)
        adif = rest[:m.start()] if m else rest
        text = text[:i] + (rest[m.start() + 1:] if m else "")
        if "%3C" in adif.upper() and "<" not in adif and "&lt;" not in adif:
            adif = urllib.parse.unquote_plus(adif)   # in case QRZ ever URL-encodes it
    rep = {k.upper(): v for k, v in urllib.parse.parse_qsl(text.rstrip("&"), keep_blank_values=True)}
    if adif is not None:
        rep["ADIF"] = adif
    return rep


async def _qrz(session, key: str, timeout=30, **fields) -> dict:
    try:
        async with session.post(QRZ_URL, data={"KEY": key, **fields},
                                timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            if r.status != 200:
                raise UploadError(f"QRZ answered with an error (HTTP {r.status}) - try again later")
            return _qrz_reply(await r.text())
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
        raise UploadError("Couldn't reach QRZ - check the internet connection") from e


def _qrz_auth_problem(rep: dict) -> str:
    why = rep.get("REASON", "").strip()
    return ("QRZ didn't accept the API key - check it in Settings → Logbook"
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


FETCH_PAGE = 250   # QSOs per request, as QRZ recommends for big logbooks


async def qrz_fetch(key: str, parse, progress=None) -> tuple[list[dict], int | None]:
    """Every QSO in the user's QRZ logbook, 250 at a time (QRZ's paging:
    AFTERLOGID = the highest logid so far + 1), and the count QRZ reports.
    If the QSOs don't carry their logid, the whole logbook is asked for in
    one go instead. Downloading needs a QRZ subscription; uploading doesn't.
    `parse` turns ADIF text into QSOs; `progress(n, total)` after each page."""
    qsos, after, total, option = [], 0, None, None
    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as s:
        while True:
            rep = await _qrz(s, key, timeout=90 if option is None else 300, ACTION="FETCH",
                             OPTION=option or f"MAX:{FETCH_PAGE},AFTERLOGID:{after}")
            result, why = rep.get("RESULT", ""), rep.get("REASON", "")
            if result == "AUTH" or "invalid api key" in why.lower():
                raise UploadError(_qrz_auth_problem(rep))
            if result != "OK":
                if "subscri" in why.lower():
                    raise UploadError("Downloading your logbook from QRZ needs a QRZ subscription "
                                      "(uploading doesn't). Without one, export the logbook as an ADIF "
                                      "file on qrz.com and use \"Import an ADIF file\" instead.")
                if rep.get("COUNT") == "0" or re.search(r"\bno\b.*(record|entr|qso|found)|not found",
                                                       why, re.I) or (qsos and not why):
                    break       # an empty logbook, or nothing after the last page
                raise UploadError(f"QRZ wouldn't send the logbook: {why or 'no reason given'}")
            if total is None and rep.get("COUNT", "").isdigit():
                total = int(rep["COUNT"])
            text = rep.get("ADIF", "")
            if "&lt;" in text:   # QRZ sends the ADIF with < and > written as HTML
                text = html.unescape(text)
            page = parse(text)
            if option:            # the whole logbook in one go
                qsos = page
                break
            qsos += page
            if progress:
                progress(len(qsos), total)
            ids = [int(q["app_qrzlog_logid"]) for q in page if q.get("app_qrzlog_logid", "").isdigit()]
            if len(page) < FETCH_PAGE:
                break
            if not ids:           # can't page without logids: fetch everything at once
                option = "ALL"
                if progress:
                    progress(None, total)
                continue
            after = max(ids) + 1
    return qsos, total


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
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **NO_WINDOW)
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
                              f"in Settings → Logbook (it must match TQSL exactly)")
        why = TQSL_EXIT.get(proc.returncode, f"TQSL stopped with error {proc.returncode}")
        last = text.strip().splitlines()[-1:] if text.strip() else []
        raise UploadError(f"LoTW upload failed: {why}" + (f" ({last[0][:160]})" if last else ""))
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

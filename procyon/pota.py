"""Parks on the Air (POTA) activations.

While an activation is on, every QSO is tagged with the standard fields POTA
reads (MY_SIG=POTA, MY_SIG_INFO=<park>). Activating several parks at once (a
"2-fer", or more where parks meet) puts the same QSOs into one log file per
park, which is what pota.app expects - named CALL@PARK-YYYYMMDD.adi."""

import re

# K-1234 (older US style), US-1234, VE-0123, GB-0001, ...
PARK_RE = re.compile(r"^[A-Z0-9]{1,4}-\d{4,6}$")
NEEDED = 10          # QSOs for a valid activation, per park per UTC day


def parse_parks(text: str) -> tuple[list[str], list[str]]:
    """Split "us-1234, US-5678 us-1234" into (["US-1234", "US-5678"], bad entries)."""
    good, bad = [], []
    for tok in re.split(r"[\s,;]+", (text or "").upper()):
        if not tok:
            continue
        if PARK_RE.match(tok):
            if tok not in good:
                good.append(tok)
        else:
            bad.append(tok)
    return good, bad


def _when(q: dict) -> str:
    return q.get("qso_date", "") + q.get("time_on", "").ljust(6, "0")


def activation_qsos(qsos: list[dict], park: str, start: str, end: str = "") -> list[dict]:
    """QSOs logged at `park` between `start` and `end` (YYYYMMDDHHMMSS, UTC)."""
    out = []
    for q in qsos:
        parks = [p.strip() for p in q.get("my_sig_info", "").upper().split(",")]
        if q.get("my_sig", "").upper() == "POTA" and park in parks \
                and _when(q) >= start and (not end or _when(q) <= end):
            out.append(q)
    return out


def count_on(qsos: list[dict], date: str) -> int:
    """QSOs that count on one UTC day: the same station twice on a band is a dupe."""
    return len({(q.get("call", "").upper(), q.get("band", "").lower())
                for q in qsos if q.get("qso_date") == date})


def export(qsos: list[dict], park: str, record, header: str) -> str:
    """ADIF for one park: the activation's QSOs with only that park in them."""
    recs = []
    for q in qsos:
        r = dict(q)
        r["my_sig"], r["my_sig_info"], r["my_pota_ref"] = "POTA", park, park
        recs.append(record(r))
    return header + "".join(recs)


def _key(q: dict) -> tuple:
    return (q.get("call", "").upper(), q.get("qso_date", ""), q.get("time_on", ""), q.get("band", "").lower())


def merge(saved: list[dict], current: list[dict], drop=()) -> list[dict]:
    """A park's saved log plus the current activation's QSOs. Keeps QSOs from an
    earlier activation of the same park that day; a QSO in both is taken from
    `current` (it may have been edited since). QSOs in `drop` (deleted from the
    main log) are left out."""
    drop = {_key(q) for q in drop}
    by_key = {_key(q): q for q in saved if _key(q) not in drop}
    by_key.update({_key(q): q for q in current})
    return sorted(by_key.values(), key=_when)


def filename(mycall: str, park: str, date: str) -> str:
    """KR1TTR@US-1234-20261021.adi (a slash isn't allowed in a file name)."""
    return f"{mycall.upper().replace('/', '_')}@{park}-{date}.adi"

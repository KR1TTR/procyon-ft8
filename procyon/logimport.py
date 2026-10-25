"""Bring QSOs from elsewhere (QRZ's logbook, or an ADIF file from another
program) into Procyon's log, so "NEW" and "new band" know everyone you've
already worked. QSOs Procyon already has are skipped, so importing the same
logbook twice adds nothing."""

import datetime as dt

from .geo import band_of, parse_adif  # noqa: F401 - parse_adif is used by the importers

# A QSO counts as already in the log if the same call on the same band is
# logged within this many minutes (programs record the start a little
# differently: first call, first report, or when it was logged).
SAME_QSO_MIN = 15


def clean(q: dict) -> dict:
    """Tidy an imported QSO the way Procyon writes its own."""
    q = dict(q)
    q["call"] = q["call"].upper()
    if q.get("gridsquare"):
        g = q["gridsquare"]
        q["gridsquare"] = g[:4].upper() + g[4:].lower()
    if q.get("band"):
        q["band"] = q["band"].lower()
    elif q.get("freq"):
        try:
            q["band"] = band_of(int(float(q["freq"]) * 1e6))
        except ValueError:
            pass
    return q


def _when(q: dict):
    d, t = q.get("qso_date", ""), (q.get("time_on", "") + "00")[:6]
    try:
        return dt.datetime.strptime(d + t, "%Y%m%d%H%M%S")
    except ValueError:
        return None


def new_qsos(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """The incoming QSOs the log doesn't have yet (nor twice among themselves)."""
    seen: dict[tuple, list] = {}
    for q in existing:
        seen.setdefault((q.get("call", "").upper(), q.get("band", "").lower()), []).append(_when(q))
    out = []
    window = dt.timedelta(minutes=SAME_QSO_MIN)
    for q in incoming:
        key = (q["call"], q.get("band", ""))
        when = _when(q)
        times = seen.setdefault(key, [])
        if any(t == when or (t and when and abs(t - when) <= window) for t in times):
            continue
        times.append(when)
        out.append(q)
    return out


def sort_key(q: dict):
    return (q.get("qso_date", ""), (q.get("time_on", "") + "000000")[:6])

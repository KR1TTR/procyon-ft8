"""Country lookup (cty.dat), Maidenhead grid math, and the ADIF logbook."""

import datetime as dt
import math
import os
import re
import shutil

from . import __version__
from .system import find_cty


DEMO_MARK = "Procyon demo mode - simulated QSO"
DEMO_MARKS = (DEMO_MARK, "Skywave demo mode - simulated QSO")   # incl. the earlier name


def strip_demo_mark(comment: str) -> str:
    for mark in DEMO_MARKS:
        if comment.startswith(mark):
            return comment[len(mark):].lstrip(" -")
    return comment

SUFFIXES = {"P", "M", "MM", "AM", "QRP", "R", "A", "B", "LH", "J"}


class Cty:
    def __init__(self, path=None):
        self.prefixes = {}
        self.exact = {}
        path = path or find_cty()
        if path:
            self._load(path)

    def _load(self, path):
        entity = None
        with open(path, encoding="latin-1") as f:
            for line in f:
                if not line.strip():
                    continue
                if not line[0].isspace():
                    parts = [p.strip() for p in line.split(":")]
                    entity = {
                        "country": parts[0], "cq": parts[1], "itu": parts[2],
                        "cont": parts[3], "lat": float(parts[4]),
                        "lon": -float(parts[5]),  # cty.dat uses west-positive
                    }
                    continue
                for item in line.strip().rstrip(";").split(","):
                    item = re.sub(r"\(.*?\)|\[.*?\]|<.*?>|\{.*?\}|~.*?~", "",
                                  item.strip())
                    if not item:
                        continue
                    if item.startswith("="):
                        self.exact[item[1:]] = entity
                    else:
                        self.prefixes[item] = entity

    def lookup(self, call: str):
        if not call:
            return None
        call = call.strip("<>").upper()
        if call in self.exact:
            return self.exact[call]
        parts = [p for p in call.split("/") if p and p not in SUFFIXES
                 and not (len(p) == 1 and p.isdigit())]
        if not parts:
            return None
        base = parts[0]
        if len(parts) > 1:  # "PJ4/K1ABC" or "K1ABC/VE3": the short part is the prefix
            base = min(parts, key=len)
        if base in self.exact:
            return self.exact[base]
        # Only KG4 + two letters (e.g. KG4AB) is Guantanamo Bay; other KG4 calls are US.
        us_kg4 = base.startswith("KG4") and not re.fullmatch(r"KG4[A-Z]{2}", base)
        for n in range(len(base), 0, -1):
            if us_kg4 and n >= 3:
                continue
            hit = self.prefixes.get(base[:n])
            if hit:
                return hit
        return None


def grid_to_latlon(grid: str):
    g = (grid or "").upper()
    if len(g) < 4 or not re.match(r"^[A-R]{2}\d{2}", g):
        return None
    lon = (ord(g[0]) - 65) * 20 - 180 + int(g[2]) * 2 + 1
    lat = (ord(g[1]) - 65) * 10 - 90 + int(g[3]) + 0.5
    if len(g) >= 6 and re.match(r"^[A-X]{2}$", g[4:6]):
        lon += (ord(g[4]) - 65) * (2 / 24) + 1 / 24 - 1
        lat += (ord(g[5]) - 65) * (1 / 24) + 1 / 48 - 0.5
    return lat, lon


def distance_bearing(a, b):
    """Great-circle distance (km) and initial bearing (deg) between lat/lon pairs."""
    if not a or not b:
        return None
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    d = 2 * math.asin(math.sqrt(math.sin((la2 - la1) / 2) ** 2 + math.cos(la1)
                                * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return round(6371 * d), round((math.degrees(math.atan2(y, x)) + 360) % 360)


BANDS = [  # name, low Hz, high Hz, FT8 dial Hz, FT4 dial Hz (0: none in common use)
    ("160m", 1_800_000, 2_000_000, 1_840_000, 0),
    ("80m", 3_500_000, 4_000_000, 3_573_000, 3_575_000),
    ("60m", 5_300_000, 5_410_000, 5_357_000, 0),
    ("40m", 7_000_000, 7_300_000, 7_074_000, 7_047_500),
    ("30m", 10_100_000, 10_150_000, 10_136_000, 10_140_000),
    ("20m", 14_000_000, 14_350_000, 14_074_000, 14_080_000),
    ("17m", 18_068_000, 18_168_000, 18_100_000, 18_104_000),
    ("15m", 21_000_000, 21_450_000, 21_074_000, 21_140_000),
    ("12m", 24_890_000, 24_990_000, 24_915_000, 24_919_000),
    ("10m", 28_000_000, 29_700_000, 28_074_000, 28_180_000),
    ("6m", 50_000_000, 54_000_000, 50_313_000, 50_318_000),
    ("2m", 144_000_000, 148_000_000, 144_174_000, 144_170_000),
]


def band_of(hz):
    for name, lo, hi, *_ in BANDS:
        if lo <= hz <= hi:
            return name
    return ""


FIELD_RE = re.compile(r"<(\w+)(?::(\d+))?(?::[^>]*)?>", re.I)


def parse_adif(text: str) -> list[dict]:
    """The QSOs in an ADIF (.adi) text. Each value is read by its length, so a
    note containing "<" or ">" comes through intact. Field names lowercase."""
    m = re.search(r"<eoh>", text, re.I)
    pos = m.end() if m else (0 if text.lstrip().startswith("<") else len(text))
    qsos, rec = [], {}
    while True:
        m = FIELD_RE.search(text, pos)
        if not m:
            break
        name = m.group(1).lower()
        if name == "eor":
            if rec.get("call"):
                qsos.append(rec)
            rec, pos = {}, m.end()
            continue
        n = int(m.group(2) or 0)
        value = text[m.end():m.end() + n]
        pos = m.end() + n
        if value.strip():
            rec[name] = value.strip()
    return qsos


class Logbook:
    """Append-only ADIF log, compatible with WSJT-X's wsjtx_log.adi format."""

    def __init__(self, path):
        self.path = path
        self.qsos = []
        if os.path.exists(path):
            self._read()

    def _read(self):
        self.qsos = parse_adif(open(self.path, encoding="utf-8", errors="replace").read())

    def worked(self, call, band=None):
        call = call.upper()
        return any(q.get("call", "").upper() == call and
                   (band is None or q.get("band", "").lower() == band) for q in self.qsos)

    HEADER = (f"Procyon ADIF export\n<adif_ver:5>3.1.4 <programid:7>Procyon "
              f"<programversion:{len(__version__)}>{__version__} <eoh>\n")

    @staticmethod
    def _record(q: dict) -> str:
        return "".join(f"<{k}:{len(str(v))}>{v} " for k, v in q.items()
                       if v not in (None, "")) + "<eor>\n"

    def add(self, q: dict):
        new = not os.path.exists(self.path)
        with open(self.path, "a", encoding="utf-8") as f:
            if new:
                f.write(self.HEADER)
            f.write(self._record(q))
        self.qsos.append({k.lower(): str(v) for k, v in q.items()})

    def _rewrite(self):
        shutil.copy2(self.path, self.path + ".bak")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(self.HEADER + "".join(self._record(q) for q in self.qsos))

    def add_many(self, qsos: list, key):
        """Add QSOs from elsewhere and put the whole log in date order (`key`),
        so older imported QSOs don't show up as the most recent ones."""
        self.qsos += [{k.lower(): str(v) for k, v in q.items()} for q in qsos]
        self.qsos.sort(key=key)
        if os.path.exists(self.path):
            self._rewrite()
        else:
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(self.HEADER + "".join(self._record(q) for q in self.qsos))

    def update(self, idx: int, fields: dict):
        """Change fields of one QSO (an empty value removes the field)."""
        q = self.qsos[idx]
        for k, v in fields.items():
            v = str(v).strip()
            if v:
                q[k] = v
            else:
                q.pop(k, None)
        self._rewrite()

    def remove(self, idx: int) -> dict:
        """Delete one QSO (the file is backed up first) and return it."""
        q = self.qsos.pop(idx)
        self._rewrite()
        return q

    def mark(self, idxs, fields: dict):
        """Set the same fields on several QSOs, with a single rewrite."""
        for i in idxs:
            self.qsos[i].update(fields)
        if idxs:
            self._rewrite()

    def take(self, pred) -> list:
        """Remove the QSOs matching `pred` (backing up the file first) and return them."""
        hits = [q for q in self.qsos if pred(q)]
        if hits:
            self.qsos = [q for q in self.qsos if not pred(q)]
            self._rewrite()
        return hits

    def recent(self, n=50):
        out = []
        first = max(0, len(self.qsos) - n)
        for idx in range(len(self.qsos) - 1, first - 1, -1):
            q = self.qsos[idx]
            out.append({
                "idx": idx, "tx_pwr": q.get("tx_pwr", ""),
                "comment": strip_demo_mark(q.get("comment", "")),
                "call": q.get("call", ""), "grid": q.get("gridsquare", ""),
                "band": q.get("band", ""), "sent": q.get("rst_sent", ""),
                "rcvd": q.get("rst_rcvd", ""), "freq": q.get("freq", ""),
                "date": q.get("qso_date", ""), "time": q.get("time_on", ""),
                "qrz": q.get("qrzcom_qso_upload_status", "").upper() == "Y",
                "lotw": q.get("lotw_qsl_sent", "").upper() == "Y",
                "park": q.get("my_sig_info", "") if q.get("my_sig", "").upper() == "POTA" else "",
            })
        return out


def utc_now():
    return dt.datetime.now(dt.timezone.utc)

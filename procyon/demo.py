"""Simulated FT8 (or FT4) band for trying Procyon without a radio.

Produces 12 kHz audio containing background QSOs, stations calling CQ, and
stations that respond to whatever you transmit - so the whole receive,
decode, auto-sequence and log loop can be exercised end to end."""

import os
import random
import string

import numpy as np

from . import ft8

FS = 12000
# For testing band sync: every demo station arrives this many seconds late, as
# if this computer's clock were that much fast (e.g. PROCYON_DEMO_CLOCK_ERROR=1.2).
CLOCK_ERROR = float(os.environ.get("PROCYON_DEMO_CLOCK_ERROR") or 0)

REGIONS = [
    ("K", ["FN42", "EM73", "DM79", "CN87", "EN52", "FM18", "EL96"]),
    ("W", ["FN31", "EM10", "DM43", "CN85", "EN34", "FM29"]),
    ("N", ["EM48", "FN20", "DN70", "CM97"]), ("VE", ["FN25", "EN82", "DO21"]),
    ("G", ["IO91", "IO83", "IO92"]), ("F", ["JN18", "IN78", "JN03"]),
    ("DL", ["JO62", "JN58", "JO31"]), ("I", ["JN61", "JN45"]), ("EA", ["IN80", "IM87"]),
    ("JA", ["PM95", "QM06", "PM74"]), ("VK", ["QF56", "PF95", "OF78"]), ("ZL", ["RF70"]),
    ("PY", ["GG66", "GG87"]), ("LU", ["GF05"]), ("OH", ["KP20"]), ("SP", ["KO02"]),
    ("OK", ["JO70"]), ("YO", ["KN34"]), ("UA", ["KO85", "LO07"]), ("ZS", ["KG33"]),
    ("HB9", ["JN47"]), ("ON", ["JO20"]), ("PA", ["JO22"]), ("SM", ["JO89"]),
    ("LA", ["JO59"]), ("EI", ["IO63"]), ("CT", ["IM58"]), ("KH6", ["BL11"]),
    ("KL7", ["BP51"]), ("4X", ["KM72"]), ("A6", ["LL75"]), ("VU", ["MK82"]),
    ("BV", ["PL05"]), ("HL", ["PM37"]), ("YB", ["OI33"]), ("9V", ["OJ11"]),
]


class Station:
    def __init__(self, rng: random.Random, taken):
        while True:
            prefix, grids = rng.choice(REGIONS)
            call = prefix + ("" if prefix[-1].isdigit() else str(rng.randint(0, 9)))
            call += "".join(rng.choice(string.ascii_uppercase) for _ in range(rng.choice((2, 3, 3))))
            if call not in taken:
                break
        self.call = call
        self.grid = rng.choice(grids)
        self.freq = rng.uniform(250, 2800)
        self.snr = rng.uniform(-22, 6)
        self.dt = rng.uniform(-0.2, 0.6)
        self.parity = rng.randint(0, 1)


class BackgroundQso:
    """Two simulated stations working each other."""

    def __init__(self, rng, taken, step=0):
        self.a, self.b = Station(rng, taken), Station(rng, taken)
        self.b.parity = 1 - self.a.parity
        a, b = self.a, self.b
        ra, rb = ft8.fmt_report(rng.randint(-20, 5)), ft8.fmt_report(rng.randint(-20, 5))
        self.steps = [(a, f"CQ {a.call} {a.grid}"), (b, f"{a.call} {b.call} {b.grid}"),
                      (a, f"{b.call} {a.call} {ra}"), (b, f"{a.call} {b.call} R{rb}"),
                      (a, f"{b.call} {a.call} RR73"), (b, f"{a.call} {b.call} 73")]
        self.i = step
        self.cq_repeats = rng.randint(0, 3)

    @property
    def done(self):
        return self.i >= len(self.steps)


class DemoBand:
    def __init__(self, mode=ft8, seed=None):
        self.m = mode                 # the ft8 or ft4 module
        self.period_samples = int(mode.PERIOD * FS)
        self.rng = random.Random(seed)
        self.mycall = ""
        self.bg: list[BackgroundQso] = [BackgroundQso(self.rng, set(), self.rng.randint(0, 4))
                                        for _ in range(5)]
        self.cqers: dict[str, dict] = {}
        self.partners: dict[str, Station] = {}
        self.recent: dict[str, Station] = {}
        self.last_cq = -999        # period of the user's last CQ
        self.last_tx = -999        # period of the user's last transmission
        self.replies: dict[int, list] = {}
        self._cache: dict[int, np.ndarray] = {}

    def taken(self):
        calls = {self.mycall} | set(self.cqers) | set(self.partners)
        for q in self.bg:
            calls |= {q.a.call, q.b.call}
        return calls

    # --------------------------------------------------- user transmissions

    def user_tx(self, period: int, msg: str):
        """Called when the user transmits `msg` in `period` - schedule replies."""
        p = ft8.parse(msg)
        out = self.replies.setdefault(period + 1, [])
        my = self.mycall
        self.last_tx = period
        if p["kind"] == "cq":
            self.last_cq = period
        if p["kind"] == "cq" and p["from"] == my:
            if self.rng.random() < 0.65:
                st = Station(self.rng, self.taken())
                st.parity = (period + 1) % 2
                self.partners[st.call] = st
                out.append((st, f"{my} {st.call} {st.grid}"))
            return
        if p["kind"] != "std" or p["from"] != my:
            return
        st = self.partners.get(p["to"])
        if st is None and p["to"] in self.cqers:
            st = self.cqers.pop(p["to"])["st"]
        if st is None:
            st = self.recent.pop(p["to"], None)
        if st is None:
            # A station finishing another QSO will take our call once it's done.
            for q in self.bg:
                if q.i >= 4 and p["to"] in (q.a.call, q.b.call):
                    st = q.a if q.a.call == p["to"] else q.b
        if st is not None:
            st.parity = (period + 1) % 2
            self.partners[st.call] = st
            # they've been answered: drop any repeats of their call still queued
            for queued in self.replies.values():   # edit in place: `out` points at one of these
                queued[:] = [(s2, m) for s2, m in queued if not (s2 is st and m == f"{my} {st.call} {st.grid}")]
        if st is None or self.rng.random() > 0.85:
            return  # nobody there / they didn't copy us this time
        rpt = ft8.fmt_report(self.rng.randint(-20, 3))
        reply = {"grid": f"{my} {st.call} {rpt}", "none": f"{my} {st.call} {rpt}",
                 "report": f"{my} {st.call} R{rpt}", "rreport": f"{my} {st.call} RR73",
                 "rr73": f"{my} {st.call} 73"}.get(p["extra_kind"])
        if reply:
            out.append((st, reply))
        if p["extra_kind"] in ("rr73", "73"):
            self.partners.pop(st.call, None)

    # ------------------------------------------------------- audio frames

    def _plan(self, period: int):
        par = period % 2
        sigs = []
        for q in self.bg:
            st, msg = q.steps[q.i]
            if st.parity != par:
                continue
            sigs.append((st, msg))
            if q.i == 0 and q.cq_repeats > 0:
                q.cq_repeats -= 1
            elif self.rng.random() < 0.85:
                q.i += 1
        for q in self.bg:
            if q.done:
                self.recent.update({q.a.call: q.a, q.b.call: q.b})
        self.recent = dict(list(self.recent.items())[-20:])
        self.bg = [q for q in self.bg if not q.done]
        while len(self.bg) < 6:
            self.bg.append(BackgroundQso(self.rng, self.taken()))
        while len(self.cqers) < 3:
            st = Station(self.rng, self.taken())
            # Some callers are activators or chasing DX, as on a real band.
            tag = self.rng.choice(["", "", "", "", "POTA ", "POTA ", "SOTA ", "DX "])
            self.cqers[st.call] = {"st": st, "left": self.rng.randint(4, 12), "tag": tag}
        for call, c in list(self.cqers.items()):
            if c["st"].parity == par:
                sigs.append((c["st"], f"CQ {c['tag']}{call} {c['st'].grid}"))
                c["left"] -= 1
                if c["left"] <= 0:
                    self.recent[call] = self.cqers.pop(call)["st"]
        sigs += self.replies.pop(period, [])
        # Like a real band: after you've called CQ, a station may call you later,
        # even when you've stopped transmitting (it saw your CQ a while ago).
        if (self.mycall and period - self.last_cq < 40 and period - self.last_tx > 2
                and self.rng.random() < 0.12):
            st = Station(self.rng, self.taken())
            st.parity = period % 2
            self.partners[st.call] = st
            call = (st, f"{self.mycall} {st.call} {st.grid}")
            sigs.append(call)                                   # now...
            for k in range(1, self.rng.randint(3, 5)):          # ...and a few more times
                self.replies.setdefault(period + 2 * k, []).append(call)
        return sigs

    def period_audio(self, period: int) -> np.ndarray:
        if period in self._cache:
            return self._cache[period]
        n_period = self.period_samples
        buf = np.random.default_rng().normal(0, 1, n_period)
        for st, msg in self._plan(period):
            tones, _ = self.m.encode(msg)
            w = self.m.synth(tones, st.freq, FS)
            snr = st.snr + self.rng.uniform(-2, 2)  # a little QSB
            amp = np.sqrt(2 * (2500 / FS * 2) * 10 ** (snr / 10))
            s = max(0, int((self.m.TX_START + st.dt + CLOCK_ERROR) * FS))
            n = min(len(w), n_period - s)
            buf[s:s + n] += amp * w[:n]
        audio = np.clip(buf * 1500, -32767, 32767).astype(np.int16)
        self._cache = {k: v for k, v in self._cache.items() if k >= period - 1}
        self._cache[period] = audio
        return audio

    def samples(self, n0: int, n1: int) -> np.ndarray:
        """Absolute sample indices since the Unix epoch (12 kHz)."""
        out = []
        while n0 < n1:
            period, off = divmod(n0, self.period_samples)
            take = min(n1 - n0, self.period_samples - off)
            out.append(self.period_audio(period)[off:off + take])
            n0 += take
        return np.concatenate(out) if out else np.zeros(0, np.int16)

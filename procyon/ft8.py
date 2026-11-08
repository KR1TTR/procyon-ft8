"""FT8 protocol helpers: message encoding (Procyon's own packer, pack77.py,
plus the shared error correction in fec.py), GFSK waveform synthesis (port
of WSJT-X gen_ft8wave.f90), and message parsing."""

import functools
import logging
import re
import subprocess
from math import erf, log, pi, sqrt

import numpy as np

from . import pack77
from .fec import codeword
from .system import NO_WINDOW

_log = logging.getLogger("procyon")   # not "log": math.log is used below

PERIOD = 15.0          # seconds per T/R cycle
SYMBOL_SEC = 0.16      # 1920 samples @ 12 kHz
NSYM = 79
TONE_SPACING = 6.25    # Hz
TX_START = 0.5         # nominal signal start within a period (DT = 0)
SIGNAL_SEC = NSYM * SYMBOL_SEC  # 12.64 s
NAME = "FT8"
WIDTH = 50             # Hz a signal takes up on the waterfall
DECODE_AT = 13.4       # seconds into the period when we decode
# A transmission may start up to this many seconds after its normal start. Like
# WSJT-X, the part that should already have gone out is skipped, so the signal
# stays aligned and ends on time; FT8's error correction rebuilds the missing
# start. Tested: still decodes at -18 dB when started 3.5 s late.
LATE_START_MAX = 3.0
JT9_MODE = "-8"        # jt9's switch for this mode
UDP_MODE = "~"         # how WSJT-X marks this mode in its UDP Decode messages
ADIF = {"mode": "FT8"}

GRID_RE = re.compile(r"^[A-R]{2}[0-9]{2}$")
REPORT_RE = re.compile(r"^[+-]\d{2}$")
RREPORT_RE = re.compile(r"^R[+-]\d{2}$")
CALL_RE = re.compile(r"^<?[A-Z0-9/]*\d[A-Z0-9/]*>?$")
# Callsigns FT8 can send in full in a standard message: a 1-2 character prefix,
# the area digit, a 1-3 letter suffix (6 characters at most), optionally /P or
# /R. Anything else (3E40CDW, PJ4/K1ABC, GB13COL, VE3ABCD) has to go as a short
# code written <CALL>, as WSJT-X does - written plainly it falls back to 13
# characters of free text and the callsign is cut off. Checked against ft8code.
STD_CALL_RE = re.compile(r"^[A-Z0-9]{1,2}\d[A-Z]{1,3}$")


def is_std_call(call: str) -> bool:
    c = strip_call(call.upper())
    if c.endswith(("/P", "/R")):
        c = c[:-2]
    return len(c) <= 6 and bool(STD_CALL_RE.match(c))


def msg_call(call: str) -> str:
    """How a callsign is written in a standard message: <CALL> if it can't be sent in full."""
    c = strip_call(call.upper())
    return c if is_std_call(c) else f"<{c}>"

def base_call(call: str) -> str:
    """The home call inside a portable one: P4/WE9V -> WE9V, CT2JWF/QRP -> CT2JWF,
    KR1TTR/P -> KR1TTR. Calls without a slash come back unchanged."""
    c = strip_call((call or "").upper())
    if "/" not in c:
        return c
    parts = [x for x in c.split("/") if x]
    std = [x for x in parts if STD_CALL_RE.match(x)]
    return max(std or parts, key=len) if parts else c


def same_station(a: str, b: str) -> bool:
    """True if two callsigns are the same operator. When both calls in a QSO are
    portable, reports go out with base calls (P4/WE9V becomes WE9V) - so
    "WE9V" in a report still means the P4/WE9V we're working."""
    a, b = strip_call((a or "").upper()), strip_call((b or "").upper())
    if not a or not b or a.startswith("...") or b.startswith("..."):
        return False
    return a == b or (("/" in a or "/" in b) and base_call(a) == base_call(b))


def fits(msg: str):
    """True if `msg` goes out exactly as written, False if it would be cut or
    can't be sent at all, None if that can't be checked."""
    try:
        return encode(msg)[1] == normalize(msg)
    except ValueError:
        return False
    except Exception:  # noqa: BLE001 - ft8code missing: can't check here
        return None


def first_fit(candidates: list[str]) -> str:
    """The first message that goes out intact; the first one if none can be checked."""
    for c in candidates:
        ok = fits(c)
        if ok or ok is None:
            return c
    return candidates[0]


def normalize(msg: str) -> str:
    return " ".join(msg.upper().split())[:37]


COSTAS = (3, 1, 4, 0, 6, 5, 2)
GRAYMAP = (0, 1, 3, 2, 5, 6, 4, 7)

# While Procyon's own packer is new, WSJT-X's ft8code (when installed) packs
# every message too. If they ever disagree, ft8code's answer is used and the
# difference goes in the log - so what goes on the air can't change because
# of the new packer. Set to False once it has proven itself.
CROSS_CHECK = True


def _ft8code_bits(msg: str):
    """(77 bits as a '0'/'1' string, decoded_text) from WSJT-X's ft8code, or
    None when it isn't installed (or gives no answer)."""
    from .system import find_tool  # noqa: PLC0415
    exe = find_tool("ft8code")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, msg], capture_output=True, text=True,
                             timeout=10, **NO_WINDOW).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return None
    decoded, bits = "", None
    for i, line in enumerate(out):
        if line.startswith(" 1.") and not decoded:
            decoded = line[42:79].strip()
        if line.startswith("Source-encoded message") and i + 1 < len(out):
            bits = out[i + 1].strip()
    if not bits or len(bits) != 77:
        return None
    return bits, decoded


@functools.lru_cache(maxsize=512)
def message_bits(msg: str):
    """(77 bits as a list, decoded_text) for `msg`. decoded_text is how a
    receiver will read it: it differs from `msg` if the message can't be sent
    as written (e.g. cut to 13 characters of free text)."""
    msg = normalize(msg)
    try:
        ours = pack77.encode(msg)
    except pack77.BadMessage:
        ours = None
    if CROSS_CHECK:
        ref = _ft8code_bits(msg)
        if ref is not None:
            if ours is None and ref[1] == "*** bad message ***":
                raise ValueError(f"{msg!r} can't be sent")
            if ours != ref:
                _log.warning("message packer: Procyon and ft8code differ for %r - using ft8code's "
                            "(Procyon %s %r, ft8code %s %r)", msg, *(ours or ("-", "")), *ref)
                ours = ref
    if ours is None or ours[1] == "*** bad message ***":
        raise ValueError(f"{msg!r} can't be sent")
    bits, decoded = ours
    return [int(c) for c in bits], decoded


def tones_from_bits(bits77) -> tuple:
    """79 FT8 tones (0-7) for 77 message bits: CRC + LDPC parity, three bits
    per tone (Gray coded), three 7x7 Costas sync arrays."""
    cw = codeword(bits77)
    data = tuple(GRAYMAP[4 * cw[3 * i] + 2 * cw[3 * i + 1] + cw[3 * i + 2]] for i in range(58))
    return COSTAS + data[:29] + COSTAS + data[29:] + COSTAS


@functools.lru_cache(maxsize=512)
def encode(msg: str):
    """Return (tones, decoded_text). decoded_text is how a receiver will see
    the message - it differs from `msg` if the message can't be encoded."""
    bits, decoded = message_bits(msg)
    return tones_from_bits(bits), decoded


def _gfsk_pulse(bt: float, t: np.ndarray) -> np.ndarray:
    c = pi * sqrt(2.0 / log(2.0))
    verf = np.vectorize(erf)
    return 0.5 * (verf(c * bt * (t + 0.5)) - verf(c * bt * (t - 0.5)))


@functools.lru_cache(maxsize=4)
def _pulse(nsps: int) -> np.ndarray:
    t = (np.arange(3 * nsps) - 1.5 * nsps) / nsps
    return _gfsk_pulse(2.0, t)


def synth(tones, f0: float, fs: int = 12000) -> np.ndarray:
    """Synthesize a constant-envelope GFSK FT8 waveform (float, amplitude 1)."""
    nsps = int(round(SYMBOL_SEC * fs))
    pulse = _pulse(nsps)
    itone = np.asarray(tones, dtype=float)
    dphi_peak = 2 * pi / nsps
    dphi = np.zeros((NSYM + 2) * nsps)
    for j, tone in enumerate(itone):
        dphi[j * nsps:j * nsps + 3 * nsps] += dphi_peak * pulse * tone
    # Dummy symbols at both ends so the first/last tones are fully shaped.
    dphi[0:2 * nsps] += dphi_peak * itone[0] * pulse[nsps:3 * nsps]
    dphi[NSYM * nsps:(NSYM + 2) * nsps] += dphi_peak * itone[-1] * pulse[0:2 * nsps]
    dphi += 2 * pi * f0 / fs
    nwave = NSYM * nsps
    phi = np.concatenate(([0.0], np.cumsum(dphi[nsps:nsps + nwave - 1])))
    wave = np.sin(phi)
    nramp = int(round(nsps / 8))
    ramp = np.arange(nramp)
    wave[:nramp] *= (1 - np.cos(2 * pi * ramp / (2 * nramp))) / 2
    wave[nwave - nramp:] *= (1 + np.cos(2 * pi * ramp / (2 * nramp))) / 2
    return wave


# ---------------------------------------------------------------- parsing

def strip_call(tok: str) -> str:
    return tok.strip("<>")


def is_call(tok: str) -> bool:
    t = strip_call(tok)
    return (bool(CALL_RE.match(tok)) and 3 <= len(t) <= 13
            and any(ch.isalpha() for ch in t) and t not in ("RR73",))


def classify_extra(tok: str | None):
    """Classify the third word of a standard message."""
    if tok is None:
        return "none"
    if tok in ("RR73", "RRR"):
        return "rr73"
    if tok == "73":
        return "73"
    if GRID_RE.match(tok):
        return "grid"
    if REPORT_RE.match(tok):
        return "report"
    if RREPORT_RE.match(tok):
        return "rreport"
    return "other"


# DXpedition / multi-answer style: one transmission finishes one QSO and starts
# the next - "N3LSB RR73; KR1TTR <P4/WE9V> -06" (WSJT-X Fox, MSHV multi-stream).
DXPED_RE = re.compile(r"^(\S+) RR73; (\S+) (<[^>]*>) ([+-]\d{2})$")


def parse(msg: str, mycall: str = "") -> dict:
    """Split a decoded FT8 message into its parts. For a two-in-one DXpedition
    message, the half addressed to `mycall` is the one described (if neither
    is, the new caller's half); "other" names the station in the other half."""
    m = DXPED_RE.match(msg)
    if m:
        first, second, dx, rpt = (strip_call(g) if i < 3 else g for i, g in enumerate(m.groups()))
        p = {"kind": "std", "from": dx, "grid": None, "dxped": True}
        if mycall and first == mycall.upper():
            p.update(to=first, other=second, extra="RR73", extra_kind="rr73")
        else:
            p.update(to=second, other=first, extra=rpt, extra_kind="report")
        return p
    toks = msg.split()
    p = {"kind": "free", "to": None, "from": None, "grid": None,
         "extra": None, "extra_kind": "none"}
    if not toks:
        return p
    if toks[0] == "CQ" or toks[0].startswith("CQ_"):
        rest = toks[1:]
        # "CQ DX K1ABC FN42", "CQ POTA K1ABC FN42"
        if len(rest) >= 2 and not is_call(rest[0]) and is_call(rest[1]):
            p["cq_tag"] = rest[0]
            rest = rest[1:]
        if rest and is_call(rest[0]):
            p.update(kind="cq", **{"from": strip_call(rest[0])})
            if len(rest) > 1 and GRID_RE.match(rest[1]):
                p["grid"] = rest[1]
        return p
    if len(toks) >= 2 and is_call(toks[0]) and is_call(toks[1]):
        p.update(kind="std", to=strip_call(toks[0]), **{"from": strip_call(toks[1])})
        extra = toks[-1] if len(toks) >= 3 else None
        if len(toks) >= 4 and toks[2] == "R" and GRID_RE.match(toks[3]):
            extra = toks[3]          # "K1ABC W9XYZ R EN37"
            p["extra_kind"] = "rgrid"
        else:
            p["extra_kind"] = classify_extra(extra)
        p["extra"] = extra
        if p["extra_kind"] in ("grid", "rgrid"):
            p["grid"] = extra
    return p


def fmt_report(snr: int) -> str:
    snr = max(-50, min(49, int(snr)))
    return f"{snr:+03d}"

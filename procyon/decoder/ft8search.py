"""Procyon's own FT8 decoder - stage 3: find the signals ourselves.

    decode_cycle(audio) -> list of decodes (dicts: text, snr, dt, freq, ...)

How it finds them (the classic FT8 recipe):
  1. a coarse waterfall of the whole cycle: 3.125 Hz x 0.04 s cells
  2. at every frequency and start time, score how well the three Costas
     sync patterns line up -> the best spots become candidates
  3. each candidate gets its own narrow-band copy of the audio (about
     60 Hz wide, 200 samples/s) - small enough that fine-tuning time and
     frequency and measuring the tones costs almost nothing
  4. soft bits + LDPC + CRC from ft8decode (stage 2)

No WSJT-X code; the recipe follows the published FT8 description.
"""

import re

import numpy as np

from . import ft8decode as D

FS = 12000
NSPS = 1920
NMAX = 15 * FS                  # one cycle of audio
BAUD = FS / NSPS                # 6.25 Hz
STEP = NSPS // 4                # coarse time step: 0.04 s
NFFT1 = 2 * NSPS                # coarse frequency step: 3.125 Hz
DF1 = FS / NFFT1
FS2 = 200                       # narrow-band copy: 200 samples/s
NSPS2 = NSPS * FS2 // FS        # 32 samples per symbol
NDOWN = NMAX * FS2 // FS        # 3000 samples per cycle
COSTAS = np.array(D.COSTAS)
# fine_tune's score for pure noise: 21 sync symbols x mean |sum of 32 noise samples|
NOISE_SYNC = 21 * np.sqrt(32) * np.sqrt(np.pi) / 2
OSD_MIN_SYNC = 1.2              # OSD only for candidates that sync this well (was 2.2; 1.2 measured 2026-11-09)
OSD_TRIES = 3                   # soft-bit methods OSD tries, closest BP first

FMIN, FMAX = 100, 3300          # where to look (Hz, lowest tone)
SYNC_MIN = 1.6                  # coarse sync needed to be a candidate
MAX_CANDIDATES = 300
OSD_ZSUM_TRIES = 1              # with "both": summed-belief tries per candidate (of OSD_TRIES)
OSD_ON_ZSUM = "both"            # OSD gets: "llr" (channel), "zsum" (BP's summed beliefs) or "both"
RESWEEPS = 0                    # re-estimate every subtracted signal this many times per pass
TUNE_SPAN = 10                  # fine search: +- this many samples (5 ms) around the rough start
LAG_MIN, LAG_MAX = -62, 62      # coarse start times: DT -2.48 .. +2.48 s (x 0.04 s)
PASSES = 3                      # decode + subtract rounds per cycle (4 before NEAR_RETRY; 3 + NEAR_RETRY beats 4, 2026-11-11)
LAGS_PER_FREQ = 1               # start times kept per frequency cell
# Known stations (heard in earlier cycles; the caller keeps the list and
# passes known=[{"call", "freq", "dt", ...}]). Their last spot is tried
# directly as a candidate, with a narrower fine search, so a station
# sitting right on top of another one is still looked for where it was.
SEED_PASSES = 9                 # 0 = off, 1 = pass 1 only, 9 = every pass (every pass: +1.5%, 2026-11-11)
SEED_SPAN = 4                   # seeds: fine search +- samples (5 ms each)
SEED_DF = 1.0                   # seeds: fine search +- Hz
KNOWN_AP = "kpr"                # guesses at a seed's spot, letters: k = "??? CALL ???",
                                # p = "PARTNER CALL ???", r = repeat of its last message / its 73
KNOWN_FULL_MAX_HARD = 32        # r: at most this many of 174 bits off (45 gave wrong "repeats")
KNOWN_TRUST = 0.0               # > 0: a guessed/OSD decode from a known call within this many Hz
                                # of its last spot skips the plausibility checks


# ------------------------------------------------------------------ step 1-2

def waterfall(audio):
    """Power, frequency cells x time cells (0.04 s steps from the cycle start)."""
    nframes = NMAX // STEP - 3
    win = np.zeros((nframes, NFFT1))
    a = np.zeros(NMAX + NSPS)
    a[:min(len(audio), NMAX)] = audio[:NMAX]
    idx = np.arange(NSPS)[None, :] + STEP * np.arange(nframes)[:, None]
    win[:, :NSPS] = a[idx]
    return (np.abs(np.fft.rfft(win, axis=1)) ** 2).T      # (NFFT1/2+1) x nframes


DEDUP_DT = 2          # skip a spot within this many time steps (0.04 s) of a better one (WSJT-X: 0)


def candidates(s):
    """(sync, DT in s, freq) for the best spots (DT 0 = 0.5 s into the cycle).
    Every frequency and start time is scored at once (numpy arrays)."""
    nf, nt = s.shape
    i0, i1 = int(FMIN / DF1), min(int(FMAX / DF1), nf - 15)
    ni = i1 - i0
    lags = np.arange(LAG_MIN, LAG_MAX + 1)
    offset = int(round(0.5 * FS / STEP))
    # power in the whole 8-tone band, per (lowest-tone cell, time)
    band = np.zeros((ni, nt))
    for m in range(8):
        band += s[i0 + 2 * m:i1 + 2 * m]
    scores = np.zeros((3, ni, len(lags)))
    for a, sym0 in enumerate(D.SYNC_AT):
        want = np.zeros((ni, len(lags)))
        tot = np.zeros((ni, len(lags)))
        for k in range(7):
            t = lags + offset + 4 * (sym0 + k)
            ok = (t >= 0) & (t < nt)
            tt = np.clip(t, 0, nt - 1)
            want += s[i0 + 2 * COSTAS[k]:i1 + 2 * COSTAS[k]][:, tt] * ok
            tot += band[:, tt] * ok
        scores[a] = np.where(tot > 0, want / np.maximum((tot - want) / 7, 1e-12), 0)
    # All three patterns - or, only where a pattern really falls outside the
    # cycle (a clock far off), the two that are inside, scaled up.
    first_out = (lags + offset) < 0
    last_out = (lags + offset + 4 * (D.SYNC_AT[2] + 7)) > nt
    sync = np.where(first_out, scores[1:].sum(0) * 1.5,
                    np.where(last_out, scores[:2].sum(0) * 1.5, scores.sum(0)))
    base = np.percentile(sync.max(1), 40)
    sync = sync / base if base > 0 else sync
    # The best few start times per frequency (a busy band or a far-off clock
    # can put a wrong start ahead of the real one); the fine search decides.
    spots = {}
    for n in range(ni):
        row = sync[n].copy()
        for _ in range(LAGS_PER_FREQ):
            j = int(row.argmax())
            if row[j] < SYNC_MIN:
                break
            spots[n, j] = float(row[j])
            row[max(0, j - 6):j + 7] = 0
    # Skip a spot next to a better one already taken: under 4 Hz apart
    # (= the neighbouring 3.125 Hz cell) and under 3 time steps.
    out, taken = [], set()
    for (n, j), sc in sorted(spots.items(), key=lambda kv: -kv[1]):
        if len(out) >= MAX_CANDIDATES:
            break
        if any((n + dn, j + dj) in taken for dn in (-1, 0, 1) for dj in range(-DEDUP_DT, DEDUP_DT + 1)):
            continue
        taken.add((n, j))
        out.append((sc, lags[j] * STEP / FS, (i0 + n) * DF1))
    return out


# ------------------------------------------------------------------ step 3

def spectrum(audio):
    a = np.zeros(NMAX)
    a[:min(len(audio), NMAX)] = audio[:NMAX]
    return np.fft.fft(a)


def downsample(spec, f0):
    """Complex 200 samples/s copy with the lowest tone at 0 Hz."""
    df = FS / NMAX
    lo, hi = f0 - 1.5 * BAUD, f0 + 8.5 * BAUD
    i_lo, i_hi = int(round(lo / df)), int(round(hi / df))
    i0 = int(round(f0 / df))
    band = spec[i_lo:i_hi + 1].copy()
    ntap = int(round(BAUD / df))                     # soft edges, one tone wide
    taper = 0.5 * (1 - np.cos(np.pi * np.arange(ntap) / ntap))
    band[:ntap] *= taper
    band[-ntap:] *= taper[::-1]
    out = np.zeros(NDOWN, complex)
    k = np.arange(i_lo, i_hi + 1) - i0               # bin offset from f0
    out[k % NDOWN] = band
    x = np.fft.ifft(out)
    return x / (np.sqrt(np.mean(np.abs(x) ** 2)) + 1e-12)


_n = np.arange(NSPS2)
_TONE = np.exp(2j * np.pi * np.outer(np.arange(8), _n) / NSPS2)    # 8 x 32
_COSTAS_REF = _TONE[COSTAS].conj()                                   # 7 x 32


def sync2(x, i0, dphi=None):
    """Sync score in the narrow-band copy at sample i0 (optionally shifted in frequency)."""
    sc = 0.0
    for sym0 in D.SYNC_AT:
        a = i0 + sym0 * NSPS2
        if a < 0 or a + 7 * NSPS2 > len(x):
            continue
        blk = x[a:a + 7 * NSPS2].reshape(7, NSPS2)
        if dphi is not None:
            blk = blk * dphi[a:a + 7 * NSPS2].reshape(7, NSPS2)
        sc += np.abs((blk * _COSTAS_REF).sum(1)).sum()
    return sc


_SYNC_SYMS = np.array([s0 + k for s0 in D.SYNC_AT for k in range(7)])        # 21
_SYNC_REF = _TONE[np.tile(COSTAS, 3)].conj()                                 # 21 x 32
_DFS = np.arange(-2.5, 2.51, 0.5)
_ROT = np.exp(-2j * np.pi * _DFS[:, None] * _n[None, :] / FS2)                 # df x 32


def fine_tune(x, t_start, span=None, dfmax=None):
    """Best start sample (in the copy) and frequency shift near the coarse
    spot: every start (+-10 samples) and shift (+-2.5 Hz) scored at once."""
    i_guess = int(round(t_start * FS2))
    span = TUNE_SPAN if span is None else span
    starts = np.arange(i_guess - span, i_guess + span + 1)
    idx = starts[:, None, None] + _SYNC_SYMS[None, :, None] * NSPS2 + _n[None, None, :]   # 21 x 21 x 32
    ok = (idx >= 0) & (idx < len(x))
    seg = np.where(ok, x[np.clip(idx, 0, len(x) - 1)], 0) * _SYNC_REF[None]
    # A frequency shift's phase is constant within a symbol except for the
    # within-symbol part, and the constant part vanishes under abs().
    sel = slice(None) if dfmax is None else np.abs(_DFS) <= dfmax + 1e-9
    dfs = _DFS[sel]
    score = np.abs(np.einsum("ijm,dm->dij", seg, _ROT[sel])).sum(-1)                      # df x starts
    d, i = np.unravel_index(np.argmax(score), score.shape)
    return float(score[d, i]), int(starts[i]), float(dfs[d])


SEQ_RETRY = 0                   # (2026-11-11: +0.08% for +0.11 s - left off) 1: candidates that fail get a second try at the spot a WSJT-X-style
                                # step-by-step search picks (time at 0 Hz, then freq, then time +-4)
SEQ_MIN_SYNC = 0.0
NEAR_RETRY = 1                  # JTDX-style (idea from JTDX's ft8_downsample/freqsub, credit JTDX team):
                                # rounds of re-reading failed candidates near a new decode; 2026-11-11: +0.3..0.9%
NEAR_HZ = 50.0                  # ... within this many Hz (JTDX: 50)              # ... only if the joint search's sync is >= this x noise


def seq_tune(x, t_start):
    """Step-by-step fine search (the order WSJT-X's ft8b uses): best start
    with no frequency shift, then the best shift at that start, then the
    start again within +-4 samples at that shift."""
    i_guess = int(round(t_start * FS2))
    starts = np.arange(i_guess - TUNE_SPAN, i_guess + TUNE_SPAN + 1)
    idx = starts[:, None, None] + _SYNC_SYMS[None, :, None] * NSPS2 + _n[None, None, :]
    ok = (idx >= 0) & (idx < len(x))
    seg = np.where(ok, x[np.clip(idx, 0, len(x) - 1)], 0) * _SYNC_REF[None]
    score = np.abs(np.einsum("ijm,dm->dij", seg, _ROT)).sum(-1)                           # df x starts
    d0 = int(np.argmin(np.abs(_DFS)))
    i = int(np.argmax(score[d0]))
    d = int(np.argmax(score[:, i]))
    lo, hi = max(0, i - 4), min(len(starts), i + 5)
    i = lo + int(np.argmax(score[d, lo:hi]))
    return float(score[d, i]), int(starts[i]), float(_DFS[d])


def tone_amps2(x, i0, df):
    t = np.arange(len(x)) / FS2
    y = x * np.exp(-2j * np.pi * df * t)
    seg = np.zeros(NSYM2 := D.NSYM * NSPS2, complex)
    lo, hi = max(i0, 0), min(i0 + NSYM2, len(y))
    if hi > lo:
        seg[lo - i0:hi - i0] = y[lo:hi]
    return np.fft.fft(seg.reshape(D.NSYM, NSPS2), axis=1)[:, :8] / NSPS2


def snr_estimate(amps):
    """Rough SNR in 2500 Hz, as WSJT-X reports it (signal tone vs the others)."""
    p = np.abs(amps) ** 2
    sig = p.max(1)
    noise = (p.sum(1) - sig) / 7
    ratio = sig.mean() / max(noise.mean(), 1e-12) - 1
    if ratio <= 0.001:
        return -24
    return int(round(max(-24, 10 * np.log10(ratio) - 27.0)))


# SNR the way WSJT-X reports it: the decoded tones' power against the band's
# background level at that frequency (not the station's own unused tones -
# a strong signal leaks into those, which capped the old estimate near 0 dB).
SNR_FROM_WF = True
SNR_CAL = -1.0                  # measured on the recordings vs jt9 (2026-11-11)


def snr_waterfall(audio, nsps=NSPS, step=STEP, nmax=NMAX):
    """Like waterfall(), with a Hann window: a strong signal stays in its own
    few cells instead of lifting the background around it (the plain
    window's skirts capped SNRs near +9 dB)."""
    nframes = nmax // step - 3
    a = np.zeros(nmax + nsps)
    a[:min(len(audio), nmax)] = audio[:nmax]
    win = np.zeros((nframes, 2 * nsps))
    idx = np.arange(nsps)[None, :] + step * np.arange(nframes)[:, None]
    win[:, :nsps] = a[idx] * np.hanning(nsps)
    return (np.abs(np.fft.rfft(win, axis=1)) ** 2).T


def noise_base(s):
    """Background power per frequency cell of a waterfall: a low percentile
    over the cycle's (recorded) time, then the lower envelope over +-100 Hz,
    so cells under a long signal take their neighbours' quiet level."""
    live = s.sum(0) > 1e-9 * max(float(s.sum(0).max()), 1e-30)
    if not live.any():
        return np.ones(s.shape[0]), live
    b = np.percentile(s[:, live], 30, axis=1) / 0.357      # noise power is exponential: p30 = 0.357 x mean
    w = int(100 / (FS / (2 * (s.shape[0] - 1)))) | 1      # +-100 Hz in cells, odd
    pad = np.pad(b, w // 2, mode="edge")
    env = np.percentile(np.lib.stride_tricks.sliding_window_view(pad, w), 20, axis=1)
    return np.maximum(env, 1e-30), live


def wf_snr(s, base, live, start, freq, tones, fs=FS, step=None, baud=BAUD, cal=None):
    """SNR in 2500 Hz of a decoded signal from the waterfall `s` (cells of
    half a tone, 4 time steps per symbol). start: seconds from the cycle
    start to symbol 0; freq: lowest tone, Hz."""
    nf, nt = s.shape
    step = step or STEP
    df1 = fs / (2 * (nf - 1))
    frames = np.round(start * fs / step + 4 * np.arange(len(tones))).astype(int)
    c = freq / df1 + 2 * np.asarray(tones)
    lo = np.floor(c).astype(int)
    ok = (frames >= 0) & (frames < nt) & (lo >= 0) & (lo + 1 < nf)
    ok[ok] &= live[frames[ok]]
    if ok.sum() < 10:
        return None
    lo, fr = lo[ok], frames[ok]
    p = (s[lo, fr] + s[lo + 1, fr]).mean()
    n = (base[lo] + base[lo + 1]).mean()
    ratio = 2 * (p / n - 1)                     # signal vs one cell (= one tone's width) of noise
    return int(round(max(-24, 10 * np.log10(max(ratio, 1e-3)) - 10 * np.log10(2500 / baud) + (SNR_CAL if cal is None else cal))))


# ------------------------------------------------------------------ all together

def decode_signal(x, t_start, osd=True):
    sc, i0, df = fine_tune(x, 0.5 + t_start)   # t_start is DT (from 0.5 s)
    amps = tone_amps2(x, i0, df)
    for nsym in D.NSYMS:
        bits, it = D.bp_decode(D.soft_bits(amps, nsym))
        if bits is not None and D.crc_ok(bits) and bits[:77].any():
            return bits, i0, df, amps, f"bp{nsym}"
    if osd and sc >= OSD_MIN_SYNC * NOISE_SYNC:
        for nsym in D.NSYMS:
            bits, d = D.osd_decode(D.soft_bits(amps, nsym))
            if bits is not None:
                return bits, i0, df, amps, f"osd{nsym}"
    return None, i0, df, amps, None


# ------------------------------------------------------------------ a priori
# "A priori" (AP) decoding: guess part of a weak message and let the error
# correction work out the rest. Same idea and labels as WSJT-X:
#   a1  CQ ??? ???                 (anyone calling CQ)
#   a2  MYCALL ??? ???             (anyone calling me)
#   a3  MYCALL DXCALL ???          (the station I'm working: report/grid)
#   a4  MYCALL DXCALL RRR, a5 ... 73, a6 ... RR73 (the whole message)
# Every guess is a new chance for noise to "fit", so these decodes are
# marked as guesses and must pass extra checks.

AP_SYNC_MIN = 1.8                # guesses only for candidates that sync this well (x noise)
AP_MAX_HARD = 50                 # at most this many of 174 bits may disagree with what was heard
AP_FULL_MAX_HARD = 45            # the same for a4-a6 (whole message guessed)
AP_FREQ_SPAN = 100               # a3-a6 only within this many Hz of the DX station
AP_EVERY_PASS = False            # True: guesses on every pass (+~0.1 weak CQ per cycle, +0.6 s)
from collections import deque  # noqa: E402  (bounded: Procyon runs for days)
AP_LOG = deque(maxlen=500)
TRUSTED = deque(maxlen=500)                     # decodes let through by KNOWN_TRUST - for checking
OSD_REJECTED = deque(maxlen=500)                # OSD decodes osd_sane threw out - for checking                      # (label, hard errors) of accepted guesses - for tuning


def ap_patterns(mycall="", dxcall=""):
    """(label, bit positions, bit values, whole codeword or None).
    Bits of a standard message: 0-27 first call, 28 its /R flag, 29-56
    second call, 57 its /R flag, 58 the "R" before a report/grid, 59-73
    grid or report, 74-76 message type."""
    pats = []

    def known(msg, idx):
        b = D.pack77.pack77(msg)
        idx = list(idx) + [74, 75, 76]
        return idx, [int(b[i]) for i in idx], b

    # a1: CQ, no /R on the caller, no "R" (a CQ never carries one)
    pats.append(("a1",) + known("CQ K1ABC FN42", list(range(29)) + [57, 58])[:2] + (None,))
    if mycall:
        try:   # a2: my call, and the caller is no rover (/R)
            idx, val, b = known(f"{mycall} K1ABC FN42", list(range(29)) + [57])
            if b[74:] == "001":
                pats.append(("a2", idx, val, None))
        except D.pack77.BadMessage:
            pass
        if dxcall:
            try:
                idx, val, b = known(f"{mycall} {dxcall} FN42", range(58))
                if b[74:] == "001":
                    pats.append(("a3", idx, val, None))
            except D.pack77.BadMessage:
                pass
            for label, tail in (("a4", "RRR"), ("a5", "73"), ("a6", "RR73")):
                try:
                    b = D.pack77.pack77(f"{mycall} {dxcall} {tail}")
                except D.pack77.BadMessage:
                    continue
                cw = np.array(D.fec.codeword([int(c) for c in b]), np.uint8)
                pats.append((label, None, None, cw))
    return pats


# ---- plausibility of a guessed decode (the noise-filled part must look real)

_cty = None


def _country(call):
    global _cty
    if _cty is None:
        from ..geo import Cty
        from ..system import find_cty
        _cty = Cty(find_cty())
    return _cty.lookup(call)


_WIDE = {"United States", "Canada", "European Russia", "Asiatic Russia", "Australia",
         "Brazil", "China", "Argentina", "Russia"}
_WIDE |= {"Indonesia", "Antarctica"}
# Big countries keep the old degree check; every other country's stations
# must be within GRID_KM of its cty.dat centre (measured 2026-11-11 on all
# recordings: real ones <= ~1000 km; the a2 ghost 'KR1TTR 9K2SHA KL70' was
# 1,600 km from Kuwait). 0 = old degree check for everyone.
_BIG = {"Mexico", "Chile", "India", "Kazakhstan", "Japan", "Saudi Arabia", "Iran", "Algeria",
        "Libya", "Sudan", "Dem. Rep. of the Congo", "South Africa", "Mongolia", "Peru", "Colombia",
        "Bolivia", "Greenland", "Egypt", "Angola", "Philippines", "Alaska", "New Zealand", "Norway",
        "Pakistan", "Niger", "Mali", "Chad", "Mauritania", "Ethiopia", "Madagascar",
        "Venezuela", "Asiatic Turkey", "Ukraine", "Thailand", "Vietnam", "Afghanistan", "Nigeria",
        "Tanzania", "Kenya", "Mozambique", "Namibia", "Botswana", "Zambia", "Somalia", "Turkmenistan",
        "Uzbekistan", "Myanmar", "Iraq", "East Malaysia",
        "Ecuador", "Paraguay", "Cameroon", "Central African Republic", "Republic of the Congo",
        "Gabon", "Cote d'Ivoire", "Ghana", "Burkina Faso", "Senegal", "Guinea", "Tunisia",
        "Morocco", "Western Sahara", "Oman", "Yemen", "Laos", "Cambodia", "Papua New Guinea",
        "Kyrgyzstan", "Tajikistan", 
        "South Sudan", "Uganda", "Zimbabwe", "Malawi", "Eritrea", "Djibouti", "Liberia",
        "Sierra Leone", "Guyana", "Suriname", "French Guiana"}
GRID_KM = 1200.0


def _grid_fits(info, g, tol):
    """Does grid g lie where a station of country info can be?"""
    lat = (ord(g[1]) - 65) * 10 - 90 + int(g[3]) + 0.5
    lon = (ord(g[0]) - 65) * 20 - 180 + int(g[2]) * 2 + 1
    if info["country"] in _WIDE:
        tol = 40
    elif GRID_KM and info["country"] not in _BIG:
        a, b, c, d = map(np.radians, (lat, lon, info["lat"], info["lon"]))
        cosang = np.sin(a) * np.sin(c) + np.cos(a) * np.cos(c) * np.cos(b - d)
        return 6371 * np.arccos(np.clip(cosang, -1, 1)) <= GRID_KM
    dlon = abs((lon - info["lon"] + 180) % 360 - 180)
    return abs(lat - info["lat"]) <= tol and dlon <= tol * 1.5


_STD_CALL = re.compile(r"^[A-Z0-9]{1,2}[0-9][A-Z]{1,3}$")
_GRID = re.compile(r"^[A-R]{2}[0-9]{2}$")
_REPORT = re.compile(r"^R?[+-][0-9]{2}$")
_CQ_MOD = re.compile(r"^([A-Z]{1,4}|[0-9]{3})$")        # CQ DX, CQ POTA, CQ 123
_WORDS = {"CQ", "DE", "QRZ", "R", "RR73", "RRR", "73"}


def plausible(text):
    """False for guessed decodes whose free part doesn't look like a real
    station: unknown country, odd call shape, or a grid far from the call's
    country. (A real CQ or caller passes; noise usually doesn't.)"""
    w = text.split()
    if not w:
        return False
    if w[0] == "CQ":
        w = w[1:]
    else:
        w = w[1:]                                  # drop my call
    if not w:
        return False
    call = w[0]
    if call.startswith("<"):
        return False                               # a hash we can't check
    if not _STD_CALL.match(call):
        return False
    info = _country(call)
    if not info:
        return False
    rest = w[1:]
    if rest and rest[0] == "R" and len(rest) > 1 and _GRID.match(rest[1]):
        return False                               # "R grid" = contest only
    g = next((x for x in rest if _GRID.match(x) and x != "RR73"), None)
    if g:
        if not _grid_fits(info, g, 12):
            return False
    return True


_CALL_LIKE = re.compile(r"^[A-Z0-9]{1,3}[0-9][A-Z0-9]{0,3}[A-Z](/[A-Z0-9]{1,4})?$")


def osd_sane(text, bits):
    """OSD (the deep search) can find a 'message' in a crowded band's mess.
    Its decodes must look like ordinary on-air FT8: no rare contest formats,
    known countries, and a grid that fits the call that sent it."""
    i3 = int("".join(map(str, bits[74:77])), 2)
    n3 = int("".join(map(str, bits[71:74])), 2)
    if i3 in (3, 5) or (i3 == 0 and n3 in (0, 3, 4)):
        return False                      # free text, RTTY roundup, EU VHF, Field Day
    w = text.split()
    if text.count("<...>") >= 2:
        return False
    if len(w) >= 2 and w[-2] == "R" and _GRID.match(w[-1]):
        return False                      # "R grid" = contest exchange
    # every word must be something FT8 really sends - not "5KUSA0APHUX"
    for k, x in enumerate(w):
        if (x in _WORDS or _REPORT.match(x) or _GRID.match(x) or _CALL_LIKE.match(x)
                or ("/" in x and any(_CALL_LIKE.match(p) for p in x.split("/")))   # P4/WE9V
                or (x.startswith("<") and x.endswith(">"))
                or (k == 1 and w[0] == "CQ" and _CQ_MOD.match(x))):
            continue
        return False
    calls = [x for x in w if x not in ("CQ", "DE", "QRZ") and not x.startswith("<")
             and _CALL_LIKE.match(x) and not _GRID.match(x)]
    for c in calls:
        if not _country(c.split("/")[0] if "/" in c and len(c.split("/")[0]) > 2 else c):
            return False
    g = w[-1] if w and _GRID.match(w[-1]) and w[-1] != "RR73" else None
    if g and calls:
        info = _country(calls[-1].split("/")[0])
        if not _grid_fits(info, g, 15):
            return False
    return True


def _hard_errors(llr, cw):
    return int(((llr > 0).astype(np.uint8) != cw).sum())


def ap_stage(cands, tunes, llrs, fails, got, ap, pats=None, nsyms=None, noise_sync=NOISE_SYNC, sync_min=None,
             full_max=None):
    """Try the guesses on every candidate the normal decoder couldn't read.
    (FT4 passes its own patterns, soft-bit methods and noise level.)"""
    if pats is None:
        pats = ap_patterns(ap.get("mycall", ""), ap.get("dxcall", ""))
    nsyms = nsyms or D.NSYMS
    dxfreq = ap.get("dxfreq")
    todo = [i for i in range(len(cands))
            if got[i] is None and tunes[i][0] >= (sync_min or AP_SYNC_MIN) * noise_sync]
    if not todo:
        return
    for label, idx, val, cw in pats:
        near = [i for i in todo if got[i] is None and (
            label in ("a1", "a2") or dxfreq is None or abs(cands[i][2] - dxfreq) <= AP_FREQ_SPAN)]
        if not near:
            continue
        if cw is not None:                   # whole message known: does it match?
            for i in near:
                best = min(nsyms, key=lambda n: _hard_errors(llrs[i, n], cw))
                h = _hard_errors(llrs[i, best], cw)
                if h <= (full_max or AP_FULL_MAX_HARD):
                    got[i] = (cw, label)
                    AP_LOG.append((label, h))
            continue
        # one soft-bit method per candidate: the one plain BP got closest with
        jobs = [(i, n) for i in near for n in nsyms]
        L0 = np.array([llrs[j] for j in jobs])
        L = L0.copy()
        mag = 1.1 * np.abs(L).max(1, keepdims=True)
        L[:, idx] = (2 * np.array(val) - 1) * mag
        bits, ok, _ = D.bp_many(L)
        done = set()
        left = []
        for j, (i, n) in enumerate(jobs):
            if i in done or got[i] is not None:
                continue
            if ok[j] and D.crc_ok(bits[j]) and _hard_errors(L0[j], bits[j]) <= AP_MAX_HARD:
                got[i] = (bits[j], label)
                done.add(i)
                AP_LOG.append((label, _hard_errors(L0[j], bits[j])))
            else:
                left.append(j)
        left = [j for j in left if jobs[j][0] not in done]
        if left:
            res = D.osd_many(L[left])
            for j, b in zip(left, res):
                i = jobs[j][0]
                if b is not None and got[i] is None and _hard_errors(L0[j], b) <= AP_MAX_HARD:
                    got[i] = (b, label)
                    AP_LOG.append((label + "osd", _hard_errors(L0[j], b)))


QRM_W = 4                       # (2026-11-11: kiwi2 93.62 -> 94.52% with QRM_MIN_SYNC 3, +0.09 s) background removal second try (+- symbols), 0 = off
QRM_MIN_SYNC = 3.0              # (2: 94.62% +0.16 s; 4: 94.25%) ... only for candidates syncing at least this well (x noise)


def debg(amps, w):
    """Each tone's running median over +-w symbols taken out of the readings
    (a carrier parked on one tone; a real FT8 signal hops between 8)."""
    p = np.abs(amps)
    pad = np.pad(p, ((w, w), (0, 0)), mode="edge")
    bg = np.median(np.lib.stride_tricks.sliding_window_view(pad, 2 * w + 1, axis=0), axis=-1)
    return amps * (np.maximum(p - bg, 0.05 * p) / np.maximum(p, 1e-12))


def _read(amps, tunes, osd):
    """Error-correct the tone readings: BP with each soft-bit method, then OSD."""
    B = len(amps)
    got = [None] * B
    llrs, fails, zsums = {}, {}, {}
    for nsym in D.NSYMS:
        todo = [i for i in range(B) if got[i] is None]
        if not todo:
            break
        L = D.soft_bits_many(amps[todo], nsym)
        bits, ok, few = D.bp_many(L)
        Z = D.last_zsum
        for j, i in enumerate(todo):
            llrs[i, nsym] = L[j]
            zsums[i, nsym] = Z[j]
            fails[i, nsym] = few[j]
            if ok[j] and D.crc_ok(bits[j]) and bits[j][:77].any():
                got[i] = (bits[j], f"bp{nsym}")
    if osd:
        # every OSD try in one batch: (candidate, soft-bit method)
        jobs = [(i, nsym) for i in range(B)
                if got[i] is None and tunes[i][0] >= OSD_MIN_SYNC * NOISE_SYNC
                for nsym in sorted(D.NSYMS, key=lambda n: fails[i, n])[:OSD_TRIES]]
        if OSD_ON_ZSUM == "both":
            # the summed-belief tries, for each candidate's closest few methods
            per = {}
            for j in jobs:
                per.setdefault(j[0], []).append(j)
            jobs = jobs + [("z",) + j for js in per.values() for j in js[:OSD_ZSUM_TRIES]]
        if jobs:
            src = lambda j: zsums[j[1:]] if j[0] == "z" else (zsums[j] if OSD_ON_ZSUM == "zsum" else llrs[j])
            res = D.osd_many(np.array([src(j) for j in jobs]))
            jobs = [j[1:] if j[0] == "z" else j for j in jobs]
            for (i, nsym), b in zip(jobs, res):
                if b is not None and got[i] is None:
                    got[i] = (b, f"osd{nsym}")
    return got, llrs, fails


def listen(spec, cands, osd=True):
    """The per-signal half of a pass, for a list of candidates: tune in,
    read the tones, error-correct (BP, then OSD). Each candidate's result
    depends only on itself, so the list can be split between processes
    (decpool.py) and the answers are the same."""
    tunes, amps = [], []
    for c in cands:
        sync, t_start, f = c[:3]
        x = downsample(spec, f)
        if len(c) > 3:                  # a known station's last spot
            sc, i0, df = fine_tune(x, 0.5 + t_start, SEED_SPAN, SEED_DF)
        else:
            sc, i0, df = fine_tune(x, 0.5 + t_start)
        tunes.append((sc, i0, df))
        amps.append(tone_amps2(x, i0, df))
    amps = np.array(amps)
    got, llrs, fails = _read(amps, tunes, osd)
    if QRM_W:
        # second try with each tone's steady background taken out (ft4search.debg)
        redo = [i for i in range(len(cands)) if got[i] is None and tunes[i][0] >= QRM_MIN_SYNC * NOISE_SYNC]
        if redo:
            g2, _, _ = _read(np.array([debg(amps[i], QRM_W) for i in redo]), [tunes[i] for i in redo], osd)
            for j, i in enumerate(redo):
                if g2[j] is not None:
                    got[i] = (g2[j][0], g2[j][1] + "q")
    if SEQ_RETRY:
        redo = []
        for i, c in enumerate(cands):
            if got[i] is not None or len(c) > 3 or tunes[i][0] < SEQ_MIN_SYNC * NOISE_SYNC:
                continue
            x = downsample(spec, c[2])
            t2 = seq_tune(x, 0.5 + c[1])
            if (t2[1], t2[2]) != (tunes[i][1], tunes[i][2]):
                redo.append((i, t2, tone_amps2(x, t2[1], t2[2])))
        if redo:
            g2, l2, f2 = _read(np.array([r[2] for r in redo]), [r[1] for r in redo], osd)
            for j, (i, t2, a2) in enumerate(redo):
                if g2[j] is not None:
                    b, how = g2[j]
                    got[i] = (b, how + "s")
                    tunes[i] = t2
                    amps[i] = a2
    return {"spec": spec, "cands": cands, "tunes": tunes, "amps": amps, "got": got,
            "llrs": llrs, "fails": fails}


def guess(st, ap):
    """A priori guesses on the candidates of a listen() result (in place)."""
    ap_stage(st["cands"], st["tunes"], st["llrs"], st["fails"], st["got"], ap)
    if KNOWN_AP and ap.get("known"):
        known_stage(st, ap["known"])


def known_patterns(call, last):
    """Guesses for a known station (call) whose last message was last."""
    pats = []

    def part(msg, idx, label):
        try:
            b = D.pack77.pack77(msg)
        except D.pack77.BadMessage:
            return
        if b[74:] != "001":
            return
        idx = list(idx) + [74, 75, 76]
        pats.append((label, idx, [int(b[i]) for i in idx], None))

    def whole(msg, label):
        try:
            b = D.pack77.pack77(msg)
        except D.pack77.BadMessage:
            return
        pats.append((label, None, None, np.array(D.fec.codeword([int(c) for c in b]), np.uint8)))

    w = last.split()
    if "k" in KNOWN_AP and _STD_CALL.match(call):
        part(f"K1ABC {call} FN42", range(29, 58), "k1")
    partner = w[0] if w and w[0] != "CQ" and len(w) >= 2 and w[1] == call else None
    if "p" in KNOWN_AP and partner and _STD_CALL.match(call) and _STD_CALL.match(partner):
        part(f"{partner} {call} FN42", range(58), "k2")
    if "r" in KNOWN_AP:
        whole(last, "k3")
        if partner and len(w) >= 3 and (w[2].startswith("R") or w[2] in ("RRR", "RR73")):
            whole(f"{partner} {call} 73", "k3")
    return pats


def known_stage(st, known):
    """Guesses on the seeded spots (known stations) nobody could read."""
    cands = st["cands"]
    for call, last in known.items():
        idx = [i for i, c in enumerate(cands) if len(c) > 3 and c[3] == call and st["got"][i] is None]
        if not idx:
            continue
        sub = [cands[i] for i in idx]
        got = [None] * len(idx)
        llrs = {(j, n): st["llrs"][i, n] for j, i in enumerate(idx) for n in D.NSYMS}
        ap_stage(sub, [st["tunes"][i] for i in idx], llrs, None, got, {},
                 pats=known_patterns(call, last), full_max=KNOWN_FULL_MAX_HARD)
        for j, i in enumerate(idx):
            if got[j] is not None:
                st["got"][i] = got[j]


def locate(st, i, n_audio):
    """Exact start time, frequency offset and full-rate rebuild of decoded
    candidate i, for subtracting it."""
    from . import ft8subtract as X
    sync, t_start, f = st["cands"][i][:3]
    sc, i0, df = st["tunes"][i]
    tones = X.tones_of(st["got"][i][0])
    t0, dfx = X.refine(downsample(st["spec"], f), tones, i0, df)
    return t0, dfx, X.reference(tones, f + dfx, t0, n_audio)


def sender(text):
    """The call that sent a message (None if unknown/hashed)."""
    w = text.split()
    if len(w) < 2:
        return None
    if w[0] == "CQ":
        c = w[2] if len(w) > 3 and _CQ_MOD.match(w[1]) and not _GRID.match(w[2]) else w[1]
    else:
        c = w[1]
    if c == "<...>" or not (_CALL_LIKE.match(c.strip("<>")) or "/" in c):
        return None
    return c


def decode_cycle(audio, hashes=None, osd=True, passes=None, keep_residual=False, ap=None, pool=None,
                 known=None, on_decodes=None):
    """Decode a whole cycle. Each pass finds candidates, decodes them, then
    subtracts every new decode from the audio so the next pass can hear
    what was underneath. pool: a decpool.Pool to share the work between
    processes (None = the default pool, decpool.default()).
    on_decodes(list): STREAMING - called with each batch of new decodes the
    moment it's ready (each pass's normal decodes, then its guesses), so
    the caller can show/answer strong stations before the later passes
    finish. The returned list is the same as without it."""
    from . import decpool
    from . import ft8subtract as X
    pool = pool or decpool.default()
    passes = passes or PASSES
    audio = np.array(audio, dtype=float)
    A = None                                   # analytic audio, made at the first subtraction
    removed = []                               # every subtracted signal, for re-estimating
    found = {}
    ncands = 0
    for p in range(1, passes + 1):
        wf = waterfall(audio)
        cands = candidates(wf)
        if SNR_FROM_WF:
            swf = snr_waterfall(audio)
            wf_base = noise_base(swf)
        else:
            wf_base = None
        if known and p <= SEED_PASSES:
            heard = {sender(t) for t in found}
            cands = cands + [(0.0, k["dt"], k["freq"], k["call"]) for k in known
                             if k["call"] not in heard]
        ncands += len(cands)
        if not cands:
            break
        spec = spectrum(audio)
        tunes, amps, got = pool.listen(__name__, spec, cands, osd)
        # Guesses only on the last pass: by then the strong signals are
        # subtracted, and the weak ones the guesses are for stand out best.
        last = p == passes or not any(g is not None and g[1][:1] != "a" and
                                      D.pack77.unpack77("".join(map(str, g[0][:77])), hashes) not in found
                                      for g in got)

        def accept(got, which):
            """Check + unpack the readings of candidates `which` -> indices of new decodes."""
            out = []
            for i in which:
                c = cands[i]
                sync, t_start, f = c[:3]
                if got[i] is None:
                    continue
                bits, how = got[i]
                sc, i0, df = tunes[i]
                text = D.pack77.unpack77("".join(map(str, bits[:77])), hashes)
                if not text.strip() or text in found:      # blank free text = noise
                    continue
                trusted = KNOWN_TRUST > 0 and known and any(
                    k["call"] == sender(text) and abs(k["freq"] - (f + tunes[i][2])) <= KNOWN_TRUST for k in known)
                if trusted and (how[:1] == "a" or how.startswith("osd")):
                    TRUSTED.append((how, text))
                elif how[:1] == "a" and how[:2] in ("a1", "a2", "a3") and not plausible(text):
                    continue
                if how[:1] == "k" and (sender(text) != c[3] or text.startswith("<") or not osd_sane(text, bits)):
                    continue
                if how.startswith("osd") and not trusted and not osd_sane(text, bits):
                    OSD_REJECTED.append(text)
                    continue
                snr = None
                if wf_base is not None:
                    snr = wf_snr(swf, *wf_base, i0 / FS2, f + df, X.tones_of(list(bits)))
                found[text] = {"text": text, "snr": snr_estimate(amps[i]) if snr is None else snr,
                               "dt": round(i0 / FS2 - 0.5, 2),
                               "freq": round(f + df), "fx": f + df, "dtx": i0 / FS2 - 0.5,
                               "sync": round(sync, 1), "how": how, "pass": p, "seed": len(c) > 3,
                               "ap": how if how[:1] in "ak" else ""}
                out.append(i)
            return out

        heard_now = list(got)                      # guess() fills the empty ones in place
        new = accept(heard_now, range(len(cands)))
        if on_decodes and new:                     # STREAMING: hand them out before guessing/subtracting
            on_decodes(list(found.values())[-len(new):])
        if ap is not None and (last or AP_EVERY_PASS):
            if KNOWN_AP and known:
                ap = dict(ap, known={k["call"]: k["msg"] for k in known})
            got = pool.guess(ap)
            extra = accept(got, [i for i in range(len(cands)) if heard_now[i] is None])
            if on_decodes and extra:
                on_decodes(list(found.values())[-len(extra):])
            new = sorted(new + extra)
        if not new:
            break
        if p < passes or keep_residual or NEAR_RETRY:
            if A is None:
                A = X.analytic(audio)
            for i, (t0, dfx, ref) in zip(new, pool.locate(new, len(A))):
                item = X.subtract(A, None, None, None, ref)
                if item is not None:
                    removed.append(item)
            if RESWEEPS:
                X.reestimate(A, removed, RESWEEPS)
            audio = A.real.copy()
            # JTDX's trick: candidates that failed next to a station just
            # subtracted get read again from the cleaned audio, same pass.
            for _ in range(NEAR_RETRY):
                fnew = [cands[i][2] for i in new]
                failed = [c for i, c in enumerate(cands) if heard_now[i] is None and got[i] is None
                          and any(abs(c[2] - f) <= NEAR_HZ for f in fnew)]
                if not failed:
                    break
                cands = failed
                tunes, amps, got = pool.listen(__name__, spectrum(audio), cands, osd)
                heard_now = list(got)
                new = accept(heard_now, range(len(cands)))
                for d in list(found.values())[-len(new):] if new else []:
                    d["how"] += "n"
                if on_decodes and new:
                    on_decodes(list(found.values())[-len(new):])
                if not new:
                    break
                for i, (t0, dfx, ref) in zip(new, pool.locate(new, len(A))):
                    item = X.subtract(A, None, None, None, ref)
                    if item is not None:
                        removed.append(item)
                audio = A.real.copy()
    if keep_residual:
        return list(found.values()), ncands, audio
    return list(found.values()), ncands

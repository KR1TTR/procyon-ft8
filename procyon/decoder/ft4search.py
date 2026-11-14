"""Procyon's own FT4 decoder: the FT8 recipe, adapted to FT4's signal.

    decode_cycle(audio, hashes=None, ap=None) -> (list of decodes, n candidates)

FT4 differs from FT8 only in how the bits are put on the air:
  * 4 tones (not 8), 20.8 Hz apart, 2 bits per symbol, 48 ms symbols
  * 103 symbols: four DIFFERENT 4x4 Costas sync arrays (symbols 0, 33, 66,
    99) around three blocks of 29 data symbols
  * the 77 message bits are scrambled (XOR with RVEC) before the CRC and
    LDPC parity are added, and the transmission has a ramp symbol at each end
  * 7.5 s cycles
The error correction (LDPC 174,91 + CRC-14), the batched BP/OSD, the
subtraction and the a priori guesses are FT8's (ft8decode / ft8search).

No WSJT-X code; follows the published FT4 description and Procyon's own
encoder (procyon/ft4.py).
"""

import numpy as np

from . import ft8decode as D
from . import ft8search as S
from . import ft8subtract as X
from .. import fec
from ..ft4 import COSTAS as _COSTAS4, GRAYMAP as _GRAY4, RVEC as _RVEC

FS = 12000
NSPS = 576                       # 48 ms per symbol
NSYM = 103
NMAX = 90000                     # one 7.5 s cycle
BAUD = FS / NSPS                 # 20.833 Hz
STEP = NSPS // 4                 # coarse time step: 12 ms
NFFT1 = 2 * NSPS                 # coarse frequency step: 10.4 Hz (half a tone)
DF1 = FS / NFFT1
NSPS2 = 16                       # narrow-band copy: 16 samples per symbol
FS2 = FS * NSPS2 / NSPS          # = 333.3 samples/s
NDOWN = NMAX * NSPS2 // NSPS     # 2500
SYNC_AT = (0, 33, 66, 99)
COSTAS = np.array(_COSTAS4)      # 4 arrays x 4 tones
DATA_SYMS = [i for i in range(NSYM) if not any(s <= i < s + 4 for s in SYNC_AT)]   # 87
RVEC = np.array(_RVEC, np.uint8)
TONE_BITS = np.zeros((4, 2), np.uint8)
for _v, _t in enumerate(_GRAY4):
    TONE_BITS[_t] = ((_v >> 1) & 1, _v & 1)

NOISE_SYNC = 4 * np.sqrt(4 * NSPS2) * np.sqrt(np.pi) / 2
NSYMS = (1, 2, 4)                # soft-bit methods (symbols listened to together)
OSD_MIN_SYNC = 4.0              # measured: real OSD wins >= 4.7, noise <= 3.4 (x NOISE_SYNC)
AP_SYNC_MIN = 4.0
OSD_TRIES = 3
OSD_ZSUM_TRIES = 3               # (2026-11-11 FT4: 1 -> +0.42%, 3 -> +0.6%) OSD also on BP's summed beliefs, this many methods per candidate
FMIN, FMAX = 100, 3300
SYNC_MIN = 1.25
LAGS_PER_FREQ = 3
MAX_CANDIDATES = 400
T0_MIN, T0_MAX = -0.45, 2.2      # where symbol 0 may start (s from the cycle start)
SNR_OFFSET = -26.0 + 10 * np.log10(BAUD / 6.25)
SNR_FROM_WF = True               # SNR from the waterfall (see ft8search.wf_snr)
SNR_CAL = 1.0                    # measured on the recordings vs jt9 (2026-11-11)
SMOOTH = 4000                    # subtraction: strength/phase tracking window (samples)


# ------------------------------------------------------------------ find candidates

def waterfall(audio):
    nframes = NMAX // STEP - 3
    a = np.zeros(NMAX + NSPS)
    a[:min(len(audio), NMAX)] = audio[:NMAX]
    win = np.zeros((nframes, NFFT1))
    idx = np.arange(NSPS)[None, :] + STEP * np.arange(nframes)[:, None]
    win[:, :NSPS] = a[idx]
    return (np.abs(np.fft.rfft(win, axis=1)) ** 2).T


def candidates(s):
    """(sync, start of symbol 0 in s, freq of the lowest tone)."""
    nf, nt = s.shape
    i0, i1 = int(FMIN / DF1), min(int(FMAX / DF1), nf - 8)
    ni = i1 - i0
    lags = np.arange(int(T0_MIN * FS / STEP), int(T0_MAX * FS / STEP) + 1)
    band = np.zeros((ni, nt))
    for m in range(4):
        band += s[i0 + 2 * m:i1 + 2 * m]
    scores = np.zeros((4, ni, len(lags)))
    for a, sym0 in enumerate(SYNC_AT):
        want = np.zeros((ni, len(lags)))
        tot = np.zeros((ni, len(lags)))
        for k in range(4):
            t = lags + 4 * (sym0 + k)
            ok = (t >= 0) & (t < nt)
            tt = np.clip(t, 0, nt - 1)
            want += s[i0 + 2 * COSTAS[a, k]:i1 + 2 * COSTAS[a, k]][:, tt] * ok
            tot += band[:, tt] * ok
        scores[a] = np.where(tot > 0, want / np.maximum((tot - want) / 3, 1e-12), 0)
    # all four arrays (FT4's 5 s signal fits the 7.5 s cycle, so no edge cases)
    sync = scores.sum(0)
    base = np.percentile(sync.max(1), 40)
    sync = sync / base if base > 0 else sync
    # the few best start times per frequency: a partial match of the arrays
    # can beat the true start of a weak signal, so the fine search decides
    out = []
    for n in range(ni):
        row = sync[n].copy()
        for _ in range(LAGS_PER_FREQ):
            j = int(row.argmax())
            if row[j] < SYNC_MIN:
                break
            out.append((float(row[j]), lags[j] * STEP / FS, (i0 + n) * DF1, n))
            row[max(0, j - 8):j + 9] = 0
    # a strong signal shows up in neighbouring frequency cells too: keep each
    # spot only where it's at least as good as the cells beside it
    best = {}
    for c in out:
        best[c[3], round(c[1] * FS / STEP)] = c[0]
    keep = [c for c in out if all(best.get((c[3] + d, round(c[1] * FS / STEP) + e), 0) <= c[0]
                                  for d in (-1, 1) for e in (-1, 0, 1))]
    keep.sort(key=lambda c: -c[0])
    return [c[:3] for c in keep[:MAX_CANDIDATES]]


# ------------------------------------------------------------------ listen

def spectrum(audio):
    a = np.zeros(NMAX)
    a[:min(len(audio), NMAX)] = audio[:NMAX]
    return np.fft.fft(a)


def downsample(spec, f0):
    df = FS / NMAX
    lo, hi = f0 - 1.5 * BAUD, f0 + 4.5 * BAUD
    i_lo, i_hi = int(round(lo / df)), int(round(hi / df))
    i0 = int(round(f0 / df))
    band = spec[i_lo:i_hi + 1].copy()
    ntap = int(round(BAUD / df))
    taper = 0.5 * (1 - np.cos(np.pi * np.arange(ntap) / ntap))
    band[:ntap] *= taper
    band[-ntap:] *= taper[::-1]
    out = np.zeros(NDOWN, complex)
    k = np.arange(i_lo, i_hi + 1) - i0
    out[k % NDOWN] = band
    x = np.fft.ifft(out)
    return x / (np.sqrt(np.mean(np.abs(x) ** 2)) + 1e-12)


_n = np.arange(NSPS2)
_TONE = np.exp(2j * np.pi * np.outer(np.arange(4), _n) / NSPS2)
_SYNC_SYMS = np.array([s0 + k for s0 in SYNC_AT for k in range(4)])          # 16
_SYNC_REF = _TONE[COSTAS.reshape(-1)].conj()                                  # 16 x 16
_DFS = np.arange(-6.0, 6.01, 0.5)
# time of every sample of the 16 sync symbols, from the start of symbol 0
_TS = (_SYNC_SYMS[:, None] * NSPS2 + _n[None, :]) / FS2                     # 16 x 16
_ROTF = np.exp(-2j * np.pi * _DFS[:, None, None] * _TS[None])                # df x 16 x 16


def sync_scores(x, starts):
    """Coherent sync score for many starts x frequency shifts: each 4-symbol
    Costas array is added up with its phase (FT4's tones have whole cycles
    per symbol, so the phase runs on smoothly), then the four sizes summed."""
    idx = starts[:, None, None] + _SYNC_SYMS[None, :, None] * NSPS2 + _n[None, None, :]
    ok = (idx >= 0) & (idx < len(x))
    seg = np.where(ok, x[np.clip(idx, 0, len(x) - 1)], 0) * _SYNC_REF[None]    # starts x 16 x 16
    per_sym = np.einsum("ism,dsm->dis", seg, _ROTF)                             # df x starts x 16
    return np.abs(per_sym.reshape(len(_DFS), len(starts), 4, 4).sum(-1)).sum(-1)


def fine_tune(x, t0, span=6, dfmax=None):
    """(score, sample of symbol 0 in the copy, frequency shift in Hz)."""
    i_guess = int(round(t0 * FS2))
    starts = np.arange(i_guess - span, i_guess + span + 1)
    score = sync_scores(x, starts)
    if dfmax is not None:
        score = np.where(np.abs(_DFS)[:, None] <= dfmax + 1e-9, score, -1)
    d, i = np.unravel_index(np.argmax(score), score.shape)
    return float(score[d, i]), int(starts[i]), float(_DFS[d])


def tone_amps(x, i0, df):
    t = np.arange(len(x)) / FS2
    y = x * np.exp(-2j * np.pi * df * t)
    n = NSYM * NSPS2
    seg = np.zeros(n, complex)
    lo, hi = max(i0, 0), min(i0 + n, len(y))
    if hi > lo:
        seg[lo - i0:hi - i0] = y[lo:hi]
    return np.fft.fft(seg.reshape(NSYM, NSPS2), axis=1)[:, :4] / NSPS2


def snr_estimate(amps):
    p = np.abs(amps) ** 2
    sig = p.max(1)
    noise = (p.sum(1) - sig) / 3
    ratio = sig.mean() / max(noise.mean(), 1e-12) - 1
    if ratio <= 0.001:
        return -24
    return int(round(max(-24, 10 * np.log10(ratio) + SNR_OFFSET)))


# ------------------------------------------------------------------ soft bits

def _combo_bits(nsym):
    combos = np.array(np.meshgrid(*[range(4)] * nsym, indexing="ij")).reshape(nsym, -1).T
    bits = np.concatenate([TONE_BITS[combos[:, j]] for j in range(nsym)], axis=1)
    return combos, bits


_COMBOS = {n: _combo_bits(n) for n in (1, 2, 3, 4)}
_GROUPS = {n: [(g, min(n, blk + 29 - g)) for blk in (0, 29, 58) for g in range(blk, blk + 29, n)]
           for n in (1, 2, 3, 4)}


def soft_bits_many(amps, nsym):
    """(B, 103, 4) tone amplitudes -> (B, 174) LLRs, positive = probably 1."""
    data = amps[:, DATA_SYMS]                     # B x 87 x 4
    B = len(data)
    llr = np.zeros((B, 174))
    by_size = {}
    for g, n in _GROUPS[nsym]:
        by_size.setdefault(n, []).append(g)
    for n, starts in by_size.items():
        cmb, cb = _COMBOS[n]
        starts = np.array(starts)
        tot = np.zeros((B, len(starts), len(cmb)), complex)
        for j in range(n):
            tot += data[:, starts + j][:, :, cmb[:, j]]
        mag = np.abs(tot)
        for k in range(2 * n):
            one = cb[:, k] == 1
            llr[:, 2 * starts + k] = mag[:, :, one].max(2) - mag[:, :, ~one].max(2)
    sd = llr.std(1, keepdims=True)
    return llr / (sd + 1e-12) * 2.83


def message_text(cw, hashes=None):
    bits = (np.asarray(cw[:77], np.uint8) ^ RVEC)
    return D.pack77.unpack77("".join(map(str, bits)), hashes)


def tones_of(cw):
    data = [_GRAY4[2 * cw[2 * i] + cw[2 * i + 1]] for i in range(87)]
    c = [tuple(r) for r in COSTAS]
    return c[0] + tuple(data[:29]) + c[1] + tuple(data[29:58]) + c[2] + tuple(data[58:]) + c[3]


# ------------------------------------------------------------------ rebuild + subtract

_PULSES = {}


def wave(tones, fs):
    """Complex baseband FT4 transmission (lowest tone at 0 Hz): ramp symbol,
    103 tones, ramp symbol - like procyon/ft4.synth. Sample 0 = ramp start."""
    nsps = int(round(NSPS * fs / FS))
    if nsps not in _PULSES:
        from procyon.ft8 import _gfsk_pulse
        t = (np.arange(1, 3 * nsps + 1) - 1.5 * nsps) / nsps
        _PULSES[nsps] = _gfsk_pulse(1.0, t)
    pulse = _PULSES[nsps]
    n = (NSYM + 2) * nsps
    dphi = np.zeros(n)
    peak = 2 * np.pi / nsps
    for j, tone in enumerate(tones):
        dphi[j * nsps:j * nsps + 3 * nsps] += peak * pulse * tone
    phi = np.concatenate(([0.0], np.cumsum(dphi[:n - 1])))
    env = np.ones(n)
    r = np.arange(nsps)
    env[:nsps] = (1 - np.cos(np.pi * r / nsps)) / 2
    env[(NSYM + 1) * nsps:] = (1 + np.cos(np.pi * r / nsps)) / 2
    return env * np.exp(1j * phi)


def refine(x, tones, i0, df):
    """Exact start of the transmission (ramp, s from cycle start) and
    frequency offset, matched against the whole rebuilt signal."""
    ref = wave(tones, FS2)
    n = len(ref)
    s0 = i0 - NSPS2                               # ramp starts one symbol earlier
    X_ = np.fft.fft(x)
    fgrid = np.fft.fftfreq(len(x), 1 / FS2)
    tn = (s0 + np.arange(n)) / FS2

    def matched(tau):
        y = np.fft.ifft(X_ * np.exp(2j * np.pi * fgrid * tau))
        seg = np.zeros(n, complex)
        lo, hi = max(s0, 0), min(s0 + n, len(y))
        if hi > lo:
            seg[lo - s0:hi - s0] = y[lo:hi]
        return seg * ref.conj()

    def best_f(p, fs):
        sc = np.abs(np.exp(-2j * np.pi * np.outer(fs, tn)) @ p)
        j = int(np.argmax(sc))
        return float(fs[j])

    def best_t(taus, f):
        rot = np.exp(-2j * np.pi * f * tn)
        sc = [abs((matched(t) * rot).sum()) for t in taus]
        return float(taus[int(np.argmax(sc))])

    f = best_f(matched(0.0), df + np.arange(-1.0, 1.001, 0.05))
    tau = best_t(np.arange(-0.006, 0.00601, 0.001), f)
    f = best_f(matched(tau), f + np.arange(-0.1, 0.1001, 0.01))
    tau = best_t(tau + np.arange(-0.001, 0.00101, 0.0002), f)
    return s0 / FS2 + tau + 0.5 / FS2 - 0.5 / FS, f


def reference(tones, f0, t_start, n_audio):
    """(lo, hi, rebuilt transmission for A[lo:hi]) at full rate."""
    start = int(round(t_start * FS))
    frac = t_start * FS - start
    base = wave(tones, FS)
    n = len(base)
    ref = base * np.exp(2j * np.pi * f0 * (np.arange(n) - frac) / FS)
    lo, hi = max(start, 0), min(start + n, n_audio)
    return lo, hi, ref[lo - start:hi - start] if hi > lo else None


def subtract(A, tones, f0, t_start, ref=None):
    lo, hi, r = ref if ref is not None else reference(tones, f0, t_start, len(A))
    if r is None:
        return
    num = X._slow(A[lo:hi] * r.conj(), SMOOTH)
    den = X._slow((np.abs(r) ** 2).astype(complex), SMOOTH).real
    A[lo:hi] -= num / np.maximum(den, 1e-3) * r


# ------------------------------------------------------------------ a priori

SEED_PASSES = 9                  # known stations' last spots as extra candidates (see ft8search)
SEED_SPAN = 2                    # ... fine search +- samples (3 ms each)
SEED_DF = 2.0                    # ... +- Hz
KNOWN_AP = "kpr"                 # guesses at a known station's spot (letters as in ft8search)
PASSES = 3                       # decode + subtract rounds per cycle
NEAR_RETRY = 1                   # (2026-11-11 FT4: +0.27%, +0.03 s) JTDX-style re-read of failed candidates near a new decode (rounds)
NEAR_HZ = 50.0                   # ... within this many Hz


def ap_patterns(mycall="", dxcall=""):
    """FT8's guesses with FT4's scrambling applied to the known bits."""
    return _scramble(S.ap_patterns(mycall, dxcall))


def _scramble(pats):
    out = []
    for label, idx, val, cw in pats:
        if cw is None:
            idx = list(idx)
            val = [v ^ int(RVEC[i]) if i < 77 else v for i, v in zip(idx, val)]
            out.append((label, idx, val, None))
        else:
            b = (np.asarray(cw[:77], np.uint8) ^ RVEC).tolist()
            out.append((label, None, None, np.array(fec.codeword(b), np.uint8)))
    return out


# ------------------------------------------------------------------ all together

OSD_SANE = True                  # OSD decodes must look like ordinary FT4 (ft8search.osd_sane)
QRM_W = 4                        # background window (+- symbols) for the second try; 0 = off
QRM_MIN_SYNC = 4.0               # (2026-11-11: 4 = 94.22% at 0.39 s, 0 = 94.25% at 0.45 s) ... only for candidates syncing at least this well (x noise)


def debg(amps, w):
    """Take each tone's steady background (running median over +-w symbols)
    out of the tone readings: a real FT4 signal hops between its 4 tones,
    an interfering carrier or other mode sits on one for many symbols."""
    p = np.abs(amps)
    pad = np.pad(p, ((w, w), (0, 0)), mode="edge")
    bg = np.median(np.lib.stride_tricks.sliding_window_view(pad, 2 * w + 1, axis=0), axis=-1)
    return amps * (np.maximum(p - bg, 0.05 * p) / np.maximum(p, 1e-12))


def _read(amps, tunes, osd):
    """BP with each soft-bit method, then OSD -> (got, llrs, fails)."""
    B = len(amps)
    got = [None] * B
    llrs, fails, zsums = {}, {}, {}
    for nsym in NSYMS:
        todo = [i for i in range(B) if got[i] is None]
        if not todo:
            break
        L = soft_bits_many(amps[todo], nsym)
        bits, ok, few = D.bp_many(L)
        Z = D.last_zsum
        for j, i in enumerate(todo):
            llrs[i, nsym] = L[j]
            zsums[i, nsym] = Z[j]
            fails[i, nsym] = few[j]
            if ok[j] and D.crc_ok(bits[j]) and bits[j][:77].any():
                got[i] = (bits[j], f"bp{nsym}")
    if osd:
        jobs = [(i, nsym) for i in range(B)
                if got[i] is None and tunes[i][0] >= OSD_MIN_SYNC * NOISE_SYNC
                for nsym in sorted(NSYMS, key=lambda n: fails[i, n])[:OSD_TRIES]]
        if OSD_ZSUM_TRIES:
            # also BP's summed beliefs of its first rounds, for each candidate's
            # closest few methods (as in ft8search; WSJT-X does the same)
            per = {}
            for j in jobs:
                per.setdefault(j[0], []).append(j)
            jobs = jobs + [("z",) + j for js in per.values() for j in js[:OSD_ZSUM_TRIES]]
        if jobs:
            res = D.osd_many(np.array([zsums[j[1:]] if j[0] == "z" else llrs[j] for j in jobs]))
            for j, b in zip(jobs, res):
                i, nsym = j[1:] if j[0] == "z" else j
                if b is not None and got[i] is None:
                    got[i] = (b, f"osd{nsym}")
    return got, llrs, fails


def listen(spec, cands, osd=True):
    """Per-signal half of a pass (see ft8search.listen)."""
    tunes, amps = [], []
    for c in cands:
        sync, t0, f = c[:3]
        x = downsample(spec, f)
        if len(c) > 3:                   # a known station's last spot
            sc, i0, df = fine_tune(x, t0, SEED_SPAN, SEED_DF)
        else:
            sc, i0, df = fine_tune(x, t0)
        tunes.append((sc, i0, df))
        amps.append(tone_amps(x, i0, df))
    amps = np.array(amps)
    got, llrs, fails = _read(amps, tunes, osd)
    if QRM_W:
        # a second try with each tone's steady background taken out (a
        # carrier or other mode parked on one tone), for the ones that failed
        redo = [i for i in range(len(cands)) if got[i] is None and tunes[i][0] >= QRM_MIN_SYNC * NOISE_SYNC]
        if redo:
            g2, _, _ = _read(np.array([debg(amps[i], QRM_W) for i in redo]), [tunes[i] for i in redo], osd)
            for j, i in enumerate(redo):
                if g2[j] is not None:
                    got[i] = (g2[j][0], g2[j][1] + "q")
    return {"spec": spec, "cands": cands, "tunes": tunes, "amps": amps, "got": got,
            "llrs": llrs, "fails": fails}


def guess(st, ap):
    S.ap_stage(st["cands"], st["tunes"], st["llrs"], st["fails"], st["got"], ap,
               pats=ap_patterns(ap.get("mycall", ""), ap.get("dxcall", "")),
               nsyms=NSYMS, noise_sync=NOISE_SYNC, sync_min=AP_SYNC_MIN)
    if KNOWN_AP and ap.get("known"):
        known_stage(st, ap["known"])


def known_stage(st, known):
    """Guesses on the seeded spots of known stations nobody could read."""
    keep = S.KNOWN_AP
    S.KNOWN_AP = KNOWN_AP
    try:
        cands = st["cands"]
        for call, last in known.items():
            idx = [i for i, c in enumerate(cands) if len(c) > 3 and c[3] == call and st["got"][i] is None]
            if not idx:
                continue
            got = [None] * len(idx)
            llrs = {(j, n): st["llrs"][i, n] for j, i in enumerate(idx) for n in NSYMS}
            S.ap_stage([cands[i] for i in idx], [st["tunes"][i] for i in idx], llrs, None, got, {},
                       pats=_scramble(S.known_patterns(call, last)), nsyms=NSYMS,
                       noise_sync=NOISE_SYNC, sync_min=AP_SYNC_MIN, full_max=S.KNOWN_FULL_MAX_HARD)
            for j, i in enumerate(idx):
                if got[j] is not None:
                    st["got"][i] = got[j]
    finally:
        S.KNOWN_AP = keep


def locate(st, i, n_audio):
    """(start, freq offset, rebuilt signal) of decoded candidate i."""
    sync, t0, f = st["cands"][i][:3]
    sc, i0, df = st["tunes"][i]
    tones = tones_of(st["got"][i][0])
    t_start, dfx = refine(downsample(st["spec"], f), tones, i0, df)
    return t_start, dfx, reference(tones, f + dfx, t_start, n_audio)


def decode_cycle(audio, hashes=None, osd=True, passes=None, ap=None, pool=None, on_decodes=None, known=None):
    """known: [{call, freq, dt, msg}] stations decoded in earlier cycles of
    the same parity (see ft8search.decode_cycle) - seeds + k1-k3 guesses."""
    passes = passes or PASSES
    from . import decpool
    pool = pool or decpool.default()
    audio = np.array(audio, dtype=float)
    A = None
    found = {}
    ncands = 0
    for p in range(1, passes + 1):
        wf = waterfall(audio)
        cands = candidates(wf)
        if SNR_FROM_WF:
            swf = S.snr_waterfall(audio, NSPS, STEP, NMAX)
            wf_base = S.noise_base(swf)
        else:
            wf_base = None
        if known and p <= SEED_PASSES:
            heard = {S.sender(t) for t in found}
            cands = cands + [(0.0, k["dt"] + 0.5 + 1 / BAUD, k["freq"], k["call"]) for k in known
                             if k["call"] not in heard]
        ncands += len(cands)
        if not cands:
            break
        spec = spectrum(audio)
        tunes, amps, got = pool.listen(__name__, spec, cands, osd)
        last = p == passes or not any(g is not None and g[1][:1] != "a" and message_text(g[0], hashes) not in found
                                      for g in got)
        if ap is not None and KNOWN_AP and known:
            ap = dict(ap, known={k["call"]: k["msg"] for k in known})

        def accept(got, which):
            out = []
            for i in which:
                c = cands[i]
                sync, t0, f = c[:3]
                if got[i] is None:
                    continue
                cw, how = got[i]
                text = message_text(cw, hashes)
                if not text.strip() or text in found:
                    continue
                if how[:2] in ("a1", "a2", "a3") and not S.plausible(text):
                    continue
                if how[:1] == "k" and (len(c) < 4 or S.sender(text) != c[3] or text.startswith("<")):
                    continue
                if how.startswith("osd") and OSD_SANE and not S.osd_sane(text, np.asarray(cw[:77], np.uint8) ^ RVEC):
                    S.OSD_REJECTED.append(text)
                    continue
                sc, i0, df = tunes[i]
                snr = None
                if wf_base is not None:
                    snr = S.wf_snr(swf, *wf_base, i0 / FS2, f + df, tones_of(list(cw)), step=STEP, baud=BAUD,
                                   cal=SNR_CAL)
                found[text] = {"text": text, "snr": snr_estimate(amps[i]) if snr is None else snr,
                               "dt": round(i0 / FS2 - 1 / BAUD - 0.5, 2),
                               "freq": round(f + df), "fx": f + df, "dtx": i0 / FS2 - 1 / BAUD - 0.5,
                               "sync": round(sync, 1), "fsync": round(sc / NOISE_SYNC, 2),
                               "how": how, "pass": p, "seed": len(c) > 3, "ap": how if how[:1] in "ak" else ""}
                out.append(i)
            return out

        heard_now = list(got)
        new = accept(heard_now, range(len(cands)))
        if on_decodes and new:                     # STREAMING (see ft8search.decode_cycle)
            on_decodes(list(found.values())[-len(new):])
        if ap is not None and last:
            got = pool.guess(ap)
            extra = accept(got, [i for i in range(len(cands)) if heard_now[i] is None])
            if on_decodes and extra:
                on_decodes(list(found.values())[-len(extra):])
            new = sorted(new + extra)
        if not new or (p == passes and not NEAR_RETRY):
            break
        if A is None:
            A = X.analytic(audio)
        for i, (t_start, dfx, ref) in zip(new, pool.locate(new, len(A))):
            subtract(A, None, None, None, ref)
        audio = A.real.copy()
        # JTDX's trick (see ft8search NEAR_RETRY): failed candidates next to a
        # station just subtracted are read again from the cleaned audio.
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
            for i, (t_start, dfx, ref) in zip(new, pool.locate(new, len(A))):
                subtract(A, None, None, None, ref)
            audio = A.real.copy()
    return list(found.values()), ncands

"""Procyon's own JS8 decoder: audio of one slot -> the frames in it.

A port of JS8Call 2.2.0's decoder (lib/js8/js8dec.f90, syncjs8.f90,
syncjs8d.f90, js8_downsample.f90, subtractjs8.f90 and the FT8 code's
bpdecode174.f90 / osd174.f90 - GPLv3, Jordan Sherer KN4CRD, building on
WSJT-X by Joe Taylor K1JT and Steve Franke K9AN) to Python + numpy.

How it works, in plain words:
  1. Find signals: a picture of the slot (spectrum every quarter symbol), and
     at every audio frequency and start time, how strongly the three 7-tone
     sync patterns show up. The strongest spots are "candidates".
  2. For each candidate: shift it down to 0 Hz and thin out the samples
     (fewer numbers to work with), then line it up to a fraction of a symbol
     in time and a fraction of a Hz in frequency.
  3. Measure how strong each of the 8 tones is in each of the 58 data
     symbols, and turn that into "how sure are we that this bit is a 1".
  4. Repair: the (174,87) error-correcting code fixes wrong bits - first by
     "belief propagation" (the bits and checks vote, back and forth), and if
     that fails, by trying the most likely small corrections ("ordered
     statistics"). The 12-bit checksum then says whether we got it right.
  5. Take every decoded signal out of the audio and look again: weaker
     signals hidden under strong ones show up (up to 4 passes).

Differences from JS8Call, on purpose (both make it a little better, never
change what a decode means): the fine sync uses all three sync patterns
(JS8Call's fine search misses the last one), the frequency fine search is
scaled to each speed's tone spacing, subtraction corrects the fade at the
signal's ends, and the signal report is our own estimate (in the usual
2500 Hz bandwidth), not JS8Call's formula.

    from procyon.js8.js8dec import decode
    for d in decode(audio, "normal"):  # audio: one slot at 12 kHz
        print(d["snr"], d["dt"], d["freq"], d["frame"], d["flags"])
"""
import numpy as np

from . import js8enc
from . import js8ldpc
from . import js8msg

FS = js8enc.FS

# Decoder settings per speed, from JS8Call's js8{a,b,c,e}_params.f90:
# samples per symbol after thinning out, how much of the slot is searched
# (NDD), the time search range (JZ, quarter symbols), how close two
# candidates may be (AZ, x tone spacing), seconds of audio (NTXDUR).
PARAMS = {
    "normal": dict(ndownsps=32, ndd=100, jz=62,  az=0.64, ntxdur=15),
    "fast":   dict(ndownsps=20, ndd=100, jz=144, az=0.80, ntxdur=10),
    "turbo":  dict(ndownsps=12, ndd=120, jz=172, az=0.60, ntxdur=6),
    "slow":   dict(ndownsps=32, ndd=90,  jz=32,  az=0.64, ntxdur=28),
}
SYNC_MIN = 1.5          # candidates weaker than this (x the background) are ignored
MAX_CAND = 300
# fine frequency search: +-FINE_N steps of FINE_STEP x the tone spacing
JOINT = True
FINE_N, FINE_STEP = 5, 0.08      # = JS8Call's +-2.5 Hz in 0.5 Hz steps at Normal
NN, ND = 79, 58         # symbols in all, data symbols
# where the data symbols sit among the 79 (S7 D29 S7 D29 S7)
DATA_SYMBOLS = np.r_[7:36, 43:72]

# ---- the (174,87) code ------------------------------------------------------

_N, _K, _M = 174, 87, 87
_COLORDER = np.array(js8enc._COLORDER)
# generator in "codeword order" (87 parity bits, then the 87 message bits)
_GEN = np.hstack([js8enc._GEN.T % 2, np.eye(_K, dtype=np.uint8)]).astype(np.uint8)
# parity checks as a padded table: 87 checks x up to 7 bits (-1 = none)
_NM = np.full((_M, 7), -1)
for _i, _r in enumerate(js8ldpc.CHECKS):
    _NM[_i, :len(_r)] = _r
_NM_OK = _NM >= 0
_NM0 = np.where(_NM_OK, _NM, 0)
# for each bit, its 3 checks and its position in each check's row
_MN = [[] for _ in range(_N)]
for _j in range(_M):
    for _p, _b in enumerate(js8ldpc.CHECKS[_j]):
        _MN[_b].append((_j, _p))
_MN_CHK = np.array([[c for c, _ in m] for m in _MN])
_MN_POS = np.array([[p for _, p in m] for m in _MN])


def bp_decode(llr, max_iter=30):
    """Belief propagation (bpdecode174). llr: 174 values in transmitted
    order, > 0 means "probably a 1". Returns (codeword bits or None, number
    of bits that disagreed with the raw measurement)."""
    llr = np.asarray(llr, dtype=float)
    tov = np.zeros((_N, 3))                 # check -> bit messages
    nclast = ncnt = 0
    for it in range(max_iter + 1):
        zn = llr + tov.sum(1)
        cw = (zn > 0).astype(np.uint8)
        synd = (cw[_NM0] * _NM_OK).sum(1) % 2
        ncheck = int(synd.sum())
        if ncheck == 0:
            nerr = int(((2 * cw.astype(int) - 1) * llr < 0).sum())
            return cw, nerr
        if it > 0:                           # stop early if it isn't getting anywhere
            ncnt = 0 if ncheck < nclast else ncnt + 1
            if ncnt >= 5 and it >= 10 and ncheck > 15:
                return None, -1
        nclast = ncheck
        # bit -> check: what the bit believes, minus what that check told it
        toc = zn[_NM0]
        for k in range(3):
            toc[_MN_CHK[:, k], _MN_POS[:, k]] -= tov[:, k]
        t = np.where(_NM_OK, np.tanh(-toc / 2), 1.0)
        # product of the other bits in each check (prefix x suffix products)
        pre = np.cumprod(np.hstack([np.ones((_M, 1)), t[:, :-1]]), 1)
        suf = np.cumprod(np.hstack([t[:, :0:-1], np.ones((_M, 1))]), 1)[:, ::-1]
        others = pre * suf
        y = np.clip(-others[_MN_CHK, _MN_POS], -0.99999, 0.99999)
        tov = 2 * np.clip(np.arctanh(y), -7, 7)
    return None, -1


# all pairs of message positions, for 2-bit corrections in osd_decode
_PAIRS = np.array([(i, j) for i in range(_K) for j in range(i + 1, _K)])
OSD_TRIPLES = True
OSD_MIN_SYNC = 8       # sync tones (of 21) needed before the slow OSD step is tried
TAU = 14


def osd_decode(llr):
    """Ordered-statistics decoding (osd174): trust the 87 most reliable bits,
    rebuild the rest from them, and also try every 1- and 2-bit correction
    of the trusted bits. Returns (codeword bits in transmitted order, number
    of disagreeing bits, sum of their reliabilities) for the closest one."""
    rx = np.asarray(llr, dtype=float)[_COLORDER]          # codeword order
    hdec = (rx >= 0).astype(np.uint8)
    absrx = np.abs(rx)
    order = np.argsort(-absrx, kind="stable")             # most reliable first
    g = _GEN[:, order].copy()
    idx = order.copy()
    # Gaussian elimination: the first K columns become the identity, using the
    # most reliable columns that are independent
    for d in range(_K):
        cols = np.nonzero(g[d, d:])[0]
        if len(cols) == 0:
            continue
        c = d + cols[0]
        if c != d:
            g[:, [d, c]] = g[:, [c, d]]
            idx[[d, c]] = idx[[c, d]]
        rows = np.nonzero(g[:, d])[0]
        rows = rows[rows != d]
        g[rows] ^= g[d]
    hd = hdec[idx]
    ar = absrx[idx]
    m0 = hd[:_K]
    c0 = (m0 @ g) % 2
    pairs = g[_PAIRS[:, 0]] ^ g[_PAIRS[:, 1]]
    cands = [c0[None, :], c0 ^ g, c0 ^ pairs]
    if OSD_TRIPLES:
        # 3-bit corrections, found quickly (JS8Call's "npre2" rule): a pair is
        # only worth trying with bit i if it makes the first TAU rebuilt bits
        # agree with what we received (allowing one disagreement)
        w = 1 << np.arange(TAU)[::-1]
        ppat = pairs[:, _K:_K + TAU] @ w
        order_p = np.argsort(ppat, kind="stable")
        sorted_p = ppat[order_p]
        e1 = (c0 ^ g)[:, _K:_K + TAU] ^ hd[_K:_K + TAU]           # (K, TAU) after flipping bit i
        flips = np.vstack([np.zeros(TAU, dtype=np.uint8), np.eye(TAU, dtype=np.uint8)])
        targets = ((e1[:, None, :] ^ flips[None, :, :]) @ w)       # (K, TAU+1)
        lo = np.searchsorted(sorted_p, targets, "left")
        hi = np.searchsorted(sorted_p, targets, "right")
        # every (bit i, pair) match, in the same order the old loop made them
        lo, hi = lo.ravel(), hi.ravel()
        cnt = hi - lo
        if cnt.sum():
            ii = np.repeat(np.repeat(np.arange(_K), TAU + 1), cnt)
            start = np.repeat(lo - np.r_[0, np.cumsum(cnt)[:-1]], cnt)
            pi = order_p[start + np.arange(cnt.sum())]
            ok = (_PAIRS[pi, 0] != ii) & (_PAIRS[pi, 1] != ii)
            if ok.any():
                cands.append(c0 ^ g[ii[ok]] ^ pairs[pi[ok]])
    cands = np.vstack(cands).astype(np.uint8)
    diff = cands ^ hd
    dist = diff @ ar
    best = int(np.argmin(dist))
    cw_mrb = cands[best]
    cw = np.zeros(_N, dtype=np.uint8)
    cw[idx] = cw_mrb                                      # back to codeword order
    out = np.zeros(_N, dtype=np.uint8)
    out[_COLORDER] = cw                                   # back to transmitted order
    return out, int(diff[best].sum()), float(dist[best])


def message_bits(cw):
    """Transmitted-order codeword -> the 87 message bits."""
    return cw[_COLORDER][_M:]


def bits_to_frame(bits87):
    """87 message bits -> (frame, flags) if the checksum is right, else None."""
    b = [int(x) for x in bits87]
    if js8enc.crc12(b[:75]) != int("".join(map(str, b[75:87])), 2):
        return None
    frame = "".join(js8enc.ALPHABET[int("".join(map(str, b[6 * i:6 * i + 6])), 2)] for i in range(12))
    return frame, b[72] * 4 + b[73] * 2 + b[74]


# ---- signal processing -------------------------------------------------------

class _Speed:
    def __init__(self, speed):
        sp, p = js8enc.SPEEDS[speed], PARAMS[speed]
        self.name = speed
        self.nsps = sp["nsps"]
        self.astart = sp["start"]
        self.costas = js8enc.COSTAS[sp["costas"]]
        self.ndownsps = p["ndownsps"]
        self.ndown = self.nsps // self.ndownsps
        self.nmax = p["ntxdur"] * FS
        self.nfft1 = 2 * self.nsps
        self.nstep = self.nsps // 4
        self.nhsym = self.nmax // self.nstep - 3
        self.jz = p["jz"]
        self.baud = FS / self.nsps
        self.az = self.baud * p["az"]
        self.ndfft1 = self.nsps * p["ndd"]
        self.ndfft2 = self.ndfft1 // self.ndown
        self.fs2 = FS / self.ndown
        self.nfilt = 1400 * self.nsps // 1920       # subtraction average (~0.12 s at Normal)
        # the sync tones as waveforms (for the fine search)
        n = np.arange(self.ndownsps)
        self.csync = [np.exp(2j * np.pi * np.outer(c, n) / self.ndownsps) for c in self.costas]


def _spectra(dd, S):
    """Power spectrum every quarter symbol (no window, zero-padded x2)."""
    idx = np.arange(S.nhsym)[:, None] * S.nstep + np.arange(S.nsps)[None, :]
    x = dd[idx] / 300.0
    return np.abs(np.fft.rfft(x, S.nfft1, axis=1)) ** 2       # (nhsym, nsps+1)


def find_candidates(dd, S, fmin, fmax):
    """syncjs8: [(freq, start time in s, sync strength), ...] sorted by freq."""
    s = _spectra(dd, S)
    df = FS / S.nfft1
    ia = max(1, int(round(fmin / df)))
    ib = int(round(fmax / df))
    nssy = S.nsps // S.nstep
    nfos = S.nfft1 // S.nsps
    jstrt = int(S.astart / (S.nstep / FS))
    js = np.arange(-S.jz, S.jz + 1)
    F = np.arange(ia, ib + 1)
    tt, t0 = [], []
    for a, offset in enumerate((0, 36, 72)):
        ta = np.zeros((len(js), len(F)))
        t0a = np.zeros_like(ta)
        for n in range(7):
            k = js + jstrt + nssy * (n + offset)
            ok = (k >= 0) & (k < S.nhsym)
            rows = s[np.clip(k, 0, S.nhsym - 1)] * ok[:, None]
            ta += rows[:, F + nfos * S.costas[a][n]]
            t0a += sum(rows[:, F + nfos * m] for m in range(7))
        tt.append(ta)
        t0.append(t0a)

    def ratio(parts):
        t = sum(tt[i] for i in parts)
        z = (sum(t0[i] for i in parts) - t) / 6.0
        return np.where(z > 0, t / np.where(z > 0, z, 1), 0)

    sync2d = np.maximum(np.maximum(ratio((0, 1, 2)), ratio((0, 1))), ratio((1, 2)))
    jpeak = np.argmax(sync2d, 0)
    red = sync2d[jpeak, np.arange(len(F))]
    order = np.argsort(red, kind="stable")
    npct = int(round(0.40 * len(F)))
    if npct < 1:
        return []
    base = red[order[npct - 1]]
    if base <= 0:
        return []
    red = red / base
    cands = []
    for i in order[::-1][:MAX_CAND]:
        if red[i] < SYNC_MIN or np.isnan(red[i]):
            break
        cands.append([F[i] * df, (js[jpeak[i]] + jstrt) * S.nstep / FS, red[i]])
    # keep only the strongest of candidates closer than az in frequency
    for i in range(len(cands)):
        for j in range(i):
            if abs(cands[i][0] - cands[j][0]) < S.az and cands[j][2] > 0:
                cands[i][2] = 0.0
                break
    return sorted([c for c in cands if c[2] >= SYNC_MIN], key=lambda c: c[0])


class _Downsampler:
    """js8_downsample: one long FFT of the slot, then for each candidate the
    band around it, shifted to 0 Hz, back to time at fs2 (ndownsps per symbol)."""

    def __init__(self, dd, S):
        self.S = S
        x = np.zeros(S.ndfft1)
        x[:len(dd)] = dd
        self.cx = np.fft.rfft(x)
        ndd = S.ndfft1 // S.nsps   # NDD
        self.taper = 0.5 * (1 + np.cos(np.arange(ndd + 1) * np.pi / ndd))

    def __call__(self, f0):
        S = self.S
        df = FS / S.ndfft1
        i0 = int(round(f0 / df))
        it = min(int(round((f0 + 8.5 * S.baud) / df)), S.ndfft1 // 2)
        ib = max(1, int(round((f0 - 1.5 * S.baud) / df)))
        c1 = np.zeros(S.ndfft2, dtype=complex)
        seg = self.cx[ib:it + 1].copy()
        ndd = len(self.taper) - 1
        seg[:ndd + 1] *= self.taper[::-1]
        seg[-(ndd + 1):] *= self.taper
        c1[:len(seg)] = seg
        c1 = np.roll(c1, -(i0 - ib))
        c1 = np.fft.ifft(c1) * S.ndfft2            # four2a(+1) is an unscaled inverse
        return c1 / np.sqrt(float(S.ndfft1) * S.ndfft2)


def _sync_fine(cd, S, i0, delf):
    """syncjs8d: how well the three sync patterns match, signal starting at
    sample i0 of cd, shifted by delf Hz."""
    n = S.ndownsps
    tw = np.exp(2j * np.pi * delf * np.arange(n) / S.fs2) if delf else 1.0
    total = 0.0
    for a, offset in enumerate((0, 36, 72)):
        for k in range(7):
            i1 = i0 + (offset + k) * n
            if i1 >= 0 and i1 + n <= len(cd):
                z = np.sum(cd[i1:i1 + n] * np.conj(S.csync[a][k] * tw))
                total += z.real ** 2 + z.imag ** 2
    return total


def _sync_grid(cd, S, starts, delfs):
    """_sync_fine for many start samples and frequency shifts at once:
    returns an array [shift, start]."""
    n = S.ndownsps
    sym = np.array([off + k for off in (0, 36, 72) for k in range(7)])          # 21 sync symbols
    tmpl = np.array([S.csync[a][k] for a in range(3) for k in range(7)])        # (21, n)
    tw = np.exp(2j * np.pi * np.outer(delfs, np.arange(n)) / S.fs2)              # (F, n)
    idx = np.asarray(starts)[None, :, None] + (sym * n)[:, None, None] + np.arange(n)[None, None, :]
    ok = (idx[:, :, 0] >= 0) & (idx[:, :, -1] < len(cd))                         # (21, T)
    blocks = cd[np.clip(idx, 0, len(cd) - 1)] * ok[:, :, None]                   # (21, T, n)
    ref = np.conj(tmpl[None, :, :] * tw[:, None, :])                             # (F, 21, n)
    z = np.einsum("ktn,fkn->fkt", blocks, ref)
    return (np.abs(z) ** 2).sum(1)


def _normalize(b):
    var = np.mean(b * b) - np.mean(b) ** 2
    sig = np.sqrt(var) if var > 0 else np.sqrt(np.mean(b * b))
    return b / sig if sig > 0 else b


def _bit_metrics(s1, log=False):
    """Per data symbol, 3 bits: best tone with the bit set minus best without."""
    ps = np.log(s1 + 1e-32) if log else s1
    def mx(tones):
        return ps[:, tones].max(1)
    r4 = mx([4, 5, 6, 7]) - mx([0, 1, 2, 3])
    r2 = mx([2, 3, 6, 7]) - mx([0, 1, 4, 5])
    r1 = mx([1, 3, 5, 7]) - mx([0, 2, 4, 6])
    return np.stack([r4, r2, r1], 1).reshape(-1)


def _decode_candidate(down, S, f1, start, depth):
    """js8dec for one candidate. Returns a decode dict, or None.
    Two stations a few Hz apart (e.g. several answering one heartbeat at
    once) can pull the best sync spot to between them; when the best spot
    fails, the next best spots RETRY_HZ or more away get a try too."""
    cd0 = down(f1)
    spots = _fine_spots(cd0, S, start)
    for n_try, (ibest, delf) in enumerate(spots):
        d = _decode_at(cd0, S, f1, ibest, delf, depth, retry=n_try > 0)
        if d is not None:
            return d
    return None


RETRY_PEAKS = 2        # extra sync spots to try when the best one fails
RETRY_MAX_BAD = 40     # stricter on a retry spot (more tries = more chances of junk)
# Junk filter (from 1 h 44 min of real 40 m, 2026-11-18): every nonsense frame
# had weak sync (7-8 of the 21 sync tones) and was either far off the time
# slot or barely passed the error check; real frames with sync that weak
# were all within 0.21 s and had at most 50 bad bits.
WEAK_SYNC = 9          # this many sync tones or fewer = weak sync
WEAK_MAX_DT = 0.5      # ... then the signal must be within this of on time (s)
WEAK_MAX_BAD = 52      # ... and hard errors + OSD distance at most this
RETRY_HZ = 1.5         # ... at least this far (Hz) from the spots already tried
# Text-frame filter (2026-11-18): plain-text frames are packed as numbers in
# a word list, so ANY bits that pass the error check read as real words
# ("T'LLPVERTICALSTANDBY HVO" was noise). Such frames carry no call sign to
# sanity-check, so they must look solid. On all recordings (502 real text
# frames) the weakest real one had 10 sync tones, 48 bad bits and was at most
# 0.66 s off time; both noise frames found fell outside that.
TEXT_MIN_SYNC = 10     # text frames need at least this many sync tones ...
TEXT_MAX_BAD = 50      # ... at most this many hard errors + OSD distance ...
TEXT_MAX_DT = 1.0      # ... and be within this of on time (s)


def _fine_spots(cd, S, start):
    i0 = int(round(start * S.fs2))
    q = S.ndownsps // 4
    starts = np.arange(i0 - q, i0 + q + 1)
    steps = np.arange(-FINE_N, FINE_N + 1) * (S.baud * FINE_STEP)
    if not JOINT:
        ibest = int(starts[int(np.argmax(_sync_grid(cd, S, starts, [0.0])[0]))])
        delf = float(steps[int(np.argmax(_sync_grid(cd, S, [ibest], steps)[:, 0]))])
        return [(ibest, delf)]
    g = _sync_grid(cd, S, starts, steps)
    best_t = g.argmax(1)                       # best start for each frequency step
    score = g[np.arange(len(steps)), best_t]
    spots = []
    for fi in np.argsort(score)[::-1]:
        if len(spots) > RETRY_PEAKS:
            break
        if all(abs(steps[fi] - d) >= RETRY_HZ for _, d in spots):
            spots.append((int(starts[best_t[fi]]), float(steps[fi])))
    return spots


def _is_text(frame, flags):
    d = js8msg.decode_frame(frame, flags)
    return d is not None and d["type"] in (js8msg.FRAME_DATA, js8msg.FRAME_DATA_COMPRESSED)


def _decode_at(cd, S, f1, ibest, delf, depth, retry=False):
    """Decode at one sync spot (start sample, frequency shift)."""
    if delf:
        cd = cd * np.exp(-2j * np.pi * delf * np.arange(len(cd)) / S.fs2)
    f1 = f1 + delf
    # tone strengths in all 79 symbols
    n = S.ndownsps
    blocks = np.zeros((NN, n), dtype=complex)
    for k in range(NN):
        i1 = ibest + k * n
        if i1 >= 0 and i1 + n <= len(cd):
            blocks[k] = cd[i1:i1 + n]
    spec = np.fft.fft(blocks, axis=1)
    s2 = np.abs(spec[:, :8]) / 1e3
    # sync check: how many of the 21 sync tones are the strongest in their symbol
    best = s2.argmax(1)
    nsync = sum(int(best[off + k] == S.costas[a][k]) for a, off in enumerate((0, 36, 72)) for k in range(7))
    if nsync <= 6:
        return None
    s1 = s2[DATA_SYMBOLS]
    med = np.median(s1)
    if med <= 0:
        return None
    s1 = s1 / med
    llr0 = 2.83 * _normalize(_bit_metrics(s1))
    llr1 = 2.83 * _normalize(_bit_metrics(s1, log=True))
    for ipass in range(1, 5):
        llr = (llr1 if ipass == 2 else llr0).copy()
        if ipass == 3:
            llr[:24] = 0
        if ipass == 4:
            llr[:48] = 0
        cw, nhard = bp_decode(llr)
        dmin = 0.0
        if cw is None:
            if depth < 3 or nsync < OSD_MIN_SYNC:
                continue                 # (below 8 sync tones OSD rescued 0 of 1888 tries - just time)
            cw, nhard, dmin = osd_decode(llr)
        if not cw.any():
            continue                     # the all-zero codeword: never a real signal
        if not (nhard + dmin < 60 and not (ipass > 2 and nhard > 39) and not (ipass == 4 and nhard > 30)):
            continue
        if retry and nhard + dmin > RETRY_MAX_BAD:
            continue
        if nsync <= WEAK_SYNC and (abs(ibest / S.fs2 - S.astart) > WEAK_MAX_DT
                                   or nhard + dmin > WEAK_MAX_BAD):
            continue                     # weak sync AND off time or barely passing: junk
        got = bits_to_frame(message_bits(cw))
        if got is None:
            continue
        frame, flags = got
        if _is_text(frame, flags) and (nsync < TEXT_MIN_SYNC or nhard + dmin > TEXT_MAX_BAD
                                       or abs(ibest / S.fs2 - S.astart) > TEXT_MAX_DT):
            continue                     # shaky plain-text frame: most likely noise
        itone = js8enc.tones(frame, flags, S.name)
        return dict(frame=frame, flags=flags, freq=f1, start=ibest / S.fs2,
                    dt=ibest / S.fs2 - S.astart, snr=_snr(spec, itone, S),
                    hard=nhard, dist=dmin, sync=nsync, tones=itone, ipass=ipass)
    return None


def _snr(spec, itone, S):
    """Signal report in dB in a 2500 Hz bandwidth: average power in the sent
    tones above the noise (median of the other tones' power)."""
    p = np.abs(spec[:, :8]) ** 2
    tone = np.array(itone)
    sig = p[np.arange(NN), tone]
    mask = np.ones_like(p, dtype=bool)
    mask[np.arange(NN), tone] = False
    noise = np.median(p[mask]) / np.log(2)      # median of noise power -> its mean
    ps = sig.mean() - noise
    if noise <= 0 or ps <= 0:
        return -30
    snr = 10 * np.log10(ps / noise) - 10 * np.log10(2500 / S.baud)
    return int(round(max(-30.0, snr)))


def subtract(dd, itone, f0, start, S):
    """subtractjs8: take a decoded signal out of the audio. The signal's
    amplitude and phase are followed slowly (a ~0.1 s average) so a fading
    signal is taken out too."""
    nframe = S.nsps * NN
    nstart = int(start * FS)
    dphi = 2 * np.pi * (f0 / FS + np.repeat(np.array(itone), S.nsps) / S.nsps)
    phi = np.concatenate([[0.0], np.cumsum(dphi)[:-1]])
    cref = np.exp(1j * phi)
    lo, hi = max(0, nstart), min(len(dd), nstart + nframe)
    if hi <= lo:
        return
    seg = slice(lo - nstart, hi - nstart)
    camp = dd[lo:hi] * np.conj(cref[seg])
    nfilt = S.nfilt
    w = np.cos(np.pi * np.arange(-nfilt // 2, nfilt // 2 + 1) / nfilt) ** 2
    w /= w.sum()
    nf = 1 << int(np.ceil(np.log2(len(camp) + len(w))))
    W = np.fft.fft(w, nf)
    cf = np.fft.ifft(np.fft.fft(camp, nf) * W)[nfilt // 2:nfilt // 2 + len(camp)]
    norm = np.fft.ifft(np.fft.fft(np.ones(len(camp)), nf) * W).real[nfilt // 2:nfilt // 2 + len(camp)]
    cf = cf / np.maximum(norm, 1e-6)
    dd[lo:hi] -= 2 * np.real(cf * cref[seg])


# SNR the way JS8Call reports it: the decoded tones' power against the band's
# background at that frequency (taken from the waterfall), NOT the station's
# own unused tones - a strong signal leaks into those, so the old way read
# strong signals ~10 dB low and weak ones ~6 dB high (measured 2026-11-18).
SNR_FROM_WF = True
# Calibrated to the JS8Call PROGRAM (what people see on screen), which reads ~4 dB
# above its own js8 command-line decoder on the same audio. Normal: 107 frames
# side by side on the air 2026-11-21 (median 0, 56% within 2 dB); the others from
# the recordings vs the js8 decoder + the same gap (fast 72, turbo 12, slow 6 frames).
SNR_CAL = {"normal": 3.0, "fast": -1.0, "turbo": -5.0, "slow": 4.5}
SNR_MIN = -28                    # JS8Call's floor


def _snr_waterfall(dd, S):
    """Power waterfall: Hann window of one symbol, FFT of two symbols (cells
    of half a tone), 4 steps per symbol. -> (cells, frames)"""
    n, step = S.nsps, S.nsps // 4
    nframes = (len(dd) - n) // step + 1
    idx = np.arange(n)[None, :] + step * np.arange(nframes)[:, None]
    win = np.zeros((nframes, 2 * n))
    win[:, :n] = dd[idx] * np.hanning(n)
    return (np.abs(np.fft.rfft(win, axis=1)) ** 2).T


def _noise_base(s, S):
    """Background power per frequency cell: 30th percentile over the slot's
    time (noise power is exponential: p30 = 0.357 x mean), then the lower
    envelope over +-100 Hz so cells under a long signal take their
    neighbours' quiet level."""
    live = s.sum(0) > 1e-9 * max(float(s.sum(0).max()), 1e-30)
    if not live.any():
        return np.ones(s.shape[0]), live
    b = np.percentile(s[:, live], 30, axis=1) / 0.357
    w = int(100 / (S.baud / 2)) | 1
    pad = np.pad(b, w // 2, mode="edge")
    env = np.percentile(np.lib.stride_tricks.sliding_window_view(pad, w), 20, axis=1)
    return np.maximum(env, 1e-30), live


SNR_BASE = "fit"                  # background: "fit" (JS8Call's way: straight scale on a crowded band) or "local"
FIT_SEG, FIT_PCT, FIT_TERMS = 10, 10, 5


def _noise_fit(s, live, S, fmin=200.0, fmax=3000.0):
    """Background like JS8Call's baselinejs8: the slot's average spectrum in
    dB, the quietest 10% of cells in each of ten slices of the band, and a
    smooth 4th-order curve through them (it follows the radio's filter but
    not the signals, so a crowded stretch of band doesn't lift it)."""
    cell = S.baud / 2
    ia, ib = max(1, int(round(fmin / cell))), min(s.shape[0] - 1, int(round(fmax / cell)))
    avg = 10 * np.log10(np.maximum(s[ia:ib + 1][:, live].mean(1), 1e-30))
    n = len(avg)
    seg = n // FIT_SEG
    xs, ys = [], []
    for k in range(FIT_SEG):
        a = avg[k * seg:(k + 1) * seg]
        if not len(a):
            continue
        lim = np.percentile(a, FIT_PCT)
        j = np.nonzero(a <= lim)[0] + k * seg
        xs.append(j), ys.append(avg[j])
    x, y = np.concatenate(xs) - n / 2, np.concatenate(ys)
    poly = np.polyfit(x / n, y, FIT_TERMS - 1)
    base_db = np.polyval(poly, (np.arange(s.shape[0]) - ia - n / 2) / n)
    base_db[:ia] = base_db[ia]
    base_db[ib + 1:] = base_db[ib]
    return 10 ** (base_db / 10)


def _wf_snr(wf, d, itone, S):
    s, base, live = wf
    nf, nt = s.shape
    frames = np.round(d["start"] * FS / (S.nsps // 4) + 4 * np.arange(NN)).astype(int)
    c = d["freq"] / (S.baud / 2) + 2 * np.asarray(itone)
    lo = np.floor(c).astype(int)
    ok = (frames >= 0) & (frames < nt) & (lo >= 0) & (lo + 1 < nf)
    ok[ok] &= live[frames[ok]]
    if ok.sum() < 10:
        return None
    lo, fr = lo[ok], frames[ok]
    p = (s[lo, fr] + s[lo + 1, fr]).mean()
    n = (base[lo] + base[lo + 1]).mean()
    ratio = 2 * (p / n - 1)          # signal vs one tone's width of noise
    snr = 10 * np.log10(max(ratio, 1e-3)) - 10 * np.log10(2500 / S.baud) + SNR_CAL.get(S.name, 0.0)
    return int(round(max(SNR_MIN, snr)))


def decode(audio, speed="normal", fmin=200.0, fmax=3000.0, depth=3, passes=4):
    """Decode one slot of audio (12 kHz, starting at the slot's start).
    Returns a list of dicts: frame, flags, snr (dB), dt (s, 0 = on time),
    freq (Hz, lowest tone), plus details (hard errors, sync, pass)."""
    S = _Speed(speed)
    dd = np.zeros(S.nmax)
    a = np.asarray(audio, dtype=float)[:S.nmax]
    rms = np.sqrt(np.mean(a * a)) if len(a) else 0
    if rms > 0:
        a = a * (1000.0 / rms)       # the level doesn't matter; keep numbers comfortable
    dd[:len(a)] = a
    if depth == 1:
        passes = 1
    found, seen = [], set()
    wf = None
    if SNR_FROM_WF:
        sw = _snr_waterfall(dd, S)
        wf = (sw,) + _noise_base(sw, S)
        if SNR_BASE == "fit":
            wf = (sw, _noise_fit(sw, wf[2], S, fmin, fmax), wf[2])
    last = -1
    for p in range(passes):
        if p > 0 and len(found) == last:
            break                       # the last pass found nothing new
        last = len(found)
        down = _Downsampler(dd, S)
        for f1, start, sync in find_candidates(dd, S, fmin, fmax):
            d = _decode_candidate(down, S, f1, start, depth)
            if d is None:
                continue
            if depth > 1 and p < passes - 1:
                subtract(dd, d["tones"], d["freq"], d["start"], S)
            key = (d["frame"], d["flags"])
            if key in seen:
                continue
            seen.add(key)
            if wf is not None:
                w = _wf_snr(wf, d, d["tones"], S)
                if w is not None:
                    d["snr"] = w
            d.pop("tones")
            d["pass"] = p + 1
            found.append(d)
    return found

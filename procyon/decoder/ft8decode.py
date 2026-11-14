"""Procyon's own FT8 decoder - stage 2: decode ONE signal whose rough time and
frequency are already known (later stages will find signals on their own).

    decode_at(audio, dt, freq) -> (text, info) or (None, info)

  audio  12 000 samples/s, one 15 s cycle (numpy int16 or float)
  dt     seconds the signal starts after 0.5 s into the cycle (jt9's "DT")
  freq   audio frequency of the lowest tone, Hz

Steps, the same ones every FT8 decoder takes:
  1. fine-tune time and frequency on the three Costas sync patterns
  2. listen to each of the 79 symbols: how strong is each of the 8 tones
  3. turn that into 174 "soft bits" (how sure we are each bit is 0 or 1)
  4. LDPC belief propagation: the 83 parity checks vote until every check
     passes (or give up after ITERATIONS rounds)
  5. the 14-bit CRC must match, then unpack the 77 bits into text

Written for Procyon from the published FT8 description; no WSJT-X code.
"""

import json
import os

import numpy as np

from .. import fec, pack77

HERE = os.path.dirname(os.path.abspath(__file__))

FS = 12000
NSPS = 1920                 # samples per symbol (0.16 s)
NSYM = 79
TONE_HZ = FS / NSPS         # 6.25
COSTAS = (3, 1, 4, 0, 6, 5, 2)
SYNC_AT = (0, 36, 72)
DATA_SYMS = [i for i in range(NSYM) if not any(s <= i < s + 7 for s in SYNC_AT)]
GRAYMAP = (0, 1, 3, 2, 5, 6, 4, 7)
# bit value (0/1) of each of the 3 bits carried by each tone
TONE_BITS = np.zeros((8, 3), np.uint8)
for v, tone in enumerate(GRAYMAP):
    TONE_BITS[tone] = ((v >> 2) & 1, (v >> 1) & 1, v & 1)
ITERATIONS = 30
NSYMS = (1, 2, 3)            # soft-bit methods tried in turn
OSD = True
ZSUM_ITERS = 3              # BP keeps the sum of its first few rounds' beliefs (for OSD)
last_zsum = None
BP_PATIENCE = 6             # give up after this many rounds without progress
last_fails = 0              # fewest failing checks in the last bp_decode

with open(os.path.join(HERE, "ldpc_checks.json")) as f:
    CHECKS = json.load(f)
# Edges of the Tanner graph: edge e joins check CHK[e] and bit BIT[e].
CHK = np.array([c for c, row in enumerate(CHECKS) for _ in row])
BIT = np.array([b for row in CHECKS for b in row])
NCHK = len(CHECKS)


# ------------------------------------------------------------------ listening

def tone_amps(audio, start, freq):
    """79 x 8 complex tone amplitudes for a signal starting at sample `start`."""
    n = NSYM * NSPS
    seg = np.zeros(n)
    lo, hi = max(start, 0), min(start + n, len(audio))
    if hi > lo:
        seg[lo - start:hi - start] = audio[lo:hi]
    t = np.arange(n) / FS
    mixed = seg * np.exp(-2j * np.pi * freq * t)       # lowest tone -> 0 Hz
    spec = np.fft.fft(mixed.reshape(NSYM, NSPS), axis=1)
    return spec[:, :8]


def sync_score(audio, start, freq):
    """How well the Costas patterns line up: their tone vs the other tones."""
    n = 7 * NSPS
    t = np.arange(NSPS) / FS
    score = 0.0
    for s in SYNC_AT:
        a = start + s * NSPS
        if a < 0 or a + n > len(audio):
            continue
        blk = audio[a:a + n].reshape(7, NSPS) * np.exp(-2j * np.pi * freq * t)
        p = np.abs(np.fft.fft(blk, axis=1)[:, :8]) ** 2
        want = p[np.arange(7), COSTAS].sum()
        score += want / (p.sum() - want + 1e-9)
    return score


def refine(audio, dt, freq, dt_span=0.12, df_span=3.0):
    """Best start sample and frequency near the guess (coarse, then fine)."""
    start0 = int(round((0.5 + dt) * FS))
    best = (-1, start0, freq)
    for step_s, step_f, span_s, span_f in ((60, 0.5, int(dt_span * FS), df_span), (10, 0.1, 60, 0.5)):
        _, s0, f0 = best
        for s in range(s0 - span_s, s0 + span_s + 1, step_s):
            for f in np.arange(f0 - span_f, f0 + span_f + 1e-9, step_f):
                sc = sync_score(audio, s, f)
                if sc > best[0]:
                    best = (sc, s, f)
    return best


# ------------------------------------------------------------------ soft bits

def _combo_bits(nsym):
    """For every combination of nsym tones: the 3*nsym bits it carries."""
    combos = np.array(np.meshgrid(*[range(8)] * nsym, indexing="ij")).reshape(nsym, -1).T
    bits = np.concatenate([TONE_BITS[combos[:, j]] for j in range(nsym)], axis=1)
    return combos, bits


_COMBOS = {n: _combo_bits(n) for n in (1, 2, 3)}


def _groups(nsym):
    """Symbol groups (start, size) for each data half, and per size the
    combination bit masks."""
    out = []
    for half in (0, 29):
        for g in range(half, half + 29, nsym):
            out.append((g, min(nsym, half + 29 - g)))
    return out


_GROUPS = {n: _groups(n) for n in (1, 2, 3)}


def soft_bits(amps, nsym=1):
    """174 log-likelihood ratios; positive = probably a 1.

    nsym = how many symbols are listened to together. With 2 or 3, the
    tones' complex amplitudes are added before taking the size, which uses
    the signal's smooth phase from one symbol to the next (like WSJT-X)."""
    data = amps[DATA_SYMS]                             # 58 x 8 complex
    llr = np.zeros(174)
    by_size = {}
    for g, n in _GROUPS[nsym]:
        by_size.setdefault(n, []).append(g)
    for n, starts in by_size.items():
        cmb, cb = _COMBOS[n]
        starts = np.array(starts)
        tot = np.zeros((len(starts), len(cmb)), complex)
        for j in range(n):
            tot += data[starts + j][:, cmb[:, j]]
        mag = np.abs(tot)                              # groups x combos
        for k in range(3 * n):
            one = cb[:, k] == 1
            llr[3 * starts + k] = mag[:, one].max(1) - mag[:, ~one].max(1)
    return llr / (llr.std() + 1e-12) * 2.83


# ------------------------------------------------------------------ LDPC

def bp_decode(llr, iterations=ITERATIONS):
    """Sum-product belief propagation. Returns (bits, iterations used) or
    (None, iterations) if the checks never all passed.
    Inside, the usual convention: positive = probably a 0."""
    llr = -llr
    to_chk = llr[BIT].copy()                     # bit -> check messages
    global last_fails
    fewest, since = NCHK, 0
    for it in range(1, iterations + 1):
        # check -> bit: tanh rule, leaving out the edge's own message
        t = np.tanh(np.clip(to_chk, -30, 30) / 2)
        t = np.where(np.abs(t) < 1e-12, 1e-12, t)
        prod = np.ones(NCHK)
        np.multiply.at(prod, CHK, t)
        others = np.clip(prod[CHK] / t, -0.999999, 0.999999)
        to_bit = 2 * np.arctanh(others)
        # bit totals and bit -> check messages
        total = llr.copy()
        np.add.at(total, BIT, to_bit)
        to_chk = total[BIT] - to_bit
        bits = (total < 0).astype(np.uint8)
        syn = np.zeros(NCHK, np.uint8)
        np.add.at(syn, CHK, bits[BIT])
        fails = int((syn % 2).sum())
        if fails == 0:
            last_fails = 0
            return bits, it
        if fails < fewest:
            fewest, since = fails, 0
        else:
            since += 1
            if since >= BP_PATIENCE:
                break                            # not getting anywhere
    last_fails = fewest
    return None, it


def crc_ok(bits):
    return list(bits[77:91]) == fec.crc14(bits[:77])


# ------------------------------------------------------------------ OSD
# Ordered statistics decoding - the fallback when belief propagation gets
# stuck. Take the 91 most reliable bits that can stand on their own,
# rebuild the full 174-bit codeword from them, and also try flipping one or
# two of the shakiest of those 91. Keep the candidate that best agrees with
# what was heard, if it also passes the CRC.

def _gf2_rref(M, cols):
    M = M.copy()
    piv = []
    r = 0
    for c in cols:
        if r == len(M):
            break
        hit = np.nonzero(M[r:, c])[0]
        if not len(hit):
            continue
        p = r + hit[0]
        if p != r:
            M[[r, p]] = M[[p, r]]
        others = np.nonzero(M[:, c])[0]
        others = others[others != r]
        M[others] ^= M[r]
        piv.append(c)
        r += 1
    return M[:r], piv


def _generator():
    """91 x 174 generator matrix of the code (rows = codewords)."""
    H = np.zeros((NCHK, 174), np.uint8)
    for c, row in enumerate(CHECKS):
        H[c, row] = 1
    R, piv = _gf2_rref(H, range(174))
    free = [c for c in range(174) if c not in piv]
    G = np.zeros((len(free), 174), np.uint8)
    for i, f in enumerate(free):
        G[i, f] = 1
        G[i, piv] = R[:, f]
    return G


G = _generator()
OSD_PAIRS = 40          # order 2 among this many least reliable info bits


def osd_decode(llr):
    """Best CRC-passing codeword near the soft bits, or None."""
    order = np.argsort(-np.abs(llr))                   # most reliable first
    Gp, piv = _gf2_rref(G, order)                      # systematic on 91 reliable bits
    piv = np.array(piv)
    hard = (llr > 0).astype(np.uint8)
    u0 = hard[piv]
    base = (u0 @ Gp) % 2
    rel = np.abs(llr)
    # candidates: no flip, every single flip, pairs among the shakiest
    k = len(piv)
    weak = np.arange(k - OSD_PAIRS, k)
    ia, ib = np.triu_indices(OSD_PAIRS, 1)
    cands = np.concatenate([base[None, :], base ^ Gp, base ^ Gp[weak[ia]] ^ Gp[weak[ib]]])
    dist = ((cands != hard) * rel).sum(1)
    for n in np.argsort(dist)[:OSD_KEEP]:
        c = cands[n]
        if crc_ok(c) and c[:77].any():
            return c, float(dist[n])
    return None, None


OSD_KEEP = 1            # only the single closest candidate may pass


# ------------------------------------------------------------------ all together

def decode_at(audio, dt, freq, hashes=None, search=True):
    audio = np.asarray(audio, dtype=float)
    if search:
        score, start, f = refine(audio, dt, freq)
    else:
        start, f, score = int(round((0.5 + dt) * FS)), freq, sync_score(audio, int(round((0.5 + dt) * FS)), freq)
    info = {"dt": round(start / FS - 0.5, 3), "freq": round(float(f), 2), "sync": round(float(score), 2)}
    amps = tone_amps(audio, start, f)
    info["why"] = "LDPC did not converge"
    for nsym in NSYMS:
        bits, it = bp_decode(soft_bits(amps, nsym))
        if bits is None:
            continue
        if not crc_ok(bits):
            info["why"] = "CRC mismatch"
            continue
        if not bits[:77].any():
            info["why"] = "all zeros"
            continue
        break
    else:
        if not OSD:
            return None, info
        for nsym in NSYMS:
            bits, d = osd_decode(soft_bits(amps, nsym))
            if bits is not None:
                it = f"osd {d:.1f}"
                break
        else:
            return None, info
    info.pop("why")
    info["nsym"], info["iterations"] = nsym, it
    text = pack77.unpack77("".join(map(str, bits[:77])), hashes)
    return text, info


# ------------------------------------------------------------------ batches
# The same soft bits and belief propagation, for many candidates at once:
# one numpy calculation per round instead of one per candidate.

_CHK_EDGES = np.full((NCHK, 7), len(BIT))           # edge numbers per check, padded
_CHK_BITS = np.full((NCHK, 7), 174)                  # bit numbers per check, padded
for _c in range(NCHK):
    _e = np.nonzero(CHK == _c)[0]
    _CHK_EDGES[_c, :len(_e)] = _e
    _CHK_BITS[_c, :len(_e)] = BIT[_e]
_BIT_EDGES = np.array([np.nonzero(BIT == b)[0] for b in range(174)])    # 174 x 3


def soft_bits_many(amps, nsym):
    """soft_bits for a batch: amps is (B, 79, 8) -> (B, 174)."""
    data = amps[:, DATA_SYMS]                          # B x 58 x 8
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
        for k in range(3 * n):
            one = cb[:, k] == 1
            llr[:, 3 * starts + k] = mag[:, :, one].max(2) - mag[:, :, ~one].max(2)
    sd = llr.std(1, keepdims=True)
    return llr / (sd + 1e-12) * 2.83


def bp_many(llr, iterations=ITERATIONS):
    """bp_decode for a batch (B, 174). Returns (bits B x 174, ok B, fewest
    failing checks B)."""
    llr = -np.asarray(llr, float)                      # positive = probably 0
    B = len(llr)
    to_chk = llr[:, BIT].copy()
    bits_out = np.zeros((B, 174), np.uint8)
    ok = np.zeros(B, bool)
    fewest = np.full(B, NCHK)
    since = np.zeros(B, int)
    live = np.arange(B)
    one = np.ones((B, 1))
    global last_zsum
    zsum = np.zeros((B, 174))
    for it in range(iterations):
        t = np.tanh(np.clip(to_chk[live], -30, 30) / 2)
        t = np.where(np.abs(t) < 1e-12, 1e-12, t)
        tp = np.concatenate([t, one[:len(live)]], axis=1)
        prod = tp[:, _CHK_EDGES].prod(2)              # live x 83
        others = np.clip(prod[:, CHK] / t, -0.999999, 0.999999)
        to_bit = 2 * np.arctanh(others)
        total = llr[live] + to_bit[:, _BIT_EDGES].sum(2)
        to_chk[live] = total[:, BIT] - to_bit
        if it < ZSUM_ITERS:
            zsum[live] += -total                    # back to positive = 1
        bits = (total < 0).astype(np.uint8)
        bp = np.concatenate([bits, np.zeros((len(live), 1), np.uint8)], axis=1)
        fails = (bp[:, _CHK_BITS].sum(2) % 2).sum(1)
        done = fails == 0
        ok[live[done]] = True
        bits_out[live[done]] = bits[done]
        better = fails < fewest[live]
        fewest[live] = np.minimum(fewest[live], fails)
        since[live] = np.where(better, 0, since[live] + 1)
        keep = ~done & (since[live] < BP_PATIENCE)
        live = live[keep]
        if not len(live):
            break
    fewest[ok] = 0
    last_zsum = zsum
    return bits_out, ok, fewest


def osd_many(llr, chunk=256):
    """osd_decode for a batch (N, 174): returns a list of codewords or None.
    The Gaussian elimination runs on all N matrices together, each with its
    own column order, rows packed into bytes."""
    llr = np.asarray(llr, float)
    out = []
    for c0 in range(0, len(llr), chunk):
        out += _osd_chunk(llr[c0:c0 + chunk])
    return out


def _osd_chunk(llr):
    N = len(llr)
    K = len(G)                                          # 91
    order = np.argsort(-np.abs(llr), axis=1)            # most reliable first
    # rows packed into 3 x 64-bit words (bit 63 of word 0 = column 0), so a
    # row operation flips 64 columns per step
    P = np.zeros((N, K, 192), np.uint8)
    P[:, :, :174] = G[:, order].transpose(1, 0, 2)
    M = np.packbits(P, axis=2).view(">u8").astype(np.uint64)  # N x 91 x 3
    ar = np.arange(N)
    cur = np.zeros(N, np.int64)                         # next column to try (permuted space)
    piv = np.zeros((N, K), int)
    one = np.uint64(1)
    for r in range(K):
        while True:
            col = (M[ar, :, cur >> 6] >> (63 - (cur & 63)).astype(np.uint64)[:, None]) & one   # N x 91
            col[:, :r] = 0
            has = col.any(1)
            if has.all():
                break
            cur[~has] += 1
        p = col.argmax(1)                               # first row >= r with the bit
        swap = p != r
        if swap.any():
            s = ar[swap]
            tmp = M[s, r].copy()
            M[s, r] = M[s, p[swap]]
            M[s, p[swap]] = tmp
        # the pivot column in every row (above r too: fully reduced)
        col = (M[ar, :, cur >> 6] >> (63 - (cur & 63)).astype(np.uint64)[:, None]) & one
        col[:, r] = 0
        mask = np.uint64(0) - col                       # all ones where the row needs the pivot row
        M ^= mask[:, :, None] & M[:, r][:, None, :]
        piv[:, r] = cur
        cur += 1
    M = M.astype(">u8").view(np.uint8).reshape(N, K, 24)
    Gs = np.unpackbits(M, axis=2)[:, :, :174]           # N x 91 x 174, permuted columns
    hardp = (np.take_along_axis(llr, order, 1) > 0).astype(np.uint8)
    relp = np.abs(np.take_along_axis(llr, order, 1)).astype(np.float32)
    u = np.take_along_axis(hardp, piv, 1)               # N x 91
    base = (np.einsum("nk,nkj->nj", u, Gs) % 2).astype(np.uint8)   # sums <= 91 fit in uint8
    # Distances without building the 871 candidates: with d0 = where the base
    # disagrees with what was heard and w = rel * (1 - 2 d0), flipping row a
    # adds s_a = G_a . w, and flipping rows a and b adds s_a + s_b - 2 G_a.(w G_b).
    d0 = base ^ hardp
    D0 = (d0 * relp).sum(1)                             # N
    w = relp * (1 - 2 * d0.astype(np.float32))          # N x 174
    Gf = Gs.astype(np.float32)
    sgl = np.einsum("nkj,nj->nk", Gf, w)                # N x 91
    weak = np.arange(K - OSD_PAIRS, K)
    Gw = Gf[:, weak]                                    # N x 40 x 174
    cross = np.einsum("naj,nbj->nab", Gw * w[:, None, :], Gw)      # N x 40 x 40
    ia, ib = np.triu_indices(OSD_PAIRS, 1)
    pair = sgl[:, weak[ia]] + sgl[:, weak[ib]] - 2 * cross[:, ia, ib]
    dist = np.concatenate([np.zeros((N, 1), np.float32), sgl, pair], axis=1) + D0[:, None]
    pick = dist.argmin(1)
    best = base.copy()
    for n in range(N):
        c = pick[n]
        if 1 <= c <= K:
            best[n] ^= Gs[n, c - 1]
        elif c > K:
            best[n] ^= Gs[n, weak[ia[c - K - 1]]] ^ Gs[n, weak[ib[c - K - 1]]]
    cw = np.zeros((N, 174), np.uint8)
    np.put_along_axis(cw, order, best, 1)
    return [c if (crc_ok(c) and c[:77].any()) else None for c in cw]

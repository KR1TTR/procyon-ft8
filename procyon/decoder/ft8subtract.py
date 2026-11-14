"""Procyon's own FT8 decoder - stage 4: take decoded signals OUT of the audio.

Once a message is decoded we know exactly which 79 tones were sent, so we can
rebuild the signal and subtract it. Weaker stations that were hidden under it
then show up in the next pass.

The rebuilt signal has to match the real one closely in time, frequency,
strength and phase - and on HF the strength and phase wander (fading). So:
  1. fine-tune time (to 0.5 ms) and frequency (to ~0.02 Hz) against the
     whole known signal, not just the sync patterns
  2. multiply the audio by the rebuilt signal's conjugate and smooth it
     (about a third of a second): that tracks the wandering strength/phase
  3. subtract the rebuilt signal with that strength/phase
"""

import numpy as np

from . import ft8decode as D

FS = 12000
FS2 = 200
NSPS = 1920
SMOOTH = 4000                   # samples (1/3 s) for the strength/phase tracking


def _pulse(nsps, bt=2.0):
    from math import erf, log, pi, sqrt
    c = pi * sqrt(2.0 / log(2.0))
    t = (np.arange(3 * nsps) - 1.5 * nsps) / nsps
    verf = np.vectorize(erf)
    return 0.5 * (verf(c * bt * (t + 0.5)) - verf(c * bt * (t - 0.5)))


_PULSES = {}


def phase(tones, f0, fs):
    """Phase (radians) of the GFSK signal sample by sample, starting at 0."""
    nsps = int(round(0.16 * fs))
    if nsps not in _PULSES:
        _PULSES[nsps] = _pulse(nsps)
    pulse = _PULSES[nsps]
    itone = np.asarray(tones, float)
    n = len(itone)
    dphi = np.zeros((n + 2) * nsps)
    peak = 2 * np.pi / nsps
    for j, tone in enumerate(itone):
        dphi[j * nsps:j * nsps + 3 * nsps] += peak * pulse * tone
    dphi[0:2 * nsps] += peak * itone[0] * pulse[nsps:3 * nsps]
    dphi[n * nsps:(n + 2) * nsps] += peak * itone[-1] * pulse[0:2 * nsps]
    dphi += 2 * np.pi * f0 / fs
    return np.concatenate(([0.0], np.cumsum(dphi[nsps:nsps + n * nsps - 1])))


def tones_of(bits):
    """79 tones from the 174 decoded codeword bits."""
    cw = bits
    data = tuple(D.GRAYMAP[4 * cw[3 * i] + 2 * cw[3 * i + 1] + cw[3 * i + 2]] for i in range(58))
    c = tuple(D.COSTAS)
    return c + data[:29] + c + data[29:] + c


def refine(x, tones, i0, df):
    """Exact start (s from cycle start) and frequency offset (Hz) of a decoded
    signal, from its narrow-band copy x (lowest tone near 0 Hz)."""
    ref = np.exp(1j * phase(tones, 0.0, FS2))          # baseband, 79 x 32 samples
    n = len(ref)
    X = np.fft.fft(x)
    fgrid = np.fft.fftfreq(len(x), 1 / FS2)
    tn = (i0 + np.arange(n)) / FS2                      # times of the signal's samples

    def matched(tau):
        """x moved EARLIER by tau seconds, times the reference's conjugate."""
        y = np.fft.ifft(X * np.exp(2j * np.pi * fgrid * tau))
        seg = np.zeros(n, complex)
        lo, hi = max(i0, 0), min(i0 + n, len(y))
        seg[lo - i0:hi - i0] = y[lo:hi]
        return seg * ref.conj()

    def best_f(p, fs):
        # score for many frequency shifts at once (a small DFT of p)
        sc = np.abs(np.exp(-2j * np.pi * np.outer(fs, tn)) @ p)
        j = int(np.argmax(sc))
        return float(sc[j]), float(fs[j])

    def best_t(taus, f):
        rot = np.exp(-2j * np.pi * f * tn)
        sc = [abs((matched(t) * rot).sum()) for t in taus]
        j = int(np.argmax(sc))
        return sc[j], float(taus[j])

    # Frequency FIRST: 0.1 Hz off over 12.6 s already smears the whole
    # match, so a time search at the wrong frequency finds nonsense.
    tau = 0.0
    p = matched(tau)
    _, f = best_f(p, df + np.arange(-0.6, 0.601, 0.02))
    _, tau = best_t(np.arange(-0.0125, 0.01251, 0.00125), f)
    p = matched(tau)
    _, f = best_f(p, f + np.arange(-0.04, 0.0401, 0.005))
    _, tau = best_t(tau + np.arange(-0.00125, 0.00126, 0.00025), f)
    # phase() adds up the phase sample by sample, so its reference sits half a
    # sample late - at 200 samples/s that is 2.5 ms (measured: estimates were
    # 2.4-2.9 ms early). The full-rate rebuild has the same half sample at 12 kHz.
    return i0 / FS2 + tau + 0.5 / FS2 - 0.5 / FS, f


def _smooth(z, w):
    """np.convolve(z, w, "same") done with FFTs (much faster for long z)."""
    n = len(z) + len(w) - 1
    m = 1 << (n - 1).bit_length()
    full = np.fft.ifft(np.fft.fft(z, m) * np.fft.fft(w, m))[:n]
    if not np.iscomplexobj(z):
        full = full.real
    a = (len(w) - 1) // 2
    return full[a:a + len(z)]


BLOCK = 40                      # strength/phase tracked at 12000/40 = 300 per second


def analytic(audio):
    """Complex version of the audio with only positive frequencies (its real
    part is the audio itself). Subtracting from it needs no extra filtering."""
    n = len(audio)
    S = np.fft.fft(audio)
    S[1:(n + 1) // 2] *= 2
    S[(n + 1) // 2 + (1 - n % 2):] = 0
    return np.fft.ifft(S)


def _slow(z, nw):
    """Smooth z over about nw samples: block means, sin^2 window, back to
    full rate. The fading it follows is far slower than a block."""
    nb = len(z) // BLOCK
    if nb < 3:
        return np.full(len(z), z.mean())
    zb = z[:nb * BLOCK].reshape(nb, BLOCK).mean(1)
    m = max(3, nw // BLOCK)
    w = np.sin(np.pi * np.arange(1, m + 1) / (m + 1)) ** 2
    sm = np.convolve(zb, w, mode="same") / np.convolve(np.ones(nb), w, mode="same")
    centres = (np.arange(nb) + 0.5) * BLOCK
    t = np.arange(len(z))
    return np.interp(t, centres, sm.real) + 1j * np.interp(t, centres, sm.imag)


def reference(tones, f0, t_start, n_audio):
    """(lo, hi, rebuilt signal for A[lo:hi]) - unit amplitude."""
    start = int(round(t_start * FS))
    frac = t_start * FS - start
    ph = phase(tones, f0, FS) - 2 * np.pi * f0 * frac / FS      # sub-sample start
    ref = np.exp(1j * ph)
    lo, hi = max(start, 0), min(start + len(ref), n_audio)
    return lo, hi, ref[lo - start:hi - start] if hi > lo else None


def subtract(A, tones, f0, t_start, ref=None):
    """Remove one signal from A (the analytic audio, complex, changed in place).
    ref: reference(...)'s answer, if already made (e.g. by a worker process).
    Returns what was removed (lo, hi, ref, amp) for re-estimating later."""
    lo, hi, r = ref if ref is not None else reference(tones, f0, t_start, len(A))
    if r is None:
        return None
    amp = _slow(A[lo:hi] * r.conj(), SMOOTH)
    A[lo:hi] -= amp * r
    return [lo, hi, r.astype(np.complex64), amp.astype(np.complex64)]


def reestimate(A, removed, sweeps=1):
    """Put each removed signal back and measure it again, now that its
    neighbours are gone too (their energy no longer leaks into its
    strength/phase estimate), then take it out again."""
    for _ in range(sweeps):
        for item in removed:
            lo, hi, r, amp = item
            A[lo:hi] += amp * r
            amp = _slow(A[lo:hi] * r.conj(), SMOOTH).astype(np.complex64)
            A[lo:hi] -= amp * r
            item[3] = amp

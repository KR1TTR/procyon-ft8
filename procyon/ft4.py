"""FT4 encoder: message -> 103 tones -> GFSK audio (ports of WSJT-X's
genft4.f90 and gen_ft4wave.f90).

FT4 packs messages exactly like FT8 (Procyon's own packer, pack77.py, makes
the 77 message bits). Then, the FT4 way:
scramble them with RVEC, add the 14-bit CRC, add the 83 LDPC parity bits
(the same (174,91) code as FT8), and send 2 bits per tone with four 4x4
Costas sync arrays."""

import functools
from math import pi

import numpy as np

from .fec import codeword, crc14  # noqa: F401 - crc14 kept importable from here
from .ft8 import _gfsk_pulse, message_bits as _message_bits

PERIOD = 7.5           # seconds per T/R cycle
NSPS = 576             # samples per symbol @ 12 kHz
SYMBOL_SEC = NSPS / 12000.0   # 48 ms
NSYM = 103
TONE_SPACING = 12000.0 / NSPS  # 20.833 Hz
TX_START = 0.5         # nominal signal start within a period (DT = 0)
SIGNAL_SEC = (NSYM + 2) * SYMBOL_SEC  # 5.04 s incl. the ramp symbols
NAME = "FT4"
WIDTH = 83             # Hz: four tones 20.8 Hz apart
DECODE_AT = 6.2        # signals end by ~6.0 s (WSJT-X decodes FT4 at 6.05 s)
# Late starts: tested, still decodes at -14 dB when started 1 s late (2 s: no).
LATE_START_MAX = 1.0
JT9_MODE = "-5"
UDP_MODE = "+"
ADIF = {"mode": "MFSK", "submode": "FT4"}   # ADIF files FT4 under MFSK

# Scrambling vector XORed onto the 77 message bits (genft4.f90), so a run of
# zeros (an empty message) doesn't come out as a run of identical tones.
RVEC = (0, 1, 0, 0, 1, 0, 1, 0, 0, 1, 0, 1, 1, 1, 1, 0, 1, 0, 0, 0, 1, 0, 0, 1,
        1, 0, 1, 1, 0, 1, 0, 0, 1, 0, 1, 1, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0,
        0, 1, 1, 1, 1, 0, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 1, 0, 1, 1, 1, 1, 1, 0,
        0, 0, 1, 0, 1)
COSTAS = ((0, 1, 3, 2), (1, 0, 2, 3), (2, 3, 1, 0), (3, 2, 0, 1))
GRAYMAP = (0, 1, 3, 2)

def tones_from_bits(bits77) -> tuple:
    """103 FT4 tones (0-3) for 77 message bits (before scrambling)."""
    cw = codeword([b ^ r for b, r in zip(bits77, RVEC)])
    data = [GRAYMAP[2 * cw[2 * i] + cw[2 * i + 1]] for i in range(87)]
    return (COSTAS[0] + tuple(data[0:29]) + COSTAS[1] + tuple(data[29:58])
            + COSTAS[2] + tuple(data[58:87]) + COSTAS[3])


def message_bits(msg: str):
    """(77 bits, decoded_text) for `msg` - the same packing as FT8."""
    return _message_bits(msg)


@functools.lru_cache(maxsize=512)
def encode(msg: str):
    """Return (tones, decoded_text), like ft8.encode but for FT4."""
    bits, decoded = message_bits(msg)
    return tones_from_bits(bits), decoded


@functools.lru_cache(maxsize=4)
def _pulse(nsps: int) -> np.ndarray:
    t = (np.arange(1, 3 * nsps + 1) - 1.5 * nsps) / nsps
    return _gfsk_pulse(1.0, t)


def synth(tones, f0: float, fs: int = 12000) -> np.ndarray:
    """Constant-envelope GFSK FT4 waveform (float, amplitude 1): one ramp-up
    symbol, the 103 tones, one ramp-down symbol (gen_ft4wave.f90)."""
    nsps = int(round(SYMBOL_SEC * fs))
    pulse = _pulse(nsps)
    nwave = (NSYM + 2) * nsps
    dphi_peak = 2 * pi / nsps
    dphi = np.zeros(nwave)
    for j, tone in enumerate(tones):
        dphi[j * nsps:j * nsps + 3 * nsps] += dphi_peak * pulse * tone
    dphi += 2 * pi * f0 / fs
    phi = np.concatenate(([0.0], np.cumsum(dphi[:nwave - 1])))
    wave = np.sin(phi)
    ramp = np.arange(nsps)
    wave[:nsps] *= (1 - np.cos(2 * pi * ramp / (2 * nsps))) / 2
    k1 = (NSYM + 1) * nsps
    wave[k1:k1 + nsps] *= (1 + np.cos(2 * pi * ramp / (2 * nsps))) / 2
    return wave

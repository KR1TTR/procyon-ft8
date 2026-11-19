"""JS8 encoder: a 12-character JS8 frame (+ 3 flag bits) -> 79 tones -> audio.

Procyon's own JS8 encoder - no other program needed. Ported from JS8Call 2.2.0
(Jordan Sherer KN4CRD and contributors, GPLv3): lib/js8/genjs8.f90,
lib/ft8/encode174.f90 + ldpc_174_87_params.f90 (the older FT8 (174,87) code,
from WSJT-X by K1JT et al.), lib/crc12.cpp and Modulator.cpp.

What happens to a frame:
  12 characters x 6 bits + 3 flag bits = 75 bits
  + 12-bit checksum (CRC-12, poly 0xC06, then XOR 42)        = 87 bits
  + 87 parity bits from the (174,87) LDPC code               = 174 bits
  -> 58 data symbols of 3 bits (tone 0-7, no Gray code)
  + three 7-tone Costas sync patterns: S7 D29 S7 D29 S7      = 79 symbols
  -> 8-tone continuous-phase FSK, tone spacing = 12000 / samples-per-symbol.

The packing of typed text into frames (varicode.cpp) is a separate step.
"""
import numpy as np

FS = 12000

# The 64 characters a frame can hold (6 bits each).
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-+"

# JS8 speeds: samples per symbol at 12 kHz, slot length (s), when the signal
# starts in the slot (s), which sync patterns (1 = original, 2 = newer).
SPEEDS = {
    "normal": dict(nsps=1920, period=15, start=0.5, costas=1, letter="A"),
    "fast":   dict(nsps=1200, period=10, start=0.2, costas=2, letter="B"),
    "turbo":  dict(nsps=600,  period=6,  start=0.1, costas=2, letter="C"),
    "slow":   dict(nsps=3840, period=30, start=0.5, costas=2, letter="E"),
}

COSTAS = {
    1: ([4, 2, 5, 6, 1, 3, 0], [4, 2, 5, 6, 1, 3, 0], [4, 2, 5, 6, 1, 3, 0]),
    2: ([0, 6, 2, 3, 5, 4, 1], [1, 5, 0, 2, 3, 6, 4], [2, 5, 0, 6, 4, 1, 3]),
}

# (174,87) LDPC generator: 87 rows of 87 bits (22 hex digits, last bit unused).
_GEN_HEX = (
    "23bba830e23b6b6f50982e1f8e55da218c5df3309052ca7b3217cd92bd59a5ae20"
    "56f78313537d0f4382964e29c29dba9c545e267762fe6be396b5e2e819e373340c"
    "293548a138858328af4210cb6c6afcdc28bb3f7c6e863f2a86f5c5bd225c961150"
    "849dd2d63673481860f62c56cdaec6e7ae14b43feeee04ef5cfa3766ba778f45a4"
    "c525ae4bd4f627320a3974fe37802941d66dde02b99c41fd9520b2e4abeb2f989c"
    "40907b01280f03c03239467fb36c24085a34d8c1dbc440fc3e44bb7d2bb2756e44"
    "d38ab0a1d2e52a8ec3bc763d0f929ef3949bd84d473445d3814f504064f80549ae"
    "f14dbf263825d0bd04b05ef08a91fb2e1f78290619a87a8dec79a51e8ac5388022"
    "ca4186dd44c3121565cf5cdb714f8f64e8ac7af1a76e8d0274de71e7c1a8055eb0"
    "51f81573dd4049b082de14d037db825175d851f3af00d8f937f31822e57c562370"
    "1bf1490607c54032660ede1616d78018d0b4745ca0f2a9fa8e50bcb032c85e3304"
    "83f640f1a48a8ebc0443eaeca9afa0f6b01d92305edc3776af54ccfbae916afde6"
    "6abb212d9739dfc02580f205209a0abb530b9e7e34b0612f63acc025b6ab476f7c"
    "0af7723161ec223080be86a8fc906976c35669e79ce045b7ab6242b77474d9f11a"
    "b274db8abd3c6f396ea3569059dfa2bb20ef7ef73ad43d188ea477f6fa41317a4e"
    "8d9071b7e7a6a2eed6965ea377253773ea678367c3f6ecbd7c73b9cd34c3720c8a"
    "b6537f417e61d1a70853366c280d2a0523d9c4bc5946d36d662a69ae24b74dcbd8"
    "d747bfc5fd65ef70fbd9bca9fa2eefa6f8796a355772cc9da55fe046d0cb3a770c"
    "f6ad4824b87c80ebfce466cc6de59755420925f90ed2164cc861bdd803c547f2ac"
    "c0fc3ec4fb7d2bb27566440dbd816fba1543f721dc72a0c0033a52ab6299802fd2"
    "bf4f56e073271f6ab4bf8057da6d13cb96a7689b279081cfc6f18c35b1e1f17114"
    "481a2a0df8a23583f82d6c1ac4672b549cd6dba79bccc87af9a5d5206abca532a8"
    "97d4169cb33e7435718d90a6573f3dc8b16c9d19f7462c4142bf42b01e71076acc"
    "081c29a10d468ccdbcecb65b0f7742bca86b8012609a012dee2198eba82b19a1da"
    "f1627701a2d692fd9449e635ad3fb0faeb5f1b0c30dcb1ca4ea2e3d173bad4379c"
    "37d8e0af9258b9e8c5f9b2cd921fdf59e882683763f66114e08483043fd3f38a8a"
    "2e547dd7a05f6597aac51695e45ecd0135aca9d6e6aeb33ec97be83ce413f9acc8"
    "c8b5dffc335095dcdcaf2a3dd01a59d86310743ec75214cd0f642fc0c5fe3a65ca"
    "3a0a1dfd7eee29c2e827e08abdb889efbe39a510a1183f231f212055371cf3e2a2"
)
_COLORDER = [
    0, 1, 2, 3, 30, 4, 5, 6, 7, 8, 9, 10, 11, 32, 12, 40, 13, 14, 15, 16,
    17, 18, 37, 45, 29, 19, 20, 21, 41, 22, 42, 31, 33, 34, 44, 35, 47, 51, 50, 43,
    36, 52, 63, 46, 25, 55, 27, 24, 23, 53, 39, 49, 59, 38, 48, 61, 60, 57, 28, 62,
    56, 58, 65, 66, 26, 70, 64, 69, 68, 67, 74, 71, 54, 76, 72, 75, 78, 77, 80, 79,
    73, 83, 84, 81, 82, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99,
    100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119,
    120, 121, 122, 123, 124, 125, 126, 127, 128, 129, 130, 131, 132, 133, 134, 135, 136, 137, 138, 139,
    140, 141, 142, 143, 144, 145, 146, 147, 148, 149, 150, 151, 152, 153, 154, 155, 156, 157, 158, 159,
    160, 161, 162, 163, 164, 165, 166, 167, 168, 169, 170, 171, 172, 173,
]


def _gen_matrix():
    rows = [_GEN_HEX[i * 22:(i + 1) * 22] for i in range(87)]
    m = np.zeros((87, 87), dtype=np.uint8)
    for i, h in enumerate(rows):
        bits = bin(int(h, 16))[2:].zfill(88)
        m[i] = [int(b) for b in bits[:87]]
    return m


_GEN = _gen_matrix()


def crc12(bits) -> int:
    """JS8's checksum of the 75 message bits (boost augmented_crc<12, 0xC06>
    over 11 bytes = the 75 bits followed by zeros), XOR 42."""
    data = list(bits) + [0] * (88 - len(bits))
    reg = 0
    for b in data:
        top = (reg >> 11) & 1
        reg = ((reg << 1) | b) & 0xFFF
        if top:
            reg ^= 0xC06
    return reg ^ 42


def frame_bits(frame: str, flags: int = 0) -> list[int]:
    """12-character frame + 3 flag bits -> the 87 message bits (with checksum)."""
    if len(frame) != 12 or any(c not in ALPHABET for c in frame):
        raise ValueError(f"a JS8 frame is 12 characters from the JS8 alphabet: {frame!r}")
    if not 0 <= flags <= 7:
        raise ValueError("flags are 3 bits (0-7)")
    bits = []
    for c in frame:
        v = ALPHABET.index(c)
        bits += [(v >> (5 - k)) & 1 for k in range(6)]
    bits += [(flags >> 2) & 1, (flags >> 1) & 1, flags & 1]
    crc = crc12(bits)
    bits += [(crc >> (11 - k)) & 1 for k in range(12)]
    return bits


def ldpc_encode(msg87) -> np.ndarray:
    """87 message bits -> 174-bit codeword (parity first, then reordered)."""
    m = np.array(msg87, dtype=np.uint8)
    parity = (_GEN.astype(np.int32) @ m) % 2
    tmp = np.concatenate([parity, m]).astype(np.uint8)
    cw = np.zeros(174, dtype=np.uint8)
    cw[_COLORDER] = tmp
    return cw


def tones(frame: str, flags: int = 0, speed: str = "normal") -> list[int]:
    """The 79 tones (0-7) of a frame."""
    cw = ldpc_encode(frame_bits(frame, flags))
    a, b, c = COSTAS[SPEEDS[speed]["costas"]]
    data = [int(cw[3 * j] * 4 + cw[3 * j + 1] * 2 + cw[3 * j + 2]) for j in range(58)]
    return a + data[:29] + b + data[29:] + c


def synth(tone_list, f0: float, speed: str = "normal", fs: int = FS) -> np.ndarray:
    """Audio (float, -1..1) for the tones, lowest tone at f0 Hz.
    Continuous phase; a short fade at each end so it doesn't click."""
    nsps = SPEEDS[speed]["nsps"] * fs // FS
    spacing = fs / nsps
    freq = np.repeat(f0 + np.array(tone_list, dtype=float) * spacing, nsps)
    phase = 2 * np.pi * np.cumsum(freq) / fs
    wave = np.sin(phase)
    n = nsps // 16
    ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(n) / n)
    wave[:n] *= ramp
    wave[-n:] *= ramp[::-1]
    return wave

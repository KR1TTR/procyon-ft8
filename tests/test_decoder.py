"""The decoder (tpdec): make FT8 and FT4 signals with Procyon's own
transmitter code, bury them in noise at a known strength, and check they
come back word for word - and that pure noise gives nothing."""

import numpy as np

from _common import MYCALL

from procyon import ft4, ft8, pack77
from procyon.decoder import ft4search, ft8search

FS = 12000


def band(mode, signals, seed=1):
    """One cycle of audio: white noise plus (message, Hz, SNR in dB in 2500 Hz)."""
    rng = np.random.default_rng(seed)
    audio = rng.normal(0, 1.0, int(mode.PERIOD * FS))
    for msg, hz, snr in signals:
        tones, _ = mode.encode(msg)
        wave = mode.synth(tones, hz)
        amp = np.sqrt(2 * 10 ** (snr / 10) * 2500 / (FS / 2))
        start = int(0.5 * FS)
        audio[start:start + len(wave)] += amp * wave
    return audio * 1000


def texts(found):
    return sorted(d["text"] for d in found)


FT8_MSGS = [("CQ W9XYZ EN37", 600, -8), ("K1ABC W9XYZ -05", 900, -12), ("W9XYZ K1ABC R-12", 1200, -15),
            ("CQ POTA N0DEF EM48", 1500, -10), ("<3E40CDW> K1ABC RR73", 1800, -10), ("W9XYZ K1ABC 73", 2100, -6)]


def test_ft8_signals_come_back_word_for_word():
    found, _ = ft8search.decode_cycle(band(ft8, FT8_MSGS), hashes=pack77.Hashes())
    want = sorted(m for m, _, _ in FT8_MSGS)
    got = texts(found)
    # The long call goes out as a 22-bit code; a fresh receiver that has
    # never seen 3E40CDW in full shows it as <...>, just like WSJT-X.
    assert got == sorted(w.replace("<3E40CDW>", "<...>") for w in want), got
    assert all(min(abs(d["freq"] - hz) for _, hz, _ in FT8_MSGS) <= 3 for d in found), "frequencies"


def test_ft8_reports_signal_strength_about_right():
    found, _ = ft8search.decode_cycle(band(ft8, [("CQ W9XYZ EN37", 1000, -10)]))
    assert len(found) == 1 and abs(found[0]["snr"] - (-10)) <= 3, found


def test_ft8_weak_signal():
    found, _ = ft8search.decode_cycle(band(ft8, [("CQ W9XYZ EN37", 1000, -19)], seed=3))
    assert texts(found) == ["CQ W9XYZ EN37"]


def test_ft4_signals_come_back_word_for_word():
    msgs = [("CQ W9XYZ EN37", 700, -8), ("K1ABC W9XYZ R-05", 1300, -10), ("W9XYZ K1ABC RR73", 1900, -12)]
    found, _ = ft4search.decode_cycle(band(ft4, msgs), hashes=pack77.Hashes())
    assert texts(found) == sorted(m for m, _, _ in msgs)


def test_pure_noise_gives_nothing():
    """No made-up stations: 12 cycles of noise, with the guessing (a priori)
    switched on as it is on the air, must give no decodes at all."""
    ap = {"mycall": MYCALL, "dxcall": "W9XYZ", "dxfreq": 1500}
    for seed in range(8):
        found, _ = ft8search.decode_cycle(band(ft8, [], seed=100 + seed), ap=ap)
        assert not found, f"FT8 noise gave {texts(found)}"
    for seed in range(4):
        found, _ = ft4search.decode_cycle(band(ft4, [], seed=200 + seed), ap=ap)
        assert not found, f"FT4 noise gave {texts(found)}"


def test_streaming_hands_over_everything_once():
    """0.9.63-style streaming: the batches handed over as decoding goes
    on add up to exactly the final list - nothing lost, nothing twice."""
    batches = []
    found, _ = ft8search.decode_cycle(band(ft8, FT8_MSGS), on_decodes=batches.append)
    streamed = [d["text"] for b in batches for d in b]
    assert sorted(streamed) == texts(found) and len(streamed) == len(set(streamed))


def test_a_known_long_call_is_shown_in_full():
    """With 3E40CDW heard in full before (it's in the hash memory), its
    bracketed code reads back as the call - no more <...>."""
    h = pack77.Hashes()
    h.add("3E40CDW")
    found, _ = ft8search.decode_cycle(band(ft8, [("<3E40CDW> K1ABC RR73", 1000, -8)]), hashes=h)
    assert texts(found) == ["<3E40CDW> K1ABC RR73"]

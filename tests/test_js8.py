"""The JS8 modem (js8 branch, for 1.1): typed text -> frames -> audio ->
Procyon's own JS8 decoder -> frames -> the same text, at every speed, and
pure noise gives nothing. No JS8Call needed."""

import numpy as np

import _common  # noqa: F401 - shared setup (no radio, no sound card)

from procyon.js8 import js8dec, js8enc, js8msg

FS = js8enc.FS
SPEEDS = ("normal", "fast", "turbo", "slow")


def slot(speed, signals, seed=1):
    """One slot of audio: white noise plus (frame, flags, Hz, SNR in dB in 2500 Hz)."""
    p = js8enc.SPEEDS[speed]
    rng = np.random.default_rng(seed)
    audio = rng.normal(0, 1.0, int(p["period"] * FS))
    for frame, flags, hz, snr in signals:
        wave = js8enc.synth(js8enc.tones(frame, flags, speed), hz, speed)
        amp = np.sqrt(2 * 10 ** (snr / 10) * 2500 / (FS / 2))
        i = int(p["start"] * FS)
        n = min(len(wave), len(audio) - i)
        audio[i:i + n] += amp * wave[:n]
    return audio * 1000


def norm(s):
    return " ".join(s.split())


MESSAGES = [
    ("N8GHI SNR?", "KR1TTR: N8GHI SNR?"),
    ("CQ CQ EN80", "KR1TTR: @ALLCALL CQ CQ EN80"),
    ("HB EN80", "KR1TTR: @HB HEARTBEAT EN80"),
    ("N8GHI RIG HERE IS KX3 AT 10W INTO AN EFHW, WX CLOUDY 12C. 73",
     "KR1TTR: N8GHI RIG HERE IS KX3 AT 10W INTO AN EFHW, WX CLOUDY 12C. 73"),
    ("VE3/N8GHI SNR?", "KR1TTR: VE3/N8GHI SNR?"),
    ("@CHESS CQ", "KR1TTR: @CHESS CQ"),
]


def test_messages_pack_and_unpack():
    for speed in SPEEDS:
        for text, want in MESSAGES:
            frames = js8msg.build_frames("KR1TTR", "EN80", text, force_identify=True, speed=speed)
            assert frames[0][1] & js8msg.FLAG_FIRST and frames[-1][1] & js8msg.FLAG_LAST, (speed, text)
            got = norm(js8msg.decode_message(frames))
            assert got == want, (speed, text, got)


def test_a_compound_own_call_identifies_itself():
    frames = js8msg.build_frames("KR1TTR/QRP", "EN80", "N8GHI SNR?")
    assert norm(js8msg.decode_message(frames)) == "KR1TTR/QRP: N8GHI SNR?"


def test_a_whole_message_comes_back_through_audio_at_every_speed():
    text, want = MESSAGES[3]
    for speed in SPEEDS:
        frames = js8msg.build_frames("KR1TTR", "EN80", text, force_identify=True, speed=speed)
        heard = []
        for k, (frame, flags) in enumerate(frames):     # one frame per slot, as on the air
            found = js8dec.decode(slot(speed, [(frame, flags, 1400, 0)], seed=k), speed)
            assert len(found) == 1, (speed, k, found)
            d = found[0]
            assert abs(d["freq"] - 1400) <= 3 and abs(d["dt"]) <= 0.1, (speed, d)
            heard.append((d["frame"], d["flags"]))
        assert heard == frames, (speed, heard, frames)
        assert norm(js8msg.decode_message(heard)) == want, speed


def test_several_stations_in_one_slot():
    frames = [js8msg.build_frames(c, "EN80", "HB EN80")[0] for c in ("K1ABC", "W9XYZ", "N0DEF", "VE3JKL")]
    sigs = [(f, b, hz, snr) for (f, b), hz, snr in zip(frames, (500, 900, 1600, 2300), (-5, -10, -14, 0))]
    found = js8dec.decode(slot("normal", sigs), "normal")
    assert sorted((d["frame"], d["flags"]) for d in found) == sorted(frames), found


# Reports are on the JS8Call program's scale (what people compare with), which
# reads about 4 dB above the true strength in 2500 Hz - the same at every level.
APP_SCALE = 4


def test_signal_report_about_right():
    frame, flags = js8msg.build_frames("K1ABC", "FN42", "HB FN42")[0]
    for true in (-16, -10, 0, 10):          # straight: strong signals aren't squeezed
        found = js8dec.decode(slot("normal", [(frame, flags, 1000, true)]), "normal")
        assert len(found) == 1 and abs(found[0]["snr"] - (true + APP_SCALE)) <= 2, (true, found)


def test_pure_noise_gives_nothing():
    for speed in SPEEDS:
        for seed in range(3):
            found = js8dec.decode(slot(speed, [], seed=100 + seed), speed)
            assert found == [], (speed, seed, found)

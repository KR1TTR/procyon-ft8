"""JS8 transmit audio (ProcyonEX step 1 of JS8 TX): reports stay inside
what JS8 can hold, messages come back word for word through our own
decoder, slots start on time, and the test player only uses the test sink."""

import asyncio

from test_js8 import slot  # noqa: F401 - also does the shared setup

import numpy as np

from procyon.js8 import js8dec, js8enc, js8msg, tx


def test_reports_are_kept_inside_what_js8_can_send():
    assert tx.clamp_reports("N8GHI SNR -60") == "N8GHI SNR -30"
    assert tx.clamp_reports("N8GHI HEARTBEAT SNR -34") == "N8GHI HEARTBEAT SNR -30"
    assert tx.clamp_reports("N8GHI SNR +45") == "N8GHI SNR +31"
    assert tx.clamp_reports("N8GHI SNR -6") == "N8GHI SNR -06"
    assert tx.clamp_reports("N8GHI SNR?") == "N8GHI SNR?"
    assert tx.clamp_reports("N8GHI 599 FROM OHIO") == "N8GHI 599 FROM OHIO"
    # JS8Call would send -60 as two frames ("-06" + "0"); we send one
    frames = tx.frames_for("KR1TTR", "EN80", "N8GHI SNR -60")
    assert len(frames) == 1, frames
    assert " ".join(js8msg.decode_message(frames).split()) == "KR1TTR: N8GHI SNR -30"


def test_typed_text_is_tidied_and_empty_text_refused():
    assert tx.frames_for("KR1TTR", "EN80", "  n8ghi   snr? ") == tx.frames_for("KR1TTR", "EN80", "N8GHI SNR?")
    for bad in ("", "   "):
        try:
            tx.frames_for("KR1TTR", "EN80", bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} was accepted")


def test_sent_audio_reads_back_at_every_speed():
    for speed in ("normal", "fast", "turbo", "slow"):
        sp = js8enc.SPEEDS[speed]
        frames = tx.frames_for("KR1TTR", "EN80", "N8GHI HEARTBEAT SNR -34", speed)
        for k, (f, fl) in enumerate(frames):
            audio = np.random.default_rng(k).normal(0, 300, int(sp["period"] * tx.FS))
            sig = tx.to_int16(tx.frame_audio(f, fl, 1500, speed)).astype(float) * 0.05
            i = int(sp["start"] * tx.FS)
            audio[i:i + len(sig)] += sig
            found = js8dec.decode(audio, speed)
            assert [(d["frame"], d["flags"]) for d in found] == [(f, fl)], (speed, found)
            assert abs(found[0]["dt"]) <= 0.1 and abs(found[0]["freq"] - 1500) <= 3, found
        assert abs(tx.signal_seconds(speed) - 79 * sp["nsps"] / tx.FS) < 1e-9


def test_frames_go_out_in_back_to_back_slots_on_time():
    # Normal: slots every 15 s, signal 0.5 s in
    assert tx.plan(3, "normal", now=1000 * 15 + 0.0) == [15000.5, 15015.5, 15030.5]
    assert tx.plan(1, "normal", now=15000.2) == [15015.5]          # too late for this slot
    assert tx.plan(2, "turbo", now=600 * 6 - 1) == [3600.1, 3606.1]
    for speed in ("normal", "fast", "turbo", "slow"):
        sp = js8enc.SPEEDS[speed]
        for t in tx.plan(4, speed, now=123456.7):
            assert abs((t - sp["start"]) % sp["period"]) < 1e-6, (speed, t)
        assert tx.signal_seconds(speed) + sp["start"] < sp["period"], speed   # ends inside its slot


def test_the_test_player_refuses_real_sound_devices():
    assert tx.test_device("procyon_test") and tx.test_device("procyon_test.monitor")
    for dev in ("", None, "alsa_output.usb-C-Media_USB_Audio_Device-00.analog-stereo", "default"):
        assert not tx.test_device(dev)
        try:
            asyncio.run(tx.play_test(dev, [("xiJ+++++++++", 3)], 1500, "turbo"))
        except ValueError:
            continue
        raise AssertionError(f"played into {dev!r}")

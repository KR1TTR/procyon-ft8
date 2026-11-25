"""JS8 receive in Procyon (ProcyonEX, listen only): frames join into
messages like JS8Call, and while JS8 is picked nothing can transmit, the
radio moves to the JS8 frequency and back, and the demo band is left alone."""

import asyncio
import json
import os

from _common import scratch_dir

from procyon.engine import Engine
import numpy as np

from procyon.js8 import js8msg, tx
from procyon.js8.rx import Js8Rx
from procyon.js8.rx import Assembler, as_message, message_text


async def _noop(*a, **k):
    return None


def _engine(demo=False, dial=7_074_000):
    folder = scratch_dir("js8rx")
    with open(os.path.join(folder, "settings.json"), "w") as f:
        json.dump({"mycall": "K1ABC", "mygrid": "FN42", "rig_enabled": False, "dial_hz": dial}, f)
    e = Engine(folder, force_demo=demo)
    for name in ("restart_audio", "_configure_udp", "push_state", "_kick_remote_clients", "broadcast"):
        setattr(e, name, _noop)
    e.toasts = []
    e.toast = lambda text, kind="info": e.toasts.append(text)
    return e


def test_frames_join_into_one_message_across_slots():
    frames = js8msg.build_frames("W9XYZ", "EN52", "K1ABC RIG HERE IS KX3 AT 10W INTO AN EFHW, WX CLOUDY 12C. 73",
                                 force_identify=True)
    assert len(frames) >= 3
    asm, got = Assembler(), []
    for slot, (frame, flags) in enumerate(frames):
        got += asm.add(1203 + slot % 2, frame, flags, -10, slot)   # a hertz or two of drift
    kinds = [k for k, _ in got]
    assert kinds == ["part"] * (len(frames) - 1) + ["msg"], kinds
    msg = as_message(*got[-1])
    assert msg["from"] == "W9XYZ" and msg["freq"] in (1203, 1204), msg
    assert " ".join(msg["text"].split()) == "K1ABC RIG HERE IS KX3 AT 10W INTO AN EFHW, WX CLOUDY 12C. 73", msg


def test_a_message_missing_its_end_is_closed_after_three_quiet_slots():
    frames = js8msg.build_frames("W9XYZ", "EN52", "K1ABC HELLO THERE HOW ARE YOU TODAY", force_identify=True)
    asm = Assembler()
    asm.add(800, *frames[0], -5, 10)            # the rest never arrives
    assert asm.expire(12) == []
    done = asm.expire(13)
    assert len(done) == 1 and done[0][0] == "msg"


def test_js8_moves_the_radio_and_blocks_transmitting():
    async def run():
        e = _engine()
        e.qso.tx_enabled = True
        await e._js8_on()
        try:
            assert e.js8 is not None and e.s["dial_hz"] == 7_078_000
            assert not e.qso.tx_enabled
            await e.command({"type": "cq"})
            await e.command({"type": "tx_enable", "on": True})
            assert not e.qso.tx_enabled, "JS8 is listen only"
            await e.tune(True)
            assert e.tune_task is None, "no Tune while JS8 listens"
            # another band while in JS8: its JS8 frequency
            assert e._band_dial(next(b for b in __import__("procyon.geo").geo.BANDS if b[0] == "20m")) == 14_078_000
        finally:
            await e._js8_off()
        assert e.js8 is None and e.s["dial_hz"] == 7_074_000, "back where it was"
    asyncio.run(run())


def test_the_demo_band_keeps_its_pretend_js8():
    async def run():
        e = _engine(demo=True)
        await e._js8_on()
        assert e.js8 is None and e.s["dial_hz"] == 7_074_000
    asyncio.run(run())


def test_nothing_keys_while_js8_listens_even_if_tx_gets_switched_on():
    """Safety: GridTracker (UDP) or anything else turning transmit on while
    JS8 is picked must not start a transmission."""
    async def run():
        e = _engine()
        await e._js8_on()
        try:
            e.qso.call_cq()
            e.qso.tx_enabled = True          # sneaks past the command checks
            e.qso.parity = 0
            sched = asyncio.create_task(e._scheduler())
            await asyncio.sleep(0.4)
            sched.cancel()
            assert e.tx_task is None and not e.tx_periods, "a transmission was started"
            assert not e.qso.tx_enabled
        finally:
            await e._js8_off()
    asyncio.run(run())


def test_each_mode_goes_to_its_own_frequency_even_with_a_slow_radio():
    """FT8 -> JS8 -> FT4 -> JS8 -> FT8 on 40 m: each lands on its own usual
    frequency. The pretend radio only moves when Procyon has finished asking
    (like a real one answering slowly), so a quick JS8 -> FT4 switch sees the
    radio still on the JS8 frequency."""
    async def run():
        e = _engine(dial=7_076_000)                  # tuned by hand, off the usual spot
        e.rig.enabled = e.rig.connected = True
        e.rig.freq = 7_076_000
        asked = []

        async def slow_set_freq(hz):
            await asyncio.sleep(0.05)
            asked.append(hz)
            e.rig.freq = hz
        e.rig.set_freq = slow_set_freq
        e._apply_rig_mode = _noop

        async def settle():
            await asyncio.sleep(0.3)
            return e.rig.freq

        await e._js8_on()
        assert await settle() == 7_078_000
        await e._js8_off()                           # the page sends these two at once
        await e._set_mode("FT4")
        assert e.s["dial_hz"] == 7_047_500
        assert await settle() == 7_047_500, asked
        await e._js8_on()
        assert await settle() == 7_078_000
        await e._js8_off()
        await e._set_mode("FT8")
        assert await settle() == 7_074_000, asked
        e.rig.freq = 7_076_500                       # tuned by hand again
        await e._set_mode("FT4")
        assert await settle() == 7_047_500
        await e._set_mode("FT8")
        assert await settle() == 7_074_000
    asyncio.run(run())


def test_a_report_split_by_js8calls_packer_is_joined_back():
    """Real frames from the air (40 m, 2026-11-21): JS8Call can only pack
    reports -30..+31, so WA7TFT's -60 went out as "-06" + a frame "0" and
    N7JFF's -34 as "-03" + "4". Procyon shows the real report."""
    wa7tft = [("UqY1BHMbxVqP", 1), ("wK++++++++++", 2)]
    n7jff = [("VlTOQHMafJqS", 1), ("y4++++++++++", 2)]
    assert " ".join(js8msg.decode_message(wa7tft).split()) == "WA7TFT: KJ5QFP HEARTBEAT SNR -06 0"
    assert as_message("msg", {"frames": wa7tft, "snr": -5, "hz": 1500})["text"] == "KJ5QFP HEARTBEAT SNR -60"
    assert as_message("msg", {"frames": n7jff, "snr": -5, "hz": 1500})["text"] == "KJ5MQE HEARTBEAT SNR -34"


def test_ordinary_reports_and_text_are_left_alone():
    def shown(text):
        return " ".join(message_text(js8msg.build_frames("W9XYZ", "EN52", text)).split())
    assert shown("K1ABC SNR -06") == "W9XYZ: K1ABC SNR -06"           # one frame, nothing to join
    assert shown("K1ABC SNR +12") == "W9XYZ: K1ABC SNR +12"
    assert shown("K1ABC HELLO 5") == "W9XYZ: K1ABC HELLO 5"           # a lone digit after other text
    long = shown("K1ABC SNR -06 THANKS FOR THE REPORT")               # more text after the report
    assert long == "W9XYZ: K1ABC SNR -06 THANKS FOR THE REPORT", long


def test_our_own_transmission_is_not_shown_as_heard():
    """While we transmit, the radio's receive audio still lets a little of our
    own signal through (seen on the dummy load, 2026-11-21: "?: DE KR1TTR
    TEST ..." in the chat). Slots we sent in aren't decoded, like JS8Call."""
    async def run(keyed):
        frame = js8msg.build_frames("K1ABC", "FN42", "DE K1ABC TEST", speed="turbo")[-1]
        x = np.random.default_rng(5).normal(0, 300, 6 * 12000)
        i = int(0.1 * 12000)
        sig = tx.frame_audio(*frame, 1500, "turbo") * 3000
        x[i:i + len(sig)] += sig
        t0 = 6000.0
        out = []

        async def send(o):
            out.append(o["m"])
        rx = Js8Rx(lambda a, b: x.astype("<i2"), lambda n: None, lambda: t0 + 4.5,
                   lambda: 0.0, lambda: ("radio", None), send)
        rx.speed = "Turbo"
        rx.keyed = keyed
        await rx._one_slot()
        return [m for m in out if m["type"] in ("part", "msg")]
    heard = asyncio.run(run(None))
    assert heard and "TEST" in heard[-1]["text"], heard       # someone else's signal: shown
    assert asyncio.run(run(lambda a, b: True)) == [], "our own signal: not shown"


def test_silence_while_we_transmit_is_not_called_a_dead_radio():
    """The radio mutes its receiver while we transmit, so the input goes
    almost silent. That must not set off "no audio coming in" (seen on air,
    2026-11-22); real silence with nothing sent still does."""
    from procyon.js8 import rx as rxmod
    now = 9000.0
    sent = [(now - 13.0, now - 0.5)]           # a frame that ended half a second ago

    def keyed(a, b):
        return any(x < b and a < y for x, y in sent)
    r = Js8Rx(lambda a, b: None, lambda n: None, lambda: now, lambda: 0.0, lambda: ("radio", None), None)
    r.keyed = keyed
    assert not r._audio_low(-90), "our own transmission's silence counted as a dead radio"
    now += rxmod.TX_QUIET_GRACE + 1                # well after we stopped
    assert r._audio_low(-90), "real silence must still be reported"
    r.keyed = None
    assert r._audio_low(-90) and not r._audio_low(-30)

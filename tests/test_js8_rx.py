"""JS8 receive in Procyon (ProcyonEX, listen only): frames join into
messages like JS8Call, and while JS8 is picked nothing can transmit, the
radio moves to the JS8 frequency and back, and the demo band is left alone."""

import asyncio
import json
import os

from _common import scratch_dir

from procyon.engine import Engine
from procyon.js8 import js8msg
from procyon.js8.rx import Assembler, as_message


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

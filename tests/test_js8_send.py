"""JS8 sending (ProcyonEX step 2 of JS8 TX): Send in the chat screen goes
out through Procyon's own PTT + audio path, one frame per slot, on time -
and every way of stopping it (Stop/Halt, band change, quit, leaving JS8)
cuts it at once with the radio back on receive. Nothing sends by itself.

No radio and no sound card: the radio's PTT and the sound output are
stand-ins that write down what they were asked to do, and the clock jumps
ahead instead of waiting for real slots."""

import asyncio
import json
import os
import time

import numpy as np

from _common import scratch_dir

from procyon.engine import Engine
from procyon.geo import BANDS
from procyon.js8 import js8dec, js8enc, js8msg

TEXT = "W9XYZ HELLO THERE HOW ARE YOU TODAY"
PER, START = 6, 0.1                              # Turbo slots


async def _noop(*a, **k):
    return None


class Station:
    """An engine with a pretend radio (PTT log) and a pretend sound card."""

    def __init__(self, mycall="K1ABC", rig=True, hold=False):
        folder = scratch_dir("js8send")
        with open(os.path.join(folder, "settings.json"), "w") as f:
            json.dump({"mycall": mycall, "mygrid": "FN42", "rig_enabled": False, "dial_hz": 7_074_000}, f)
        e = self.e = Engine(folder)
        for name in ("restart_audio", "_configure_udp", "push_state", "_kick_remote_clients"):
            setattr(e, name, _noop)
        self.sent = []                           # what the chat screen was told
        self.ptt = []                            # True/False, in order
        self.played = []                         # (target, audio at 12 kHz, keyed?)
        self.toasts = []
        self.shift = 0.0
        self.hold = hold                         # play() waits for Halt (a frame in progress)
        self.playing = asyncio.Event()

        async def broadcast(obj):
            self.sent.append(obj)
        e.broadcast = broadcast
        e.toast = lambda text, kind="info": self.toasts.append(text)
        # the band's clock: 0.8 s before a Turbo slot starts, and it jumps on
        # while a frame "plays", so nobody waits for real slots
        now = time.time()
        self.shift = (now // PER + 1) * PER - 0.8 - now
        e.band_time = lambda: time.time() + self.shift
        if rig:
            e.rig.enabled = e.rig.connected = True

            async def ptt(on):
                self.ptt.append(bool(on))
                return True
            e.rig.ptt = ptt
            e.rig.set_freq = _noop
            e._apply_rig_mode = _noop

        async def play(device, make_wave, target, halt, gain):
            self.played.append((target, make_wave(12000), self.ptt[-1:] == [True]))
            self.playing.set()
            if self.hold:
                try:
                    await asyncio.wait_for(halt.wait(), 5)
                except asyncio.TimeoutError:
                    pass
                return
            self.shift += PER                    # the frame took its slot
        e.audio.play = play

    async def js8_on(self):
        await self.e._js8_on()
        await self.e.js8.set_speed("Turbo")

    def done(self):
        return [m["m"] for m in self.sent if m.get("type") == "js8" and m["m"]["type"] == "txdone"]

    def plans(self):
        return [m["m"] for m in self.sent if m.get("type") == "js8" and m["m"]["type"] == "txplan"]


def heard(audio):
    """Our decoder on one Turbo slot holding that frame."""
    rng = np.random.default_rng(3)
    x = rng.normal(0, 1, PER * 12000)
    i = int(START * 12000)
    x[i:i + len(audio)] += 0.3 * audio
    return [(d["frame"], d["flags"]) for d in js8dec.decode(x * 1000, "turbo")]


def test_send_keys_the_radio_frame_by_frame_on_time():
    async def run():
        st = Station()
        e = st.e
        await st.js8_on()
        try:
            await e.command({"type": "js8_tx", "id": 7, "text": TEXT.lower(), "freq": 1400})
            await asyncio.wait_for(e.tx_task, 10)
        finally:
            await e._js8_off()
        frames = js8msg.build_frames("K1ABC", "FN42", TEXT, speed="turbo")
        n = len(frames)
        assert n >= 3 and len(st.played) == n, (n, len(st.played))
        assert st.ptt == [True, False] * n, st.ptt          # keyed for each frame, always released
        assert all(keyed for _, _, keyed in st.played), "audio only while keyed"
        starts = [t for t, _, _ in st.played]
        for a, b in zip(starts, starts[1:]):
            assert abs(b - a - PER) < 1e-6, starts        # back-to-back slots
        assert all(abs((t % PER) - START) < 1e-6 for t in starts), starts   # 0.1 s into each slot
        got = [heard(a) for _, a, _ in st.played]
        assert got == [[f] for f in frames], got           # the audio IS the message, at 1400 Hz
        assert " ".join(js8msg.decode_message(frames).split()) == "K1ABC: " + TEXT
        assert st.plans()[0]["n"] == n and st.done() == [{"type": "txdone", "id": 7}], st.done()
        assert e.tx_task is None and e.tx_info is None
    asyncio.run(run())


def test_stop_cuts_the_frame_and_drops_what_waits():
    async def run():
        st = Station(hold=True)
        e = st.e
        await st.js8_on()
        try:
            await e.command({"type": "js8_tx", "id": 1, "text": TEXT, "freq": 1500})
            await e.command({"type": "js8_tx", "id": 2, "text": "W9XYZ 73", "freq": 1500})
            await asyncio.wait_for(st.playing.wait(), 5)
            assert e.tx_info and e.tx_info["js8"], "ON AIR shows while a frame plays"
            await e.command({"type": "js8_txstop"})       # the chat screen's Stop (or Esc)
            await asyncio.wait_for(e.tx_task, 3)
        finally:
            await e._js8_off()
        assert len(st.played) == 1, "nothing after Stop"
        assert st.ptt == [True, False], st.ptt
        assert sorted(d["id"] for d in st.done()) == [1, 2] and all(d.get("stopped") for d in st.done())
        assert e.tx_info is None
    asyncio.run(run())


def test_halt_band_change_quit_and_leaving_js8_all_stop_it():
    async def one(how):
        st = Station(hold=True)
        e = st.e
        await st.js8_on()
        await e.command({"type": "js8_tx", "id": 1, "text": TEXT, "freq": 1500})
        await asyncio.wait_for(st.playing.wait(), 5)
        task = e.tx_task
        if how == "halt":                                # Procyon's own Halt button / Esc
            await e.command({"type": "halt"})
        elif how == "band":
            band = next(b for b in BANDS if b[0] == "20m")
            await e.command({"type": "band", "hz": e._band_dial(band)})
        elif how == "quit":
            await e.shutdown()
        elif how == "leave":                             # back to FT8
            await e.command({"type": "js8", "on": False})
        await asyncio.wait_for(asyncio.shield(task), 3)
        if e.js8 is not None:
            await e._js8_off()
        assert len(st.played) == 1, (how, len(st.played))
        assert st.ptt and st.ptt[-1] is False, (how, st.ptt)
        assert st.ptt.count(True) == 1, (how, st.ptt)
        assert [d.get("stopped") for d in st.done()] == [True], (how, st.done())
    for how in ("halt", "band", "quit", "leave"):
        asyncio.run(one(how))


def test_refused_without_a_callsign_or_a_way_to_key_the_radio():
    async def run():
        st = Station(mycall="")
        await st.js8_on()
        assert st.e.js8.hello()["tx"] is None, "no callsign: listen only"
        await st.e.command({"type": "js8_tx", "id": 1, "text": TEXT, "freq": 1500})
        await st.e._js8_off()
        assert st.played == [] and st.ptt == [] and "callsign" in st.done()[0]["error"]

        st = Station(rig=False)                          # PTT by CAT, but radio control off
        await st.js8_on()
        await st.e.command({"type": "js8_tx", "id": 1, "text": TEXT, "freq": 1500})
        await asyncio.wait_for(st.e.tx_task, 5)
        await st.e._js8_off()
        assert st.played == [], "no audio into a radio that isn't keyed"
        assert "radio control is off" in st.done()[0]["error"], st.done()
    asyncio.run(run())


def test_the_radio_not_confirming_ptt_stops_everything():
    async def run():
        st = Station()
        e = st.e

        async def ptt(on):
            st.ptt.append(bool(on))
            return not on                                # refuses to key
        e.rig.ptt = ptt
        await st.js8_on()
        await e.command({"type": "js8_tx", "id": 1, "text": TEXT, "freq": 1500})
        await e.command({"type": "js8_tx", "id": 2, "text": "W9XYZ 73", "freq": 1500})
        await asyncio.wait_for(e.tx_task, 5)
        await e._js8_off()
        assert st.played == [] and st.ptt[-1] is False
        d = {x["id"]: x for x in st.done()}
        assert "PTT" in d[1]["error"] and d[2].get("stopped"), d
    asyncio.run(run())


def test_nothing_goes_out_by_itself():
    """Only Send transmits: not the scheduler, not FT8's CQ or TX buttons,
    not GridTracker switching TX on."""
    async def run():
        st = Station()
        e = st.e
        await st.js8_on()
        try:
            await e.command({"type": "cq"})
            await e.command({"type": "tx_enable", "on": True})
            e.qso.call_cq()
            e.qso.tx_enabled = True                      # sneaks past the command checks
            sched = asyncio.create_task(e._scheduler())
            await asyncio.sleep(0.5)
            sched.cancel()
        finally:
            await e._js8_off()
        assert st.played == [] and st.ptt == [] and e.tx_task is None
    asyncio.run(run())

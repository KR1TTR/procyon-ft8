"""JS8 transmit AUDIO (ProcyonEX, for 1.1) - step 1 of JS8 TX.

Turns a typed message into JS8 frames (js8msg.build_frames, like JS8Call),
each frame into tones and audio (js8enc), and works out when each frame
goes out: one frame per slot, starting on time (Normal 0.5 s into each
15 s slot, Fast 0.2 s, Turbo 0.1 s, Slow 0.5 s).

SAFETY: this module has no radio control of its own - no PTT, no CAT, no
rigctld. Js8Tx (step 2) sends each frame through a play() it is GIVEN: the
engine's own _play, the same path FT8/FT4 use (PTT on, audio, PTT off in a
finally; Halt cuts the audio at once). Nothing here starts by itself: a
message goes out only when submit() is called (the user pressed Send).
The little test player at the end refuses every sound device except the
TEST one (the PulseAudio null sink procyon_test).
"""
import asyncio
import collections
import logging
import re
import time

import numpy as np

from . import js8enc, js8msg

FS = 12000
LEVEL = 0.3                              # audio level (of full scale) for the test player
TEST_SINKS = ("procyon_test",)           # the only devices play_test() will use
SNR_MIN, SNR_MAX = -30, 31               # what a JS8 report can hold

_SNR_RE = re.compile(r"(\bSNR\s+)([-+]?\d+)\b")


def clamp_reports(text):
    """Keep every report in "SNR n" inside -30..+31, written the JS8Call way
    (-06, +12). JS8Call itself sends e.g. -60 as "-06" plus an extra frame
    "0"; we never do that."""
    def fix(m):
        n = max(SNR_MIN, min(SNR_MAX, int(m.group(2))))
        return f"{m.group(1)}{'+' if n >= 0 else '-'}{abs(n):02d}"
    return _SNR_RE.sub(fix, text)


def frames_for(mycall, mygrid, text, speed="normal", selected_call=""):
    """Typed text -> [(frame, flags), ...]. Raises ValueError if JS8 can't send it."""
    text = " ".join(clamp_reports(text).upper().split())
    if not text:
        raise ValueError("nothing to send")
    frames = js8msg.build_frames(mycall, mygrid, text, selected_call=selected_call, speed=speed)
    if not frames:
        raise ValueError("nothing to send")
    return frames


def signal_seconds(speed):
    """How long one frame's signal lasts (79 tones)."""
    return 79 * js8enc.SPEEDS[speed]["nsps"] / FS


def frame_audio(frame, flags, freq, speed="normal"):
    """One frame's signal as float audio (-1..1), lowest tone at freq Hz."""
    return js8enc.synth(js8enc.tones(frame, flags, speed), float(freq), speed)


def plan(n, speed, now, lead=0.4):
    """Start time of each of n frames (same clock as `now`): back-to-back
    slots from the next slot whose start is still at least `lead` away."""
    sp = js8enc.SPEEDS[speed]
    per, start = sp["period"], sp["start"]
    t0 = (now // per) * per
    if now + lead > t0 + start:
        t0 += per
    return [t0 + k * per + start for k in range(n)]


def to_int16(x, level=LEVEL):
    return np.clip(np.round(x * level * 32767), -32767, 32767).astype("<i2")


# ------------------------------------------------------------------ sender
LEAD = 0.6                               # s before a frame's start to begin playing it
log = logging.getLogger("procyon.js8")


class Js8Tx:
    """Sends typed messages one after another, one frame per slot.

    play(make_wave, target): coroutine that keys the radio, plays
        make_wave(rate) starting at `target` (computer clock) and ALWAYS
        unkeys - the engine's _play. It returns early when `halt` is set.
    band_time(), offset(): the band's clock and how far the computer's
        clock is ahead of it (band sync), like the receiver's.
    halt: the engine's halt event (Halt button, Esc, band change, quit).
    report(obj): coroutine, tells the chat screen (txplan / txdone).
    on_air(info): coroutine, info = {"msg", "freq", "start", "end", ...}
        while a frame plays, None after (the header's ON AIR sign).
    """

    def __init__(self, play, band_time, offset, halt, report, on_air):
        self.play, self.band_time, self.offset, self.halt = play, band_time, offset, halt
        self.report, self.on_air = report, on_air
        self.queue = collections.deque()
        self.task = None
        self.busy_until = 0.0            # band time when the last planned frame ends

    @property
    def busy(self):
        return self.task is not None

    def submit(self, rid, frames, freq, speed, text=""):
        """Queue one message (the user pressed Send). Returns the sending task."""
        self.queue.append({"id": rid, "frames": frames, "freq": float(freq), "speed": speed, "text": text})
        if self.task is None:
            self.task = asyncio.create_task(self._run())
        return self.task

    def stop(self):
        """Drop what's waiting; the caller sets `halt`, which cuts the frame
        that's playing (and its PTT) at once."""
        dropped = list(self.queue)
        self.queue.clear()
        self.busy_until = 0.0
        for req in dropped:
            asyncio.ensure_future(self.report({"type": "txdone", "id": req["id"], "stopped": True}))

    async def _wait_until(self, t):
        """Wait until band time t; True if Halt came first."""
        while True:
            left = t - self.band_time()
            if self.halt.is_set():
                return True
            if left <= 0:
                return False
            try:
                await asyncio.wait_for(self.halt.wait(), min(left, 1.0))
                return True
            except asyncio.TimeoutError:
                pass

    async def _run(self):
        try:
            while self.queue:
                req = self.queue.popleft()
                await self._send(req)
        finally:
            self.task = None
            self.busy_until = 0.0
            await self.on_air(None)

    async def _send(self, req):
        rid, frames, freq, speed = req["id"], req["frames"], req["freq"], req["speed"]
        self.halt.clear()
        sec = signal_seconds(speed)
        starts = plan(len(frames), speed, max(self.band_time(), self.busy_until), lead=LEAD + 0.1)
        self.busy_until = starts[-1] + sec
        off = self.offset()
        await self.report({"type": "txplan", "id": rid, "n": len(frames),
                           "start": int((starts[0] + off) * 1000), "end": int((starts[-1] + sec + off) * 1000)})
        log.info("JS8 TX %d frame(s), %s, %d Hz: %s", len(frames), speed, freq, req["text"])
        try:
            for k, (t, (frame, flags)) in enumerate(zip(starts, frames)):
                if await self._wait_until(t - LEAD):
                    raise _Stopped
                target = t + self.offset()           # on the computer's clock
                await self.on_air({"msg": req["text"], "freq": round(freq), "start": target, "end": target + sec,
                                   "js8": True, "frame": k + 1, "frames": len(frames)})
                await self.play(lambda rate, fr=frame, fl=flags: js8enc.synth(
                    js8enc.tones(fr, fl, speed), freq, speed, fs=rate), target)
                await self.on_air(None)
                if self.halt.is_set():
                    raise _Stopped
        except _Stopped:
            log.info("JS8 TX stopped")
            self.queue.clear()
            await self.report({"type": "txdone", "id": rid, "stopped": True})
            return
        except Exception as e:  # noqa: BLE001 - e.g. the radio didn't confirm PTT
            log.exception("JS8 transmit")
            for later in list(self.queue):
                await self.report({"type": "txdone", "id": later["id"], "stopped": True})
            self.queue.clear()
            await self.report({"type": "txdone", "id": rid, "error": str(e)})
            return
        await self.report({"type": "txdone", "id": rid})


class _Stopped(Exception):
    pass


# ------------------------------------------------------------- test player
def test_device(device):
    return bool(device) and device.split(".")[0] in TEST_SINKS


async def play_test(device, frames, freq, speed, now=None):
    """Play the frames into the TEST null sink at their slot times (band
    clock = computer clock here). Returns the planned start times.
    Refuses anything but the test device. Linux/PulseAudio only."""
    if not test_device(device):
        raise ValueError(f"won't play into {device!r}: only the test device ({', '.join(TEST_SINKS)})")
    starts = plan(len(frames), speed, time.time() if now is None else now)
    lat = 0.05
    for t, (frame, flags) in zip(starts, frames):
        x = to_int16(frame_audio(frame, flags, freq, speed))
        await asyncio.sleep(max(0, t - 0.4 - time.time()))
        p = await asyncio.create_subprocess_exec(
            "pacat", "--playback", "--raw", "--format=s16le", f"--rate={FS}", "--channels=1",
            f"--latency-msec={int(lat * 1000)}", f"--device={device}",
            stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        lead = max(0.0, t - time.time() - lat)
        p.stdin.write(np.zeros(int(lead * FS), "<i2").tobytes() + x.tobytes())
        await p.stdin.drain()
        p.stdin.close()
        await p.wait()
    return starts

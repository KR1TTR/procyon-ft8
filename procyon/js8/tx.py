"""JS8 transmit AUDIO (ProcyonEX, for 1.1) - step 1 of JS8 TX.

Turns a typed message into JS8 frames (js8msg.build_frames, like JS8Call),
each frame into tones and audio (js8enc), and works out when each frame
goes out: one frame per slot, starting on time (Normal 0.5 s into each
15 s slot, Fast 0.2 s, Turbo 0.1 s, Slow 0.5 s).

SAFETY: this module only makes AUDIO and slot times. There is no radio
control in it - no PTT, no CAT, no rigctld. The little player at the end
refuses every sound device except the TEST one (the PulseAudio null sink
procyon_test): a radio on VOX would key from audio alone. Sending through
the radio comes later (step 2: Procyon's own audio + PTT path and its
safety guards; step 3: dummy load, with the user's go-ahead).
"""
import asyncio
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

"""JS8 receive inside Procyon - LISTEN ONLY (ProcyonEX, for 1.1).

Ported from the prototype's live helper (js8-chat/modem/js8live.py). Every
slot it cuts the radio audio Procyon already records, decodes it with
Procyon's own JS8 decoder, joins each station's frames into messages (like
JS8Call: frames close in frequency, until the "last frame" flag) and hands
them to the chat screen. It never plays audio and never keys the radio.

Messages to the page go out as {"type": "js8", "m": {...}} with the same
inner messages the helper sent (hello, part, msg, wf); Procyon's main page
passes them on to the chat screen in its frame.
"""
import asyncio
import collections
import logging
import time

import numpy as np

from . import js8dec, js8enc, js8msg

FS = 12000
SPEEDS = {"Slow": "slow", "Normal": "normal", "Fast": "fast", "Turbo": "turbo"}
DECODE_LATE = 0.5                        # s after an on-time signal ends (JS8Call's figure)
JOIN_HZ = 10                             # frames this close in frequency = one station
OPEN_SLOTS = 3                           # a message with no new frame for this many slots is done
LOW, HIGH = 300, 3300                    # the chat screen's waterfall range
WF_BINS = 300
WF_N = 4096
WF_FLOOR_DB = 3                          # waterfall: dark up to this many dB above the noise
WF_RANGE_DB = 15                         # ... white this many dB higher
QUIET_DB = -65                           # audio below this (dB of full scale) = nothing coming in
QUIET_SEC = 5                            # ... for this long before the screen says so
HISTORY = 300                            # messages kept for a page that opens (or reloads) later

# JS8Call's usual dial frequencies (its FrequencyList.cpp); none on 60 m.
JS8_DIALS = {"160m": 1_842_000, "80m": 3_578_000, "40m": 7_078_000, "30m": 10_130_000,
             "20m": 14_078_000, "17m": 18_104_000, "15m": 21_078_000, "12m": 24_922_000,
             "10m": 28_078_000, "6m": 50_318_000, "2m": 144_178_000}

log = logging.getLogger("procyon.js8")


class Assembler:
    """Joins one station's frames into a message, like JS8Call."""

    def __init__(self):
        self.open = []                   # {hz, frames, snr, last}

    def add(self, hz, frame, flags, snr, slot):
        """-> [('part'|'msg', message dict), ...]"""
        m = next((m for m in self.open if abs(m["hz"] - hz) <= JOIN_HZ), None)
        done = []
        if m is not None and flags & js8msg.FLAG_FIRST:
            self.open.remove(m)
            done.append(("msg", m))      # a new message starts: the old one is as complete as it gets
            m = None
        if m is None:
            m = {"hz": hz, "frames": [], "snr": snr, "last": slot}
            self.open.append(m)
        m["frames"].append((frame, flags))
        m.update(hz=hz, snr=snr, last=slot)
        if flags & js8msg.FLAG_LAST:
            self.open.remove(m)
            done.append(("msg", m))
        else:
            done.append(("part", m))
        return done

    def expire(self, slot):
        old = [m for m in self.open if slot - m["last"] >= OPEN_SLOTS]
        for m in old:
            self.open.remove(m)
        return [("msg", m) for m in old]


def _report_split(frames):
    """JS8Call's packer quirk (still in JS8Call 3.0): a directed frame's report
    holds only -30..+31 and JS8Call reads at most two characters of it, so a
    report like -60 goes out as "-06" plus one extra frame with just "0"
    (-34 -> "-03" + "4"). Returns the real report ("-60") when the message is
    exactly that: an SNR report of the form -0N/+0N whose next frame is the
    message's last one and holds a single digit. Otherwise None."""
    if len(frames) < 2:
        return None
    a = js8msg.decode_frame(*frames[-2]) or {}
    b = js8msg.decode_frame(*frames[-1]) or {}
    if not (frames[-1][1] & js8msg.FLAG_LAST) or b.get("type") != js8msg.FRAME_DATA:
        return None
    digit = b.get("text", "").strip()
    if len(digit) != 1 or not digit.isdigit():
        return None
    if a.get("type") == js8msg.FRAME_DIRECTED and js8msg.is_snr_command(a.get("cmd", "")):
        num = a.get("num", "")
    elif a.get("type") == js8msg.FRAME_COMPOUND_DIRECTED and " SNR " in a.get("extra", "") + " ":
        num = a["extra"].split()[-1]
    else:
        return None
    if len(num) != 3 or num[0] not in "+-" or num[1] != "0" or not num[2].isdigit():
        return None
    return num, num[0] + num[2] + digit


def message_text(frames):
    """The text JS8Call shows, but with a report split by JS8Call's packer
    joined back ("HEARTBEAT SNR -06 0" -> "HEARTBEAT SNR -60")."""
    text = js8msg.decode_message(frames)
    split = _report_split(frames)
    if split:
        shown, real = split
        head = js8msg.decode_message(frames[:-1]).rstrip()
        if head.endswith(shown):
            text = head[:-len(shown)] + real + " "
    return text


def as_message(kind, m):
    """The text the screen gets: sender call apart, the rest starting with
    the call or @group it's for (like the chat screen's station.js expects)."""
    text = message_text(m["frames"])
    first = js8msg.decode_frame(*m["frames"][0]) or {}
    frm = first.get("from", "")
    if frm and text.startswith(frm + ":"):
        text = text[len(frm) + 1:]
    elif first.get("type") == js8msg.FRAME_COMPOUND_DIRECTED or not (m["frames"][0][1] & js8msg.FLAG_FIRST):
        frm = ""                         # we missed the frame with the sender's call
    return {"type": kind, "from": frm, "text": " ".join(text.split()), "snr": m["snr"], "freq": round(m["hz"])}


class Js8Rx:
    """One JS8 listening session. The engine starts it when JS8 is picked and
    stops it when the user goes back to FT8/FT4.

    samples(t0, t1): radio audio between two computer-clock times (int16)
    latest(n): the newest n samples (or None)
    band_time(): the band's time (Procyon's clock with band sync applied)
    offset(): how far the computer's clock is ahead of the band's
    status(): (audio device name, audio error text)
    send(obj): coroutine that sends obj to every open page
    """

    def __init__(self, samples, latest, band_time, offset, status, send):
        self.samples, self.latest, self.band_time, self.offset = samples, latest, band_time, offset
        self.status, self.send = status, send
        self.speed = "Normal"
        self.asm = Assembler()
        self.history = collections.deque(maxlen=HISTORY)
        self.since = int(time.time() * 1000)   # when listening started (ms)
        self.quiet = False
        self.level = None
        self.tasks = []
        self.tx = None                   # what Send transmits through (set by the engine), None = listen only

    # ---------------------------------------------------------------- plumbing
    def start(self):
        self.tasks = [asyncio.create_task(self._decode_loop()), asyncio.create_task(self._wf_loop())]
        log.info("JS8 listening started (%s)", self.speed)

    async def stop(self):
        for t in self.tasks:
            t.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []
        log.info("JS8 listening stopped")

    async def _out(self, m):
        await self.send({"type": "js8", "m": m})

    def hello(self):
        device, error = self.status()
        return {"type": "hello", "listen_only": not self.tx, "tx": self.tx, "device": device or "the radio's audio",
                "speed": self.speed, "error": error or None, "quiet": self.quiet, "level": self.level,
                "since": self.since}

    def welcome(self):
        """For a chat screen that opens (or reloads) while listening: hello + what was heard."""
        return {"type": "js8", "m": self.hello(), "history": list(self.history)}

    async def set_speed(self, speed):
        if speed in SPEEDS and speed != self.speed:
            self.speed = speed
            self.asm = Assembler()
            log.info("JS8 speed %s", speed)
            await self._out(self.hello())

    def new_band(self):
        """Half-heard messages from the old band will never finish."""
        self.asm = Assembler()

    # ---------------------------------------------------------------- decoding
    async def _decode_loop(self):
        while True:
            try:
                await self._one_slot()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - keep listening whatever one slot does
                log.exception("JS8 decode")
                await asyncio.sleep(1)

    async def _one_slot(self):
        speed = SPEEDS[self.speed]
        sp = js8enc.SPEEDS[speed]
        per = sp["period"]
        now = self.band_time()
        t0 = (now // per) * per
        # decode like JS8Call: once an on-time signal's last tone + 0.5 s is in
        # (Normal 13.6 s into the 15 s slot), not at the end of the slot
        t1 = t0 + min(per, sp["start"] + 79 * sp["nsps"] / FS + DECODE_LATE)
        if now > t1:
            await asyncio.sleep(t0 + per - now)
            return                       # started too late for this slot: wait for the next one
        await asyncio.sleep(max(0, t1 + 0.1 - now))
        if SPEEDS[self.speed] != speed:
            return                       # speed changed during the slot
        off = self.offset()
        audio = self.samples(t0 + off, t1 + off)
        if audio is None or not np.any(audio):
            return
        found = await asyncio.to_thread(js8dec.decode, audio.astype(float), speed)
        if SPEEDS[self.speed] != speed:
            return                       # speed changed while decoding
        slot = int(t0 // per)
        out = []
        for d in sorted(found, key=lambda d: d["freq"]):
            out += self.asm.add(d["freq"], d["frame"], d["flags"], d["snr"], slot)
        out += self.asm.expire(slot)
        for kind, m in out:
            msg = as_message(kind, m)
            msg["t"] = int(time.time() * 1000)
            if kind == "msg":
                self.history.append(msg)
            if msg["text"] or kind == "msg":
                await self._out(msg)
                if kind == "msg":
                    log.info("JS8 %s %+d dB %4d Hz  %s: %s", time.strftime("%H:%M:%S", time.gmtime(t0)),
                             msg["snr"], msg["freq"], msg["from"] or "?", msg["text"])

    # ------------------------------------------------- waterfall + input level
    async def _wf_loop(self):
        hz = np.fft.rfftfreq(WF_N, 1 / FS)
        idx = np.searchsorted(hz, np.linspace(LOW, HIGH, WF_BINS + 1))
        win = np.hanning(WF_N)
        low_since, n = None, 0
        while True:
            await asyncio.sleep(0.25)
            x = self.latest(WF_N)
            if x is None:
                continue
            x = x.astype(float)
            p = np.abs(np.fft.rfft(x * win)) ** 2
            bins = np.array([p[idx[i]:max(idx[i + 1], idx[i] + 1)].mean() for i in range(WF_BINS)])
            db = 10 * np.log10(bins + 1e-9)
            v = np.clip((db - np.median(db) - WF_FLOOR_DB) / WF_RANGE_DB, 0, 1)
            await self._out({"type": "wf", "v": [round(float(a), 2) for a in v]})
            n += 1
            if n % 4:
                continue
            # once a second: say so when (almost) no audio comes in - a radio
            # switched off or its volume down
            lvl = 20 * np.log10(max(np.sqrt(np.mean(x * x)), 1e-3) / 32768)
            self.level = round(lvl)
            low = lvl < (QUIET_DB + (5 if self.quiet else 0))      # a little hysteresis
            low_since = (low_since or time.time()) if low else None
            quiet = low_since is not None and time.time() - low_since >= QUIET_SEC
            if quiet != self.quiet:
                self.quiet = quiet
                log.info("JS8: audio %s (%.0f dB)", "almost silent - radio off or volume down?" if quiet else "back", lvl)
                await self._out(self.hello())

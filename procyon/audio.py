"""Sound card input and output.

Two interchangeable backends:
  - PulseAudio / PipeWire (parec/pacat): the default on Linux desktops.
  - PortAudio (the `sounddevice` package): Windows, macOS, and Linux
    systems without PulseAudio.
Procyon works at 12 kHz internally (what the FT8 decoder wants); the
PortAudio backend converts to and from the sound card's own rate."""

import asyncio
import logging
import os
import re
import shutil
import subprocess
import threading
import time

import numpy as np

from .system import IS_WINDOWS

log = logging.getLogger("procyon.audio")
FS = 12000


def _align(pcm: np.ndarray, lead: float, rate: int) -> np.ndarray:
    """Line the audio up with its start time. Early: pad with silence.
    Late: skip the part that should already have played (as WSJT-X does),
    so the signal stays in step with the cycle and ends on time."""
    n = int(abs(lead) * rate)
    if lead >= 0:
        return np.concatenate([np.zeros(n, pcm.dtype), pcm])
    return pcm[n:]


def match_device(name: str, names) -> str | None:
    """The device to use for a saved name: "" (system default) stays "", an
    exact match wins, and PipeWire's numbered copy of a name ("....9") counts
    as the same device. None = not connected - the sound server would quietly
    use some other device instead, so callers must not go ahead."""
    if not name:
        return ""
    names = list(names)
    if name in names:
        return name
    base = re.sub(r"\.\d+$", "", name)
    if base != name and base in names:
        return base
    return None


def choose_backend():
    want = os.environ.get("PROCYON_AUDIO", "").lower()
    if want != "portaudio" and not IS_WINDOWS and shutil.which("parec") and shutil.which("pacat"):
        return PulseAudio()
    return PortAudio()


# ------------------------------------------------------------ PulseAudio

class PulseAudio:
    name = "pulse"

    def _list(self, kind):
        try:
            out = subprocess.run(["pactl", "list", kind], capture_output=True, text=True, timeout=5).stdout
        except Exception:  # noqa: BLE001
            return []
        devs, name = [], None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Name:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("Description:") and name:
                devs.append({"name": name, "desc": line.split(":", 1)[1].strip()})
                name = None
        if kind == "sources":
            devs.sort(key=lambda d: d["name"].endswith(".monitor"))
        return devs

    def inputs(self):
        return self._list("sources")

    def outputs(self):
        return self._list("sinks")

    def resolve(self, name, kind):
        devs = self.inputs() if kind == "in" else self.outputs()
        return match_device(name, (d["name"] for d in devs))

    def guess(self, kind):
        """The first USB codec - usually the radio interface."""
        for d in (self.inputs() if kind == "in" else self.outputs()):
            n = d["name"].lower()
            if "usb" in n and ("codec" in n or "audio_device" in n) and not n.endswith(".monitor"):
                return d["name"]
        return ""

    async def capture(self, device, on_audio):
        args = ["parec", "--raw", "--format=s16le", f"--rate={FS}", "--channels=1", "--latency-msec=40"]
        if device:
            args += ["-d", device]
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            while True:
                data = await proc.stdout.read(2400)
                if not data:
                    err = (await proc.stderr.read()).decode().strip()
                    raise RuntimeError(err or "audio input stopped")
                if len(data) % 2:
                    data += await proc.stdout.readexactly(1)
                await on_audio(np.frombuffer(data, np.int16), time.time())
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    async def play(self, device, make_wave, target, halt: asyncio.Event, gain):
        """Play make_wave(rate) (float, -1..1) starting at time `target`, scaled
        by gain() - read again for every 0.1 s block, so changing the audio drive
        during a transmission takes effect within about a third of a second.
        Returns early if `halt` is set; always stops the audio on the way out."""
        rate = 48000
        args = ["pacat", "--playback", "--raw", "--format=s16le", f"--rate={rate}",
                "--channels=1", "--latency-msec=60"]
        if device:
            args += ["-d", device]
        proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            t0 = time.time() + 0.06                        # when the first sample plays
            wave = _align(make_wave(rate), target - t0, rate)
            block = rate // 10
            for i in range(0, len(wave), block):
                if halt.is_set():
                    return
                # stay only ~0.3 s ahead of what's playing, so gain changes show up quickly
                ahead = t0 + i / rate - 0.3 - time.time()
                if ahead > 0:
                    await asyncio.sleep(ahead)
                pcm = np.clip(wave[i:i + block] * gain(), -32768, 32767).astype(np.int16)
                proc.stdin.write(pcm.tobytes())
                await proc.stdin.drain()
            proc.stdin.close()
            done = asyncio.create_task(proc.wait())
            stop = asyncio.create_task(halt.wait())
            await asyncio.wait({done, stop}, return_when=asyncio.FIRST_COMPLETED)
            stop.cancel()
            if proc.returncode not in (None, 0):
                err = (await proc.stderr.read()).decode().strip()
                raise RuntimeError(err or "audio output failed")
        finally:
            # Never leave a player running into the radio (Halt, Tune off, errors).
            if proc.returncode is None:
                proc.kill()
                await proc.wait()


# ------------------------------------------------------------- PortAudio

class Resampler:
    """Streaming sample-rate converter: low-pass filter, then interpolate.
    Plenty for FT8, whose signals all sit below 3 kHz."""

    def __init__(self, fin: int, fout: int = FS, cutoff: float = 5400.0, taps: int = 127):
        self.step = fin / fout
        n = np.arange(taps) - (taps - 1) / 2
        h = np.sinc(2 * cutoff / fin * n) * np.hamming(taps)
        self.h = (h / h.sum()).astype(np.float64)
        self.tail = np.zeros(taps - 1)
        self.last = 0.0      # last filtered sample, for interpolating across blocks
        self.pos = 0.0       # where the next output sample falls, in this block's samples

    def process(self, x: np.ndarray) -> np.ndarray:
        buf = np.concatenate([self.tail, x.astype(np.float64)])
        self.tail = buf[-(len(self.h) - 1):]
        y = np.convolve(buf, self.h, mode="valid")          # len(y) == len(x)
        ybuf = np.concatenate([[self.last], y])             # index p in y -> p + 1 here
        n_out = int(np.floor((len(y) - 1 - self.pos) / self.step)) + 1 if self.pos <= len(y) - 1 else 0
        idx = self.pos + self.step * np.arange(max(0, n_out))
        out = np.interp(idx + 1, np.arange(len(ybuf)), ybuf)
        self.pos = (idx[-1] + self.step if n_out > 0 else self.pos) - len(y)
        self.last = y[-1] if len(y) else self.last
        return out


class PortAudio:
    name = "portaudio"

    def __init__(self):
        import sounddevice as sd  # noqa: PLC0415 - only needed for this backend
        self.sd = sd

    def _hostapi(self):
        """WASAPI on Windows (full device names, modern); otherwise the default."""
        apis = self.sd.query_hostapis()
        if IS_WINDOWS:
            for i, a in enumerate(apis):
                if "WASAPI" in a["name"]:
                    return i
        return self.sd.default.hostapi

    def _devices(self, kind):
        api = self._hostapi()
        return [(i, d) for i, d in enumerate(self.sd.query_devices())
                if d["hostapi"] == api and d[f"max_{kind}_channels"] > 0]

    def inputs(self):
        return [{"name": d["name"], "desc": d["name"]} for _, d in self._devices("input")]

    def outputs(self):
        return [{"name": d["name"], "desc": d["name"]} for _, d in self._devices("output")]

    def guess(self, kind):
        for _, d in self._devices("input" if kind == "in" else "output"):
            n = d["name"].lower()
            if "usb" in n and any(w in n for w in ("codec", "audio device", "pnp sound")):
                return d["name"]
        return ""

    def _extra(self, dev, kind):
        """On WASAPI, let Windows convert channel count and sample rate if needed."""
        info = self.sd.query_devices(dev, kind)
        if "WASAPI" in self.sd.query_hostapis(info["hostapi"])["name"]:
            try:
                return self.sd.WasapiSettings(auto_convert=True)
            except TypeError:   # older sounddevice
                return None
        return None

    def resolve(self, name, kind):
        return match_device(name, (d["name"] for _, d in self._devices(kind)))

    def _find(self, name, kind):
        for i, d in self._devices(kind):
            if d["name"] == name:
                return i
        return None   # default device

    async def capture(self, device, on_audio):
        dev = self._find(device, "input")
        info = self.sd.query_devices(dev, "input")
        rate = int(info["default_samplerate"])
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def callback(indata, frames, t, status):  # PortAudio's thread
            loop.call_soon_threadsafe(queue.put_nowait, indata[:, 0].copy())

        rs = Resampler(rate)
        stream = self.sd.InputStream(device=dev, channels=1, samplerate=rate, dtype="float32",
                                     blocksize=rate // 20, callback=callback,
                                     extra_settings=self._extra(dev, "input"))
        log.info("audio input: %s at %d Hz", info["name"], rate)
        with stream:
            while True:
                try:
                    block = await asyncio.wait_for(queue.get(), 5)
                except asyncio.TimeoutError:
                    raise RuntimeError(f"no audio arriving from {info['name']}") from None
                out = rs.process(block)
                await on_audio(np.clip(out * 32768, -32768, 32767).astype(np.int16), time.time())

    async def play(self, device, make_wave, target, halt: asyncio.Event, gain):
        """As PulseAudio.play: gain() is read for every 0.1 s block."""
        dev = self._find(device, "output")
        info = self.sd.query_devices(dev, "output")
        rate = int(info["default_samplerate"])
        channels = min(2, int(info["max_output_channels"]))
        wave = make_wave(rate).astype(np.float32)
        stop = threading.Event()
        errors = []

        def worker():   # blocking writes in a thread; checks `stop` every 0.1 s
            try:
                with self.sd.OutputStream(device=dev, channels=channels, samplerate=rate, dtype="float32",
                                          extra_settings=self._extra(dev, "output")) as out:
                    data = _align(wave, target - time.time() - out.latency, rate)
                    block = rate // 10
                    for i in range(0, len(data), block):
                        if stop.is_set():
                            out.abort()
                            return
                        chunk = np.clip(data[i:i + block] * (gain() / 32768), -1, 1)
                        out.write(np.repeat(chunk[:, None], channels, axis=1))
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(None, worker)
        halted = asyncio.create_task(halt.wait())
        try:
            await asyncio.wait({fut, halted}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # Never leave audio playing into the radio (Halt, Tune off, errors).
            stop.set()
            halted.cancel()
            await asyncio.shield(fut)
        if errors:
            raise RuntimeError(f"audio output failed: {errors[0]}")

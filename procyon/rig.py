"""Radio control through Hamlib's rigctld (network protocol)."""

import asyncio
import logging
import re
import time

from .system import NO_WINDOW, find_tool

log = logging.getLogger("procyon.rig")

# rigctld repeats the same complaints every few seconds while the radio is off.
# Each message is written to the log once per this many seconds; the rest are
# counted and summed up, so they don't push the useful history out of the log.
REPEAT_LOG_SEC = 300


class Rig:
    def __init__(self):
        self.host = "127.0.0.1"
        self.port = 4532
        self.enabled = False
        self.connected = False      # rigctld is reachable
        self.answering = False      # ...and the radio itself answers it (False while it's off)
        self.freq = None
        self.error = ""
        self._reader = None
        self._writer = None
        self._lock = asyncio.Lock()
        self._daemon = None
        self._daemon_log: list[str] = []     # rigctld's last few messages, for explaining failures
        self._logged_at: dict[str, float] = {}   # message -> when it last went into the log
        self._hidden = 0                     # repeats not written since the last summary
        self._hidden_since = 0.0

    @property
    def up(self):
        """Talking to the radio: rigctld reachable and the radio answering."""
        return self.connected and self.answering

    async def configure(self, s: dict):
        await self.close()
        self.enabled = bool(s.get("rig_enabled"))
        self.host = s.get("rig_host") or "127.0.0.1"
        self.port = int(s.get("rig_port") or 4532)
        model = str(s.get("rig_model") or "").strip()
        if self.enabled and model and s.get("rig_spawn"):
            exe = find_tool("rigctld", "rigctld-wsjtx")
            if not exe:
                self.error = "rigctld not found"
                return
            args = [exe, "-m", model, "-t", str(self.port), "-vv"]   # -vv: say why a radio won't connect
            if model == "1":
                args += ["-P", "RIG"]   # Hamlib's simulated radio has no PTT unless asked
            if s.get("rig_device"):
                args += ["-r", s["rig_device"]]
            if s.get("rig_baud"):
                args += ["-s", str(s["rig_baud"])]
            if s.get("ptt") in ("rts", "dtr") and s.get("rig_device"):
                args += ["-P", s["ptt"].upper(), "-p", s["rig_device"]]
            log.info("starting %s", " ".join(args))
            self._daemon_log = []
            self._daemon = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE, **NO_WINDOW)
            asyncio.get_running_loop().create_task(self._drain(self._daemon))
            await asyncio.sleep(1.0)
            if self._daemon.returncode is not None:     # rigctld gave up straight away
                self.error = self._why() or "rigctld stopped - check the radio model, port and baud"

    async def _drain(self, proc):
        """Keep rigctld's messages: they say why a radio won't connect."""
        while proc.stderr and (line := await proc.stderr.readline()):
            text = line.decode(errors="replace").strip()
            if text:
                self._log_daemon(text)
                self._daemon_log = (self._daemon_log + [text])[-5:]

    def _log_daemon(self, text: str):
        now = time.monotonic()
        if now - self._logged_at.get(text, -REPEAT_LOG_SEC) < REPEAT_LOG_SEC:
            if not self._hidden:
                self._hidden_since = now
            self._hidden += 1
        else:
            self._logged_at[text] = now
            log.info("rigctld: %s", text)
        if self._hidden and now - self._hidden_since >= REPEAT_LOG_SEC:
            log.info("rigctld: (%d repeats of the messages above in the last %d minutes not shown)",
                     self._hidden, round((now - self._hidden_since) / 60))
            self._hidden = 0
        if len(self._logged_at) > 200:   # forget old messages
            self._logged_at = {k: t for k, t in self._logged_at.items() if now - t < REPEAT_LOG_SEC}

    def _why(self) -> str:
        """rigctld's latest complaint in plain words ('' if it hasn't said anything)."""
        if not self._daemon_log:
            return ""
        last = " ".join(self._daemon_log)
        if re.search(r"No such file|not configured", last, re.I):
            return "Serial port not found - is the radio plugged in and switched on?"
        if re.search(r"busy|Permission denied", last, re.I):
            return "The serial port is in use by another program (close WSJT-X or other radio software)."
        if re.search(r"power is off|get_powerstat while off|validation failed|time ?out|IO error|protocol error", last, re.I):
            return ("The radio isn't answering - check its model, the serial port and the baud rate "
                    "(they must match the radio's CAT settings).")
        return self._daemon_log[-1]

    async def close(self):
        self.connected = self.answering = False
        if self._writer:
            self._writer.close()
        self._reader = self._writer = None
        if self._daemon and self._daemon.returncode is None:
            self._daemon.terminate()
            await self._daemon.wait()
        self._daemon = None

    async def _cmd(self, cmd: str) -> str:
        async with self._lock:
            if not self.connected:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port), 2)
                self.connected = True
                self.error = ""
            self._writer.write((cmd + "\n").encode())
            await self._writer.drain()
            line = await asyncio.wait_for(self._reader.readline(), 2)
            if not line:
                raise ConnectionError("rigctld closed the connection")
            return line.decode().strip()

    async def cmd(self, cmd: str):
        if not self.enabled:
            return None
        try:
            return await self._cmd(cmd)
        except Exception as e:  # noqa: BLE001 - any failure means "not connected"
            self.connected = self.answering = False
            self.error = self._why() or str(e) or type(e).__name__
            if self._writer:
                self._writer.close()
            return None

    async def poll(self):
        r = await self.cmd("f")
        if r and r.isdigit():
            self.freq = int(r)
            self.answering = True
            if self.error.startswith("The radio isn't answering"):
                self.error = ""
        elif r and r.startswith("RPRT -"):   # rigctld is up but the radio isn't answering it
            self.answering = False
            self.error = ("The radio isn't answering - is it switched on and plugged in? Procyon picks "
                          "it up again by itself. (If it never answers, check the model, serial port and baud.)")

    async def set_freq(self, hz: int):
        await self.cmd(f"F {int(hz)}")
        await self.poll()

    async def set_mode(self, mode: str) -> bool:
        """Set the operating mode (e.g. PKTUSB = data, USB). A passband of -1
        leaves the radio's filter width as the operator set it."""
        r = await self.cmd(f"M {mode} -1")
        return r == "RPRT 0"

    async def get_mode(self):
        r = await self.cmd("m")
        if r and self._reader:
            try:   # rigctld answers with the mode, then the passband on a 2nd line
                await asyncio.wait_for(self._reader.readline(), 1)
            except Exception:  # noqa: BLE001
                pass
        return r

    async def ptt(self, on: bool) -> bool:
        """Key or unkey the radio. True only if the radio confirmed it."""
        return await self.cmd(f"T {1 if on else 0}") == "RPRT 0"

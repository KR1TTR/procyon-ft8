"""Procyon engine: audio capture, T/R timing, waterfall, decoding (WSJT-X jt9),
transmit, rig control and QSO automation."""

import asyncio
import collections
import datetime as dt
import json
import logging
import os
import re
import shutil
import tempfile
import time
import wave

import numpy as np

from . import ft4, ft8
from . import AUTHORS, RELEASE_DATE, __version__
from .audio import choose_backend
from .auth import PIN_RE, Auth, lan_addresses
from .demo import DemoBand
from .geo import (BANDS, DEMO_MARK, DEMO_MARKS, Cty, Logbook, band_of, distance_bearing, grid_to_latlon,
                  strip_demo_mark, utc_now)
from .qso import Qso
from . import logimport, newversion, pota, uploads, wsjtx_install
from .radio_detect import blocked_ports, detect_radios, test_rig
from .setup_help import hamlib_rigs, import_wsjtx
from .system import NO_WINDOW, find_tool, serial_ports, tools
from .udp import WsjtxUdp
from .rig import Rig

log = logging.getLogger("procyon")

FS = 12000
# The modes: each module has the timing (PERIOD, TX_START, DECODE_AT...) and
# its own encode() and synth(). Messages are packed the same way in both.
MODES = {"FT8": ft8, "FT4": ft4}
RING_SEC = 45
FFT_N = 4096
WF_HOP = 1500             # 8 waterfall rows per second
WF_BINS = int(4000 / (FS / FFT_N))   # 0 - 4000 Hz; the UI shows the range the user picks

DEFAULTS = {
    "mycall": "", "mygrid": "", "audio_in": "", "audio_out": "",
    "tx_level": 35, "tx_watts": "", "depth": 2, "demo": False, "ptt": "cat",
    "rig_enabled": False, "rig_host": "127.0.0.1", "rig_port": 4532,
    "rig_spawn": False, "rig_model": "", "rig_device": "", "rig_baud": "",
    "mode": "FT8",                   # FT8 or FT4 (the switch next to the band)
    "rig_mode": "data",              # what to put the radio in for FT8: data, usb, or none
    "wf_start": 0, "wf_end": 3000,   # waterfall range in Hz, to match the radio's filter
    "log_reports_in_comment": False,  # add "FT8 Sent: -05 Rcvd: -12" to log comments
    "team_badges": True,             # raccoon next to Procyon's developer and testers
    "units": "auto",                 # distances: "auto" (from your callsign), "km" or "mi"
    "export_new_only": False,        # Export ADIF gives only QSOs not exported before
    "auto_quit": True,               # stop Procyon a minute after its last browser tab closes
    "update_check": True,            # ask GitHub once a day whether a newer Procyon is out
    "clock_sync": "computer",        # Procyon's timing: "computer" clock as is, "auto" band sync, "manual" band sync
    "dial_hz": 14_074_000, "tx_freq": 1500, "rx_freq": 1500,
    "remote_enabled": False, "remote_pin_hash": "",
    # WSJT-X compatible UDP for GridTracker, JTAlert, loggers (WSJT-X defaults)
    "udp_enabled": True, "udp_host": "127.0.0.1", "udp_port": 2237, "udp_accept": True,
    # online logbooks, uploaded on a click (Settings → Logbook)
    "qrz_key": "", "lotw_location": "",
    # POTA activation (QSO panel → POTA): parks, where from, when
    "pota_active": False, "pota_parks": [], "pota_grid": "", "pota_state": "",
    "pota_start": "", "pota_end": "",
}
SECRETS = ("remote_pin_hash", "qrz_key")   # never sent to the browser
LOCAL_ONLY = ("remote_", "qrz_key", "lotw_")  # only the computer running Procyon changes these
OWN_COMMANDS = ("pota_", "mode")   # changed by their own commands, not the Settings form

# Band clock check: each decode's DT says how early/late that station arrived
# by our clock. One station's clock may be off, but the median over many is a
# good stand-in for "the band's time" - so it shows how far off ours is,
# without internet or GPS.
CLOCK_WINDOW_SEC = 120     # stations heard in the last two minutes
CLOCK_MIN_STATIONS = 8     # fewer than this: not enough to judge
CLOCK_MAX_DT = 2.0         # ignore wild values (a station whose clock is way off)
CLOCK_MAX_OFFSET = 2.5     # the decoder can't see further than this anyway
CLOCK_SYNC_MODES = ("computer", "auto", "manual")

LINE_RE = re.compile(r"^\d{4,6}\s+(-?\d+)\s+(-?\d+\.\d)\s+(\d+)\s+[~+]\s+(.+?)\s*$")   # ~ FT8, + FT4

# Long/unusual callsigns (3E40CDW, P4/WE9V) are sent as short codes, shown as
# <...> unless the decoder has heard the full call. jt9 forgets between runs, so
# each run first decodes a "reminder" recording of the ones heard so far.
LONG_CALLS_MAX = 40
PRIMER_TIME = "000003"    # the reminder's time: never a cycle start (FT8 or FT4), so easy to drop


class Engine:
    def __init__(self, data_dir: str, force_demo=False):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.settings_path = os.path.join(data_dir, "settings.json")
        self.s = dict(DEFAULTS)
        if os.path.exists(self.settings_path):
            saved = json.load(open(self.settings_path))
            self.s.update(saved)
            if "miles" in saved:   # 0.9.20 had an on/off switch; "on" was a deliberate choice
                if "units" not in saved:
                    self.s["units"] = "mi" if saved["miles"] else "auto"
                self.s.pop("miles")
        # The demo band never carries over: every launch starts live, unless
        # started with --demo (e.g. the Windows "DEMO ONLY" launcher).
        self.s["demo"] = bool(force_demo)
        if self.s["mode"] not in MODES:
            self.s["mode"] = "FT8"
        self.auth = Auth(data_dir)
        self.port = 8073
        self.on_remote_change = None   # set by the server: start/stop listening on the network
        self.on_quit = None            # set by the server: stop Procyon (the Quit button)
        self.udp = WsjtxUdp(on_reply=self._udp_reply, on_halt=self._udp_halt)
        self.cty = Cty()
        self._migrate_demo_qsos()
        self.logbook = self._open_logbook()
        self.grids_path = os.path.join(data_dir, "grids.json")
        self._load_grids()
        self.rig = Rig()
        self._installing = False
        self.uploading = set()          # services with an upload in progress
        self.importing = False          # a logbook import is running
        self.tqsl = None   # LoTW off (0.9.60): was uploads.find_tqsl()
        self.qso = Qso(self._log_qso, self.toast)
        self._set_station()
        self.demo = DemoBand(self.m)
        self.clients = set()
        self._ever_connected = False   # a tab has been open at least once
        self._alone_task = None
        self.history = collections.deque(maxlen=80)   # recent decode batches
        self.tmp = tempfile.mkdtemp(prefix="procyon-")
        self.long_calls = collections.OrderedDict()   # heard in full, newest last
        self.clock_dts = {}         # band clock check: sender -> (time heard, DT by the computer's clock)
        # Band sync: how far the computer's clock is ahead of the band's.
        # Procyon's own timing (cycles, decoding, transmit) runs on
        # computer time minus this; the computer's clock itself is never changed.
        self.clock_offset = 0.0
        self._clock_logged = None
        self._primer = ((), "", "")                     # (calls, mode, reminder .wav)
        # audio ring buffer
        self.ring = np.zeros(RING_SEC * FS, np.int16)
        self.total = 0
        self.t0 = None
        self.since_wf = 0
        self.noise_floor = None
        self.level_db = -99.0
        self._level_acc = []
        self.audio = choose_backend()
        self.rx_task = None
        self.audio_error = ""
        # timing
        self.last_decoded = 0
        self.decoding = False
        self.tx_periods = set()
        self.tx_task = None
        self.tx_info = None
        self.newer = None             # {"version", "url"} when a newer release is out
        self.tune_task = None
        self._halt = asyncio.Event()
        self._warned = ""

    # ------------------------------------------------------------ plumbing

    async def start(self):
        self._guess_devices()
        await self.rig.configure(self.s)
        await self.restart_audio()
        await self._configure_udp()
        asyncio.create_task(self._scheduler())
        asyncio.create_task(self._watch_releases())

    def _guess_devices(self):
        """Default to the first USB codec - usually the radio interface."""
        for key, kind in (("audio_in", "in"), ("audio_out", "out")):
            if not self.s[key]:
                try:
                    self.s[key] = self.audio.guess(kind)
                except Exception:  # noqa: BLE001 - no sound system: leave it to Settings
                    pass

    def _open_logbook(self):
        """Demo mode keeps its own logbook so simulated QSOs never mix with real ones."""
        name = "procyon_demo_log.adi" if self.s["demo"] else "procyon_log.adi"
        return Logbook(os.path.join(self.data_dir, name))

    def _migrate_demo_qsos(self):
        """Move demo QSOs logged by older versions out of the real logbook."""
        real = Logbook(os.path.join(self.data_dir, "procyon_log.adi"))
        moved = real.take(lambda q: q.get("comment", "").startswith(DEMO_MARKS))
        if moved:
            demo = Logbook(os.path.join(self.data_dir, "procyon_demo_log.adi"))
            for q in moved:
                demo.add(q)
            log.info("moved %d demo QSO(s) to the demo logbook", len(moved))

    def save(self):
        with open(self.settings_path, "w") as f:
            json.dump(self.s, f, indent=2)

    async def broadcast(self, obj):
        msg = json.dumps(obj)
        for ws in list(self.clients):
            try:
                await ws.send_str(msg)
            except Exception:  # noqa: BLE001
                self.clients.discard(ws)

    async def broadcast_bin(self, data: bytes):
        for ws in list(self.clients):
            try:
                await ws.send_bytes(data)
            except Exception:  # noqa: BLE001
                self.clients.discard(ws)

    def toast(self, text, kind="info"):
        asyncio.get_event_loop().create_task(
            self.broadcast({"type": "toast", "text": text, "kind": kind}))

    @property
    def m(self):
        """The mode in use: the ft8 or ft4 module."""
        return MODES[self.s["mode"]]

    @property
    def dial(self):
        if self.rig.enabled and self.rig.connected and self.rig.freq:
            return self.rig.freq
        return int(self.s["dial_hz"])

    def state(self):
        dx = self.qso.dxcall
        if dx and not self.qso.dxgrid and self.grids.get(dx):
            self.qso.dxgrid = self.grids[dx]
        info = self._station_info(dx, self.qso.dxgrid) if dx else {}
        return {
            "type": "state",
            "settings": {k: v for k, v in self.s.items() if k not in SECRETS},
            "home": self._home(),
            "export": {"new_only": self.s["export_new_only"], "pending": len(self._not_exported())},
            "qrz_key_set": bool(self.s["qrz_key"]), "tqsl_found": bool(self.tqsl),
            "uploads": self._upload_status(),
            "pota": self._pota_status(),
            "remote": {"enabled": self.s["remote_enabled"], "pin_set": bool(self.s["remote_pin_hash"]),
                       "urls": [f"http://{a}:{self.port}/" for a in lan_addresses()]},
            "audio": {"running": self.rx_task is not None and not self.audio_error,
                      "error": self.audio_error, "level": round(self.level_db, 1)},
            "rig": {"enabled": self.rig.enabled, "connected": self.rig.connected, "answering": self.rig.answering,
                    "freq": self.rig.freq, "error": self.rig.error},
            "dial": self.dial, "band": band_of(self.dial),
            "bands": [{"name": b[0], "hz": self._band_dial(b)} for b in BANDS],
            "timing": {"mode": self.m.NAME, "period": self.m.PERIOD, "tx_start": self.m.TX_START,
                       "signal": self.m.SIGNAL_SEC, "decode_at": self.m.DECODE_AT, "width": self.m.WIDTH},
            "qso": {**self.qso.snapshot(), "info": info},
            "tx": self.tx_info, "tuning": self.tune_task is not None,
            "log": [{**e, **self._station_info(e["call"], e["grid"])}
                    for e in self.logbook.recent(50)],
            "qso_count": len(self.logbook.qsos),
            "tools": {k: bool(v) for k, v in tools().items()},
            "can_install_wsjtx": wsjtx_install.available(),
            "about": {"version": __version__, "date": RELEASE_DATE, "authors": AUTHORS},
            "update": self._updated_to(),
            "newer": self.newer if self.s["update_check"] else None,
            "clock": self._clock_status(),
        }

    async def _watch_releases(self):
        """Once a day (and soon after start), ask whether a newer Procyon is out.
        Switching the setting on checks within a minute."""
        await asyncio.sleep(20)       # let start-up settle first
        last = 0.0
        while True:
            if self.s["update_check"] and time.time() - last >= newversion.CHECK_EVERY_SEC:
                last = time.time()
                rel = await newversion.latest()
                if rel:
                    newer = rel if newversion.is_newer(rel["version"]) else None
                    if newer and newer != self.newer:
                        log.info("new version check: Procyon %s is available (running %s)",
                                 newer["version"], __version__)
                    if newer != self.newer:
                        self.newer = newer
                        await self.push_state()
                else:
                    last -= newversion.CHECK_EVERY_SEC - 3600   # no answer: try again in an hour
            await asyncio.sleep(60)

    _VERSION_FILE = os.path.join(os.path.dirname(__file__), "__init__.py")
    _VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.M)

    def _updated_to(self):
        """The version now installed on disk, when it isn't the one running (an
        update was installed while Procyon was open) - else "". Read again only
        when the file changes."""
        try:
            mtime = os.stat(self._VERSION_FILE).st_mtime
            if mtime != getattr(self, "_disk_mtime", None):
                with open(self._VERSION_FILE, encoding="utf-8") as f:
                    m = self._VERSION_RE.search(f.read())
                self._disk_mtime, self._disk_version = mtime, m.group(1) if m else __version__
        except OSError:
            return ""
        return self._disk_version if self._disk_version != __version__ else ""

    async def push_state(self):
        await self.broadcast(self.state())
        self._udp_status()

    # ------------------------------------------- companion apps (WSJT-X UDP)

    async def _configure_udp(self):
        await self.udp.stop()
        self.udp.accept = bool(self.s["udp_accept"])
        # Paused in demo mode, so simulated stations never reach GridTracker etc.
        if self.s["udp_enabled"] and not self.s["demo"]:
            try:
                await self.udp.start(self.s["udp_host"] or "127.0.0.1", int(self.s["udp_port"] or 2237))
            except (OSError, ValueError) as e:
                self.toast(f"Couldn't start the GridTracker link: {e}", "warn")

    def _udp_status(self):
        q = self.qso
        self.udp.status({
            "dial": self.dial, "dx_call": q.dxcall,
            "report": ft8.fmt_report(q.snr) if q.dxcall else "", "mode": self.m.NAME,
            "period": int(self.m.PERIOD),
            "tx_enabled": q.tx_enabled, "transmitting": bool(self.tx_info),
            "decoding": self.decoding, "rx_df": int(self.s["rx_freq"]), "tx_df": int(self.s["tx_freq"]),
            "de_call": self.s["mycall"].upper(), "de_grid": self.s["mygrid"][:4].upper(),
            "dx_grid": q.dxgrid, "tx_message": q.msgs[q.next] if q.msgs else "",
        })

    def _udp_reply(self, r: dict):
        """A companion app asked to answer a decode (e.g. a click in GridTracker)."""
        for batch in reversed(self.history):
            for d in batch["items"]:
                if d["msg"] == r["msg"] and abs(d["freq"] - r["freq"]) <= 10:
                    if not self.s["mycall"]:
                        return
                    self.qso.reply(d)
                    self.s["rx_freq"] = d["freq"]
                    self.toast(f"Answering {d['p']['from'] or d['msg']} (requested by a companion app)", "info")
                    asyncio.get_event_loop().create_task(self.push_state())
                    return

    def _udp_halt(self):
        asyncio.get_event_loop().create_task(self.halt())
        asyncio.get_event_loop().create_task(self.push_state())

    # ---------------------------------------------------------------- audio

    async def restart_audio(self):
        if self.rx_task:
            self.rx_task.cancel()
            self.rx_task = None
        self.total, self.t0, self.audio_error = 0, None, ""
        self.demo.mycall = self.s["mycall"].upper()
        if self.s["demo"]:
            self.rx_task = asyncio.create_task(self._demo_reader())
        else:
            self.rx_task = asyncio.create_task(self._capture())

    async def _capture(self):
        try:
            await self.audio.capture(self.s["audio_in"], self._on_audio)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self.audio_error = str(e)
            log.error("audio input: %s", e)
            await self.push_state()

    async def _demo_reader(self):
        n = int(time.time() * FS)
        self.t0 = n / FS
        self.total = 0
        while True:
            await asyncio.sleep(0.1)
            n1 = int(time.time() * FS)
            chunk = self.demo.samples(n, n1)
            n = n1
            await self._on_audio(chunk, None)

    async def _on_audio(self, x: np.ndarray, t_arrival):
        n = len(x)
        if n == 0:
            return
        i = self.total % len(self.ring)
        first = min(n, len(self.ring) - i)
        self.ring[i:i + first] = x[:first]
        self.ring[:n - first] = x[first:]
        self.total += n
        if t_arrival is not None:
            cand = t_arrival - self.total / FS
            if self.t0 is None or abs(cand - self.t0) > 0.5:
                self.t0 = cand
            else:
                self.t0 += 0.02 * (cand - self.t0)
        self._level_acc.append(float(np.mean(x.astype(np.float64) ** 2)))
        self.since_wf += n
        while self.since_wf >= WF_HOP:
            self.since_wf -= WF_HOP
            await self._waterfall_row()

    def samples(self, t_start, t_end):
        n0 = int(round((t_start - self.t0) * FS))
        n1 = int(round((t_end - self.t0) * FS))
        out = np.zeros(n1 - n0, np.int16)
        lo, hi = max(n0, self.total - len(self.ring)), min(n1, self.total)
        if hi > lo:
            idx = np.arange(lo, hi) % len(self.ring)
            out[lo - n0:hi - n0] = self.ring[idx]
        return out

    async def _waterfall_row(self):
        if self.total < FFT_N:
            return
        idx = np.arange(self.total - FFT_N, self.total) % len(self.ring)
        x = self.ring[idx].astype(np.float32) * np.hanning(FFT_N).astype(np.float32)
        spec = np.abs(np.fft.rfft(x)[:WF_BINS]) ** 2
        db = 10 * np.log10(spec + 1e-3)
        floor = float(np.median(db[60:]))
        self.noise_floor = floor if self.noise_floor is None else 0.9 * self.noise_floor + 0.1 * floor
        row = np.clip((db - self.noise_floor + 4) * (255 / 34), 0, 255).astype(np.uint8)
        await self.broadcast_bin(row.tobytes())
        if self._level_acc:
            p = float(np.mean(self._level_acc))
            self._level_acc = []
            self.level_db = 10 * np.log10(p / 32768 ** 2 + 1e-12)
            await self.broadcast({"type": "level", "db": round(self.level_db, 1)})

    # -------------------------------------------------------------- timing

    async def _scheduler(self):
        last_poll = last_beat = 0
        while True:
            await asyncio.sleep(0.05)
            now = self.band_time()
            m = self.m
            p = int(now // m.PERIOD)
            pos = now - p * m.PERIOD
            try:
                if pos >= m.DECODE_AT and self.last_decoded < p and self.t0 is not None:
                    self.last_decoded = p
                    if p not in self.tx_periods:
                        asyncio.create_task(self._decode(p))
                q = self.qso
                if (q.tx_enabled and p % 2 == q.parity and p not in self.tx_periods
                        and self.tx_task is None and self.tune_task is None):
                    if self.decoding and pos < 1.0:
                        continue  # let the previous period's decode finish first
                    if pos <= m.TX_START + m.LATE_START_MAX:
                        self.tx_periods.add(p)
                        self.tx_task = asyncio.create_task(self._transmit(p))
                q.watchdog()
                if now - last_beat > 15:
                    last_beat = now
                    self.udp.heartbeat()
                    self._udp_status()
                if now - last_poll > 2:
                    last_poll = now
                    if self.rig.enabled:
                        was = (self.rig.up, self.rig.freq, self.rig.error)
                        await self.rig.poll()
                        if self.rig.up and not was[0]:
                            await self._apply_rig_mode()   # just connected
                        if was != (self.rig.up, self.rig.freq, self.rig.error):
                            await self.push_state()
            except Exception:  # noqa: BLE001
                log.exception("scheduler")

    # --------------------------------------------------------------- decode

    async def _decode(self, p):
        self.decoding = True
        mode = self.m
        try:
            t_start = p * mode.PERIOD
            off = self.clock_offset   # the cycle by the band's time, on the computer's clock
            audio = self.samples(t_start + off, t_start + off + mode.PERIOD)
            hhmmss = time.strftime("%H%M%S", time.gmtime(t_start))
            path = os.path.join(self.tmp, f"{time.strftime('%y%m%d', time.gmtime(t_start))}_{hhmmss}.wav")
            with wave.open(path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(FS)
                wf.writeframes(audio.tobytes())
            jt9 = find_tool("jt9")
            if not jt9:
                raise RuntimeError("WSJT-X's decoder (jt9) wasn't found - is WSJT-X installed?")
            args = [jt9, mode.JT9_MODE, "-d", str(self.s["depth"]), "-a", self.tmp, "-t", self.tmp]
            if self.s["mycall"]:
                args += ["-c", self.s["mycall"].upper()]
            if self.s["mygrid"]:
                args += ["-G", self._grid()[:4].upper()]
            if self.qso.dxcall:
                args += ["-x", self.qso.dxcall]
            self._remember_call(self.s["mycall"], ours=True)
            self._remember_call(self.qso.dxcall, ours=True)
            primer = await self._primer_wav(mode)
            if primer:
                args.append(primer)
            args.append(path)
            proc = await asyncio.create_subprocess_exec(
                *args, cwd=self.tmp, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, **NO_WINDOW)
            out, _ = await asyncio.wait_for(proc.communicate(), 12)
            if mode is not self.m:
                return   # the mode was switched while decoding: these belong to the old one
            os.unlink(path)
            decodes, seen = [], set()
            for line in out.decode(errors="replace").splitlines():
                m = LINE_RE.match(line)
                if not m or line.startswith(PRIMER_TIME):
                    continue
                # jt9 ends a line with "a1".."a7" when it filled part of the message
                # in by guessing (a priori decoding: "CQ ???", "MYCALL ???"...), or
                # "?" when unsure. Such guesses now and then turn noise into a
                # station calling you that doesn't exist, so keep them marked.
                tag = re.search(r"\s+(\?|a\d+)$", m.group(4))
                msg = m.group(4)[:tag.start()].strip() if tag else m.group(4).strip()
                if msg in seen:
                    continue
                seen.add(msg)
                d = self._annotate(p, hhmmss, int(m.group(1)), float(m.group(2)), int(m.group(3)), msg)
                if tag:
                    d["uncertain"] = tag.group(1)
                decodes.append(d)
            decodes.sort(key=lambda d: d["freq"])
            self._remember_calls(decodes)
            with_grid = {d["p"]["from"] for d in decodes if d["p"]["from"] and d["p"]["grid"]}
            if with_grid:
                self._backfill_log_grids(with_grid)
            self._save_grids()
            batch = {"type": "decodes", "period": p, "time": hhmmss, "items": decodes,
                     "band": band_of(self.dial), "dial": self.dial}
            self.history.append(batch)
            await self.broadcast(batch)
            for d in decodes:
                self.udp.decode(p * mode.PERIOD, d["snr"], d["dt"], d["freq"], d["msg"], mode.UDP_MODE,
                                low_confidence=bool(d.get("uncertain")))
            self.qso.on_decodes(decodes, p)
            self._note_clock(decodes, off)
            await self.push_state()
        except Exception as e:  # noqa: BLE001
            log.exception("decode")
            self.toast(f"Decoder error: {e}", "bad")
        finally:
            self.decoding = False

    def band_time(self):
        """Now, by the band's clock (the computer's, with any band sync applied)."""
        return time.time() - self.clock_offset

    def _note_clock(self, decodes, off=0.0):
        """Keep each station's latest DT for the band clock check, as measured
        by the computer's own clock (`off` = the correction in use when that
        audio was cut). Guessed ("?"/a1..) decodes, hashed calls and wild DTs
        are left out. With automatic band sync, nudges the correction."""
        now = time.time()
        for d in decodes:
            frm = d["p"].get("from") or ""
            if not frm or frm.startswith("<") or d.get("uncertain") or abs(d["dt"]) > CLOCK_MAX_DT:
                continue
            self.clock_dts[frm] = (now, d["dt"] + off)
        for k in [k for k, (t, _) in self.clock_dts.items() if t < now - CLOCK_WINDOW_SEC]:
            del self.clock_dts[k]
        band = self._band_dt()
        if band is not None and (self._clock_logged is None or abs(band - self._clock_logged) >= 0.1):
            self._clock_logged = band
            log.info("band clock: computer clock %+.2f s from the band (median of %d stations)",
                     band, len(self.clock_dts))
        if self.s["clock_sync"] == "auto" and band is not None:
            self._auto_sync(band)

    def _band_dt(self):
        """Median DT of the stations heard lately, by the computer's clock:
        how far it is ahead of the band. None if too few stations."""
        vals = sorted(v for _, v in self.clock_dts.values())
        n = len(vals)
        if n < CLOCK_MIN_STATIONS:
            return None
        mid = n // 2
        return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2

    def _auto_sync(self, band):
        """Move the correction part of the way towards the band each cycle -
        in small steps, and never while transmitting (a shift mid-transmission
        would stretch or cut it)."""
        err = band - self.clock_offset
        if abs(err) < 0.05 or self.tx_task or self.tune_task:
            return
        step = err if abs(err) < 0.1 else max(-0.5, min(0.5, err / 2))
        self._set_offset(self.clock_offset + step)

    def _set_offset(self, off):
        off = round(max(-CLOCK_MAX_OFFSET, min(CLOCK_MAX_OFFSET, off)), 2)
        if off != self.clock_offset:
            log.info("band sync: Procyon's timing %+.2f s from the computer's clock", off)
        self.clock_offset = off

    def _clock_status(self):
        band = self._band_dt()
        return {"n": len(self.clock_dts), "band": None if band is None else round(band, 2),
                "offset": self.clock_offset, "sync": self.s["clock_sync"]}

    def sync_clock_now(self):
        """The manual "Sync to the band" click."""
        band = self._band_dt()
        if band is None:
            self.toast(f"Not enough stations to sync to yet: needs {CLOCK_MIN_STATIONS} heard in the "
                       f"last 2 minutes ({len(self.clock_dts)} so far).", "warn")
        elif self.tx_task or self.tune_task:
            self.toast("Transmitting - sync again once this transmission ends.", "warn")
        else:
            self._set_offset(band)
            self.toast(f"Synced to the band: Procyon's timing is now {self.clock_offset:+.1f} s "
                       f"from the computer's clock.", "info")

    def _remember_call(self, tok: str, ours=False):
        """Note a callsign written in full if it's one FT8 sends as a code. Our
        own QSO's /P or /R calls too (`ours`): a portable station may send them
        as a code when both sides are portable."""
        c = (tok or "").upper()
        if (not c or c.startswith("<") or not ft8.is_call(c) or ft8.GRID_RE.match(c)
                or (ft8.is_std_call(c) and not (ours and "/" in c))):
            return
        self.long_calls.pop(c, None)
        self.long_calls[c] = True
        while len(self.long_calls) > LONG_CALLS_MAX:
            self.long_calls.popitem(last=False)

    def _remember_calls(self, decodes):
        for d in decodes:
            toks, p = d["msg"].split(), d["p"]
            if p["kind"] == "cq":
                for t in toks[1:3]:
                    if ft8.strip_call(t) == p["from"]:
                        self._remember_call(t)
            elif p["kind"] == "std":
                self._remember_call(toks[0])
                self._remember_call(toks[1])

    async def _primer_wav(self, m) -> str:
        """A clean recording of "CQ <call>" for every remembered long callsign,
        in mode `m`, rebuilt only when the list or the mode changes. "" when
        there are none."""
        calls = tuple(self.long_calls)
        if not calls:
            return ""
        if self._primer[:2] != (calls, m.NAME) or not os.path.exists(self._primer[2]):
            path = os.path.join(self.tmp, f"000000_{PRIMER_TIME}.wav")
            await asyncio.to_thread(self._write_primer, m, calls, path)
            self._primer = (calls, m.NAME, path)
        return self._primer[2]

    @staticmethod
    def _write_primer(m, calls, path):
        x = np.zeros(int(m.PERIOD * FS))
        amp = min(1500.0, 24000.0 / len(calls))   # all together never clip
        start = int(m.TX_START * FS)
        step = 60 if m is ft8 else 100   # FT4 signals are wider
        for i, c in enumerate(calls):
            msg = f"CQ {c}"
            tones, decoded = m.encode(msg)
            if decoded != msg:
                continue
            w = m.synth(tones, 300 + (step * i) % 3400, FS) * amp
            x[start:start + len(w)] += w[:len(x) - start]
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(FS)
            wf.writeframes(np.clip(x, -32767, 32767).astype(np.int16).tobytes())

    # Countries too big for a "middle of the country" distance to mean anything.
    BIG_COUNTRIES = {"United States", "Canada", "Alaska", "Asiatic Russia", "European Russia",
                     "Brazil", "Australia", "China", "Argentina", "India", "Kazakhstan",
                     "Mexico", "Indonesia", "Chile", "Peru", "Greenland"}

    # Countries (cty.dat names) whose operators get American spelling, and those
    # that measure distance in miles - picked from your own callsign.
    US_ENTITIES = {"United States", "Alaska", "Hawaii", "Puerto Rico", "US Virgin Islands",
                   "Guam", "Mariana Islands", "American Samoa", "Swains Island", "Navassa Island",
                   "Desecheo Island", "Baker & Howland Islands", "Johnston Island",
                   "Midway Island", "Palmyra & Jarvis Islands", "Wake Island"}
    MILES_ENTITIES = US_ENTITIES | {"England", "Scotland", "Wales", "Northern Ireland",
                                    "Isle of Man", "Jersey", "Guernsey"}

    def _home(self):
        """Where your callsign is from, and what that means for the display.
        Without a callsign (or one cty.dat doesn't know): American, km."""
        e = self.cty.lookup(self.s["mycall"]) if self.s["mycall"] else None
        country = e["country"] if e else ""
        return {"country": country, "us_spelling": not country or country in self.US_ENTITIES,
                "miles": country in self.MILES_ENTITIES}

    def _station_info(self, call, grid):
        e = self.cty.lookup(call)
        info = {"country": e["country"] if e else "", "cont": e["cont"] if e else ""}
        grid = grid or self.grids.get(call, "")
        mine = grid_to_latlon(self.s["mygrid"])
        there = grid_to_latlon(grid)
        if not there and e:
            # No grid known: estimate from the country, unless it's ours or huge.
            my_country = (self.cty.lookup(self.s["mycall"]) or {}).get("country")
            if e["country"] not in self.BIG_COUNTRIES and e["country"] != my_country:
                there = (e["lat"], e["lon"])
        db = distance_bearing(mine, there)
        if db:
            info["km"], info["az"] = db
            info["approx"] = not grid
        if grid:
            info["grid"] = grid
        return info

    # ------------------------------------------------------- grid memory

    def _load_grids(self):
        """Grids we've seen stations send, so a later message without a grid
        (or a QSO that never included one) still gets the right location."""
        self.grids: dict[str, str] = {}
        try:
            with open(self.grids_path) as f:
                self.grids.update(json.load(f))
        except (OSError, ValueError):
            pass
        for q in self.logbook.qsos:   # the logbook knows grids too
            if q.get("call") and q.get("gridsquare"):
                self.grids.setdefault(q["call"].upper(), q["gridsquare"][:4].upper())
        self._grids_dirty = False

    def _learn_grid(self, call: str, grid: str):
        grid = grid[:4].upper()
        if call and grid and self.grids.get(call) != grid:
            self.grids[call] = grid
            self._grids_dirty = True

    def _save_grids(self):
        if not self._grids_dirty:
            return
        if len(self.grids) > 50000:   # keep the file small
            self.grids = dict(list(self.grids.items())[-40000:])
        tmp = self.grids_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.grids, f)
        os.replace(tmp, self.grids_path)
        self._grids_dirty = False

    def _backfill_log_grids(self, calls):
        """A station just sent its grid: add it to recent QSOs logged without one.
        Only the last 6 hours - portable stations move, so older QSOs are left alone."""
        cutoff = (utc_now() - dt.timedelta(hours=6)).strftime("%Y%m%d%H%M%S")
        for idx, q in enumerate(self.logbook.qsos):
            call = q.get("call", "").upper()
            if call in calls and not q.get("gridsquare") and self.grids.get(call):
                if q.get("qso_date", "") + q.get("time_on", "") >= cutoff:
                    self.logbook.update(idx, {"gridsquare": self.grids[call]})
                    self.toast(f"Added grid {self.grids[call]} to your QSO with {call}", "good")

    def _annotate(self, p, hhmmss, snr, dt, freq, msg):
        parsed = ft8.parse(msg, self.s["mycall"])
        my = self.s["mycall"].upper()
        d = {"period": p, "parity": p % 2, "time": hhmmss, "snr": snr, "dt": dt,
             "freq": freq, "msg": msg, "p": parsed,
             "to_me": bool(my) and ft8.same_station(parsed["to"], my),
             "from_me": bool(my) and ft8.same_station(parsed["from"], my)}
        call = parsed["from"]
        if call:
            if parsed["grid"]:
                self._learn_grid(call, parsed["grid"])
            d.update(self._station_info(call, parsed["grid"]))
            band = band_of(self.dial)
            d["new"] = not self.logbook.worked(call)
            d["new_band"] = not d["new"] and not self.logbook.worked(call, band)
        return d

    # ------------------------------------------------------------- transmit

    async def _transmit(self, p):
        q = self.qso
        m = self.m
        idx = q.next
        text = ft8.normalize(q.msgs[idx])
        try:
            if not text:
                q.tx_enabled = False
                return
            if not self.s["mycall"]:
                q.tx_enabled = False
                self.toast("Set your callsign in Settings before transmitting", "warn")
                return
            tones, decoded = m.encode(text)
            if len(decoded) < len(text):
                # FT8/FT4 would cut this message short (e.g. a callsign it can't
                # encode falling back to 13 characters of free text). Never put
                # a cut-off callsign on the air: don't key up at all.
                q.tx_enabled = False
                self.toast(f"Not sent: {m.NAME} can't fit “{text}” - it would go out cut short as "
                           f"“{decoded}”. Edit the message and enable TX again.", "bad")
                return
            if decoded != text and decoded != self._warned:
                self._warned = decoded
                self.toast(f"Message will be received as “{decoded}”", "warn")
            q.note_tx(p, decoded)
            target = p * m.PERIOD + m.TX_START + self.clock_offset   # on the computer's clock
            late = max(0.0, time.time() - target)
            self.tx_info = {"msg": decoded, "period": p, "idx": idx, "freq": self.s["tx_freq"],
                            "start": target, "end": target + m.SIGNAL_SEC,
                            "late": round(late, 1) if late >= 0.5 else 0}
            self._halt.clear()
            await self.push_state()
            if self.s["demo"]:
                self.demo.user_tx(p, decoded)
                try:
                    await asyncio.wait_for(self._halt.wait(), self.tx_info["end"] - time.time())
                except asyncio.TimeoutError:
                    pass
            else:
                f0 = float(self.s["tx_freq"])
                await self._play(lambda rate: m.synth(tones, f0, rate), target)
            if not self._halt.is_set():
                q.after_tx(idx)
        except Exception as e:  # noqa: BLE001
            log.exception("transmit")
            self.toast(f"Transmit failed: {e}", "bad")
            q.tx_enabled = False
        finally:
            self.tx_info = None
            self.tx_task = None
            await self.push_state()

    async def _play(self, make_wave, target: float):
        """Key the radio and play make_wave(rate) - a float waveform at the sound
        card's rate - starting at `target`. PTT is always released afterwards."""
        if self.s["ptt"] != "vox":
            # Never play audio into a radio that isn't actually transmitting.
            if not self.rig.enabled:
                raise RuntimeError("PTT is set to use radio control, but radio control is off, so "
                                   "Procyon can't key your radio. Turn on radio control in Settings, "
                                   "or set PTT to VOX.")
            if not await self.rig.ptt(True):
                await self.rig.ptt(False)
                raise RuntimeError("The radio didn't confirm it was transmitting (PTT). "
                                   f"{(self.rig.error or 'It refused the command').rstrip('.')}. "
                                   "Check radio control and PTT in Settings.")
        try:
            # Audio drive is read live, so moving the slider mid-transmission (e.g.
            # while watching ALC during Tune) takes effect straight away.
            await self.audio.play(self.s["audio_out"], make_wave, target, self._halt,
                                  lambda: 32767 * (self.s["tx_level"] / 100.0) ** 2)
        finally:
            if self.s["ptt"] != "vox":
                await self.rig.ptt(False)

    async def _radio_job(self, fn, *args):
        """Run blocking serial-port work. Procyon's own rigctld holds the port,
        so it is stopped for the duration and restarted after."""
        await self.halt()
        await self.rig.close()
        try:
            return await asyncio.get_running_loop().run_in_executor(None, fn, *args)
        finally:
            await self.rig.configure(self.s)
            await self.rig.poll()
            await self.push_state()

    async def _rig_test(self, m: dict):
        """Try the radio settings on screen (saved or not) without keying it."""
        if not m.get("spawn"):       # an external rigctld: just ask it
            await self.rig.poll()
            ok = self.rig.connected and self.rig.freq
            return {"type": "rig_test", "ok": bool(ok), "freq": self.rig.freq,
                    "error": "" if ok else (self.rig.error or "No answer from rigctld")}
        res = await self._radio_job(test_rig, str(m.get("model") or ""),
                                    str(m.get("device") or ""), str(m.get("baud") or ""))
        return {"type": "rig_test", **res}

    def _install_wsjtx(self):
        """Download and install WSJT-X in the background, reporting progress to the browser."""
        if self._installing:
            return
        self._installing = True
        loop = asyncio.get_running_loop()

        def progress(text, frac=None):     # called from the installer's thread
            loop.call_soon_threadsafe(
                lambda: loop.create_task(self.broadcast({"type": "install", "text": text, "frac": frac})))

        async def run():
            try:
                await loop.run_in_executor(None, wsjtx_install.install, progress)
                hamlib_rigs.cache_clear()      # the radio list needs WSJT-X's Hamlib
                await self.rig.configure(self.s)
                await self.broadcast({"type": "install", "done": True,
                                      "text": "WSJT-X is installed in your Applications folder."})
                self.toast("WSJT-X installed - Procyon is ready", "good")
            except wsjtx_install.InstallError as e:
                await self.broadcast({"type": "install", "error": str(e)})
            finally:
                self._installing = False
                await self.push_state()

        loop.create_task(run())

    # ------------------------------------------------ when every tab is closed

    TX_ALONE_SEC = 10     # long enough for a page reload to reconnect
    QUIT_ALONE_SEC = 60

    def client_joined(self):
        self._ever_connected = True
        if self._alone_task:
            self._alone_task.cancel()
            self._alone_task = None

    def client_left(self):
        if not self.clients and self._ever_connected and not self._alone_task:
            self._alone_task = asyncio.get_event_loop().create_task(self._alone())

    async def _alone(self):
        """No Procyon tab is open anywhere: never keep transmitting with nobody
        watching, and (unless switched off) stop Procyon itself after a minute, so
        it doesn't sit in the background holding the radio and sound card."""
        try:
            await asyncio.sleep(self.TX_ALONE_SEC)
            if self.clients:
                return
            if self.qso.tx_enabled or self.tx_task or self.tune_task:
                log.info("no Procyon tab open - transmitting stopped")
                await self.halt()
                await self.push_state()
            if not self.s["auto_quit"]:
                return
            await asyncio.sleep(self.QUIT_ALONE_SEC - self.TX_ALONE_SEC)
            if self.clients:
                return
            log.info("no Procyon tab open for a minute - stopping")
            if self.on_quit:
                self.on_quit()
        except asyncio.CancelledError:
            pass
        finally:
            if self._alone_task is asyncio.current_task():
                self._alone_task = None

    async def halt(self):
        self.qso.tx_enabled = False
        self._halt.set()
        if self.tune_task:
            self.tune_task.cancel()

    async def shutdown(self):
        """Before Procyon exits (Quit or Ctrl-C): end any transmission and make
        sure the radio is back on receive BEFORE the radio connection closes."""
        await self.halt()
        busy = [t for t in (self.tune_task, self.tx_task) if t]
        if busy:
            await asyncio.wait(busy, timeout=3)   # their own cleanup unkeys the radio
        if self.rig.enabled and self.s["ptt"] != "vox":
            try:
                if not await self.rig.ptt(False):   # once more, to be certain
                    log.warning("shutdown: the radio didn't confirm receive")
            except Exception:  # noqa: BLE001
                log.exception("shutdown: couldn't set the radio to receive")

    async def tune(self, on: bool):
        if not on:
            if self.tune_task:
                self.tune_task.cancel()
            return
        if self.tune_task or self.tx_task or self.s["demo"]:
            if self.s["demo"]:
                self.toast("Tune is not available in demo mode", "warn")
            return

        async def run():
            try:
                f0 = float(self.s["tx_freq"])

                def tone(rate):
                    return np.sin(2 * np.pi * f0 * np.arange(int(30 * rate)) / rate)
                self._halt.clear()
                self.tx_info = {"msg": "TUNE", "freq": self.s["tx_freq"], "tune": True,
                                "start": time.time(), "end": time.time() + 30}
                await self.push_state()
                await self._play(tone, time.time())
            except asyncio.CancelledError:
                pass   # Tune switched off: _play has already stopped the audio and PTT
            except Exception as e:  # noqa: BLE001
                log.exception("tune")
                self.toast(f"Tune failed: {e}", "bad")
            finally:
                self.tune_task = None
                self.tx_info = None
                await self.push_state()

        self.tune_task = asyncio.create_task(run())

    # ----------------------------------------------------------------- log

    def _log_qso(self, q: Qso):
        if not q.dxgrid:   # they never sent it in this QSO, but we may have seen it
            q.dxgrid = self.grids.get(q.dxcall, "")
        now = utc_now()
        start = q.started or now
        dial = self.dial
        rec = {
            "call": q.dxcall, "gridsquare": q.dxgrid, **self.m.ADIF,
            "rst_sent": q.sent_report(), "rst_rcvd": q.report_rcvd,
            "qso_date": start.strftime("%Y%m%d"), "time_on": start.strftime("%H%M%S"),
            "qso_date_off": now.strftime("%Y%m%d"), "time_off": now.strftime("%H%M%S"),
            "band": band_of(dial), "freq": f"{(dial + int(self.s['tx_freq'])) / 1e6:.6f}",
            "station_callsign": self.s["mycall"].upper(), "my_gridsquare": self._grid().upper(),
        }
        if self.s["pota_active"] and self.s["pota_parks"]:
            parks = ",".join(self.s["pota_parks"])
            rec.update(my_sig="POTA", my_sig_info=parks, my_pota_ref=parks)
            if self.s["pota_state"]:
                rec["my_state"] = self.s["pota_state"]
        if str(self.s["tx_watts"]).strip():
            rec["tx_pwr"] = str(self.s["tx_watts"]).strip()
        rec["comment"] = self._comment(self._qso_comment(q))
        self.logbook.add(rec)
        q.log_idx = len(self.logbook.qsos) - 1
        if self.s["pota_active"]:
            self._pota_save()
        if not self.s["demo"]:   # never put simulated QSOs into companion apps' logs
            adif = (f"<adif_ver:5>3.1.4 <programid:7>Procyon <programversion:{len(__version__)}>"
                    f"{__version__} <EOH>\n" + Logbook._record(rec))
            self.udp.qso_logged(rec, start, now, adif)
        self.toast(f"Logged QSO with {q.dxcall}", "good")

    def _remote_settings(self, new: dict):
        pin = str(new.get("remote_pin", "")).strip()
        if pin:
            if PIN_RE.match(pin):
                self.s["remote_pin_hash"] = self.auth.hash_pin(pin)
            else:
                self.toast("The PIN must be 4 to 8 digits - it was not changed", "warn")
        want = bool(new.get("remote_enabled", self.s["remote_enabled"]))
        if want and not self.s["remote_pin_hash"]:
            self.toast("Set a PIN before allowing other devices", "warn")
            want = False
        self.s["remote_enabled"] = want
        self.save()

    async def _kick_remote_clients(self):
        """After a PIN change, signed-in network devices must sign in again."""
        for ws in list(self.clients):
            if not getattr(ws, "procyon_local", True):
                await ws.close(code=4001, message=b"PIN changed")

    RIG_MODES = {"data": ("PKTUSB", "DATA"), "usb": ("USB", "USB")}

    async def _apply_rig_mode(self):
        """Put the radio in the mode chosen for FT8, as WSJT-X does whenever it
        connects or changes band."""
        want = self.RIG_MODES.get(self.s.get("rig_mode"))
        if not want or not self.rig.enabled:
            return
        if not await self.rig.set_mode(want[0]) and self.rig.connected:
            self.toast(f"Couldn't switch the radio to {want[1]} mode - please set it on the radio", "warn")

    def _band_dial(self, band) -> int:
        """The band's usual dial frequency for the mode in use."""
        return band[4] if self.m is ft4 and band[4] else band[3]

    async def _set_mode(self, mode: str):
        """Switch between FT8 and FT4. Transmitting stops, the QSO and Band
        activity start fresh (their cycle numbers belong to the old mode), and
        if the radio sits on the old mode's usual frequency it moves to the new
        one's on the same band, as WSJT-X does."""
        if mode not in MODES or mode == self.s["mode"]:
            return
        await self.halt()
        busy = [t for t in (self.tx_task, self.tune_task) if t]
        if busy:
            await asyncio.wait(busy, timeout=3)
        old_dial = self.dial
        self.s["mode"] = mode
        self.last_decoded = 0
        self.tx_periods.clear()
        self.qso.reset()
        self.qso.regen()
        self.qso.next = 5
        self.history.clear()
        self.clock_dts.clear()
        await self.broadcast({"type": "history", "batches": []})
        self.udp.clear()
        self.demo = DemoBand(self.m)
        self.demo.mycall = self.s["mycall"].upper()
        retune = False
        for b in BANDS:
            if old_dial in (b[3], b[4]) and self._band_dial(b) != old_dial:
                self.s["dial_hz"] = self._band_dial(b)
                retune = self.rig.enabled
                break
        self._clean_wf_range()   # FT4 is wider: keep the TX/RX markers inside the range
        self.save()
        log.info("mode switched to %s", mode)
        if retune:
            # The radio may be slow to answer (or not answering at all), so
            # the switch shows at once and the radio follows in the background.
            asyncio.get_event_loop().create_task(self._retune(self.s["dial_hz"]))

    async def _retune(self, hz: int):
        await self.rig.set_freq(hz)
        await self.push_state()

    def _audio_range(self):
        """Where a signal (50 Hz wide in FT8, 83 in FT4) can sit inside the chosen waterfall range."""
        return max(100, int(self.s["wf_start"])), min(4000 - self.m.WIDTH, int(self.s["wf_end"]) - self.m.WIDTH)

    def _clean_wf_range(self):
        try:
            start = int(float(self.s["wf_start"]))
            end = int(float(self.s["wf_end"]))
        except (TypeError, ValueError):
            start, end = 0, 3000
        start = max(0, min(3500, start))
        end = max(start + 500, min(4000, end))
        self.s["wf_start"], self.s["wf_end"] = start, end
        lo, hi = self._audio_range()
        for key in ("tx_freq", "rx_freq"):   # keep the markers inside the new range
            self.s[key] = int(max(lo, min(hi, self.s[key])))

    def _qso_comment(self, q: Qso) -> str:
        """The user's note, plus the signal reports if they asked for them."""
        parts = [q.note.strip()]
        if self.s.get("log_reports_in_comment"):
            parts.append(f"{self.m.NAME} Sent: {q.sent_report()} Rcvd: {q.report_rcvd or '?'}")
        return " · ".join(p for p in parts if p)

    def _comment(self, note: str) -> str:
        """Demo QSOs keep a marker in front of the note, so they're never
        mistaken for real contacts."""
        note = note.strip()
        if self.s["demo"]:
            return f"{DEMO_MARK} - {note}" if note else DEMO_MARK
        return note

    def _log_delete(self, m: dict) -> None:
        """Delete one QSO from the log by hand - e.g. a second entry made when a
        station missed our 73, sent RR73 again later and we answered it."""
        i = int(m.get("idx", -1))
        qsos = self.logbook.qsos
        # the index must still point at the QSO the user saw (a QSO logged
        # in the meantime would not move it, but an import could)
        if not (0 <= i < len(qsos)) or qsos[i].get("call", "") != m.get("call") \
                or qsos[i].get("qso_date", "") + qsos[i].get("time_on", "") != m.get("when"):
            self.toast("That log entry has changed - nothing was deleted", "warn")
            return
        if self.uploading:
            self.toast("An upload is running - try again when it has finished", "warn")
            return
        q = self.logbook.remove(i)
        cur = self.qso.log_idx
        if cur is not None:   # keep the QSO in progress pointing at its own entry
            self.qso.log_idx = None if cur == i else cur - 1 if cur > i else cur
        if q.get("my_sig", "").upper() == "POTA":
            self._pota_save(drop=[q])
        log.info("log entry deleted by hand: %s %s %s %s", q.get("call"),
                 q.get("qso_date"), q.get("time_on"), q.get("band"))
        on_qrz = q.get("qrzcom_qso_upload_status", "").upper() == "Y"
        self.toast(f"Deleted the QSO with {q.get('call', '')}"
                   + (" - it's still on QRZ; delete it there too" if on_qrz else ""),
                   "warn" if on_qrz else "good")

    def _logged_entry(self, q: Qso):
        """Index of this QSO's log entry, if it's still in the open logbook."""
        i = q.log_idx
        if i is not None and i < len(self.logbook.qsos) and self.logbook.qsos[i].get("call") == q.dxcall:
            return i
        return None

    # ------------------------------------------------------ POTA activation

    def _grid(self) -> str:
        """Where we're operating from: the park's grid during an activation."""
        if self.s["pota_active"] and self.s["pota_grid"]:
            return self.s["pota_grid"]
        return self.s["mygrid"]

    def _set_station(self):
        self.qso.cq_tag = "POTA" if self.s["pota_active"] else ""
        self.qso.set_station(self.s["mycall"], self._grid())

    async def _pota_start(self, m: dict) -> dict:
        """Start an activation, or change the parks/grid of the one that's on.
        Answered inside the POTA window (notices would be hidden behind it)."""
        parks, bad = pota.parse_parks(str(m.get("parks", "")))
        grid = str(m.get("grid", "")).strip()
        state = re.sub(r"[^A-Z0-9]", "", str(m.get("state", "")).upper())[:6]
        if bad:
            return {"type": "pota", "ok": False,
                    "text": f"{', '.join(bad)} doesn't look like a park reference (e.g. US-1234)."}
        if not parks:
            return {"type": "pota", "ok": False, "text": "Enter at least one park, e.g. US-1234."}
        if grid and not re.match(r"^[A-Ra-r]{2}\d{2}([A-Xa-x]{2})?$", grid):
            return {"type": "pota", "ok": False, "text": f"{grid} isn't a grid square (e.g. EN80 or EN80ma)."}
        if not self.s["mycall"]:
            return {"type": "pota", "ok": False, "text": "Enter your callsign in Settings first."}
        was_on = self.s["pota_active"]
        self.s.update(pota_parks=parks, pota_state=state,
                      pota_grid=grid[:4].upper() + grid[4:].lower() if grid else "")
        if not was_on:
            self.s.update(pota_active=True, pota_end="", pota_start=utc_now().strftime("%Y%m%d%H%M%S"))
        self.save()
        self._set_station()
        await self.push_state()
        where = ", ".join(parks)
        if not was_on and not self.qso.msgs[5].startswith("CQ POTA"):
            return {"type": "pota", "ok": True, "text": f"Activation started at {where}. Note: {self.m.NAME} can't "
                    f"send \"CQ POTA\" with {self.s['mycall']}, so your CQ goes out as a plain CQ."}
        return {"type": "pota", "ok": True,
                "text": f"Activation {'updated' if was_on else 'started'}: {where}. Good luck!"}

    def _pota_end(self):
        if not self.s["pota_active"]:
            return
        self.s.update(pota_active=False, pota_end=utc_now().strftime("%Y%m%d%H%M%S"))
        self.save()
        self._set_station()
        self._pota_save()
        n = self._pota_status()["total"]
        self.toast(f"Activation ended with {n} QSO{'s' if n != 1 else ''}"
                   + (" - the park logs are in the POTA window" if n else ""), "good")

    def _pota_qsos(self, park: str) -> list[dict]:
        return pota.activation_qsos(self.logbook.qsos, park, self.s["pota_start"], self.s["pota_end"])

    def _pota_status(self) -> dict:
        parks, start = self.s["pota_parks"], self.s["pota_start"]
        out = {"active": self.s["pota_active"], "parks": parks, "grid": self.s["pota_grid"],
               "state": self.s["pota_state"], "start": start, "needed": pota.NEEDED,
               "today": 0, "total": 0, "files": []}
        if not (parks and start):
            return out
        qsos = self._pota_qsos(parks[0])     # the same QSOs count for every park
        out["today"] = pota.count_on(qsos, utc_now().strftime("%Y%m%d"))
        out["total"] = len(qsos)
        out["folder"] = self._pota_dir()
        if self.s["demo"]:   # practice park logs: plain text only, never ADIF
            out["files"] = [{"park": p, "name": self._pota_practice_name(p),
                             "count": len(self._pota_qsos(p))} for p in parks]
            return out
        out["files"] = [{"park": p, "name": os.path.basename(self._pota_path(p)),
                         "count": len(Logbook(self._pota_path(p)).qsos)
                         if os.path.exists(self._pota_path(p)) else 0} for p in parks]
        return out

    def _pota_practice_name(self, park: str) -> str:
        return os.path.splitext(os.path.basename(self._pota_path(park)))[0] + "-DEMO-practice.txt"

    def _pota_dir(self) -> str:
        """Park logs live next to the main log; demo QSOs get their own folder."""
        return os.path.join(self.data_dir, "pota-demo" if self.s["demo"] else "pota")

    def _pota_path(self, park: str) -> str:
        return os.path.join(self._pota_dir(), pota.filename(self.s["mycall"], park, self.s["pota_start"][:8]))

    def _pota_save(self, drop=()):
        """Keep each park's own log file up to date (after every QSO or edit).
        The QSOs stay in the main log too - that's what QRZ/LoTW uploads use.
        `drop`: QSOs just deleted from the main log, taken out of the park files too."""
        parks, start = self.s["pota_parks"], self.s["pota_start"]
        if not (parks and start and self.s["mycall"]) or self.s["demo"]:
            return   # demo park logs are never written as ADIF (see pota_export)
        for park in parks:
            current = [uploads.for_upload(q) for q in self._pota_qsos(park)]
            path = self._pota_path(park)
            saved = Logbook(path).qsos if os.path.exists(path) else []
            if not current and not saved:
                continue
            qsos = pota.merge(saved, current, drop)
            os.makedirs(self._pota_dir(), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(pota.export(qsos, park, Logbook._record, Logbook.HEADER))
            os.replace(tmp, path)

    def pota_export(self, park: str):
        """(file name, ADIF text) of one park's saved log, or None."""
        park = park.upper()
        if park not in self.s["pota_parks"] or not self.s["pota_start"]:
            return None
        if self.s["demo"]:
            return self._pota_practice_name(park), self.practice_log_text(
                self._pota_qsos(park), f"POTA park {park}")
        self._pota_save()
        path = self._pota_path(park)
        if not os.path.exists(path):
            return None
        return os.path.basename(path), open(path, encoding="utf-8").read()

    def practice_log_text(self, qsos=None, what="logbook") -> str:
        """The demo log as plain text, never ADIF: no logbook program or site can
        import it, so simulated QSOs can't end up on QRZ, LoTW or POTA by mistake."""
        qsos = self.logbook.qsos if qsos is None else qsos
        n = len(qsos)
        lines = [
            "DEMO PRACTICE LOG - SIMULATED STATIONS, NOT REAL QSOs",
            "Made with Procyon's demo band: nothing was transmitted or received.",
            "Do not upload or copy these into QRZ, LoTW, POTA or any other logbook.",
            "",
            f"{what.capitalize() if what == 'logbook' else what} - {self.s['mycall'] or 'no callsign'} - "
            f"{n} simulated QSO{'' if n == 1 else 's'} - exported {utc_now():%Y-%m-%d %H:%M} UTC",
            "",
            f"{'Date':10}  {'UTC':5}  {'Call':12} {'Grid':6} {'Band':5} {'Sent':>4}  {'Rcvd':>4}  Note",
        ]
        for q in qsos:
            d, t = q.get("qso_date", ""), q.get("time_on", "")
            lines.append(f"{d[:4]}-{d[4:6]}-{d[6:8]}  {t[:2]}:{t[2:4]}  {q.get('call', ''):12} "
                         f"{q.get('gridsquare', ''):6} {q.get('band', ''):5} {q.get('rst_sent', ''):>4}  "
                         f"{q.get('rst_rcvd', ''):>4}  {strip_demo_mark(q.get('comment', ''))}".rstrip())
        if not qsos:
            lines.append("(no QSOs yet)")
        lines += ["", "DEMO PRACTICE LOG - not real QSOs. Switch the demo band off to log real contacts."]
        return "\r\n".join(lines) + "\r\n"   # Windows line endings: opens cleanly in Notepad too

    # ------------------------------------------------- export new QSOs only

    def _not_exported(self) -> list[int]:
        return [i for i, q in enumerate(self.logbook.qsos) if not q.get(uploads.EXPORTED_FIELD)]

    def export_new(self):
        """(file name, ADIF text, count) of the QSOs not exported before, marked as
        exported now - for importing into another logging program (e.g. N3FJP)
        without bringing in the same QSOs twice."""
        idxs = self._not_exported()
        now = utc_now()
        qsos = [uploads.for_upload(self.logbook.qsos[i]) for i in idxs]
        text = Logbook.HEADER + "".join(Logbook._record(q) for q in qsos)
        self.logbook.mark(idxs, {uploads.EXPORTED_FIELD: now.strftime("%Y%m%d")})
        return f"procyon-new-qsos-{now:%Y%m%d-%H%M}.adi", text, len(idxs)

    def _export_mark_all(self) -> dict:
        """For switching the option on when the other program already has them all."""
        idxs = self._not_exported()
        self.logbook.mark(idxs, {uploads.EXPORTED_FIELD: utc_now().strftime("%Y%m%d")})
        n = len(idxs)
        return {"type": "export_marked", "text": (
            f"✓ {n} QSO{'' if n == 1 else 's'} marked as exported - the next export starts after them."
            if n else "✓ Every QSO was already marked as exported.")}

    # ---------------------------------------------------- online logbooks

    def _logbook_settings(self, new: dict):
        key = str(new.get("qrz_key_new", "")).strip()
        if key:
            self.s["qrz_key"] = key
        if new.get("qrz_key_forget"):
            self.s["qrz_key"] = ""
        # LoTW off (0.9.60):
        # if "lotw_location" in new:
        #     self.s["lotw_location"] = str(new["lotw_location"]).strip()
        # self.tqsl = uploads.find_tqsl()   # they may have just installed it
        self.save()

    def _upload_ready(self, service: str) -> bool:
        """Set up, and not the demo log (simulated QSOs never leave Procyon)."""
        if self.s["demo"]:
            return False
        if service == "qrz":
            return bool(self.s["qrz_key"])
        return bool(self.s["lotw_location"] and self.tqsl)

    def _upload_status(self) -> dict:
        out = {}
        for svc, (name, _, _) in uploads.SERVICES.items():
            ready = self._upload_ready(svc)
            out[svc] = {"name": name, "ready": ready, "busy": svc in self.uploading,
                        "pending": sum(not uploads.is_sent(q, svc) for q in self.logbook.qsos) if ready else 0}
        return out

    def _start_upload(self, service: str):
        if service not in uploads.SERVICES or service in self.uploading:
            return
        if self.importing:   # the import re-sorts the log under the upload's feet
            self.toast("Wait for the logbook import to finish, then upload", "warn")
            return
        if not self._upload_ready(service):
            self.toast(f"Set up {uploads.SERVICES[service][0]} in Settings → Logbook first", "warn")
            return
        self.uploading.add(service)
        asyncio.get_event_loop().create_task(self._upload(service))

    async def _upload(self, service: str):
        name, sent_field, date_field = uploads.SERVICES[service]
        book = self.logbook    # stays the same even if demo mode is switched mid-upload
        idxs = [i for i, q in enumerate(book.qsos) if not uploads.is_sent(q, service)]
        qsos = [book.qsos[i] for i in idxs]
        done, problem, note = [], "", ""
        try:
            if not qsos:
                self.toast(f"Everything is already on {name}", "good")
                return
            await self.push_state()   # show the button as busy
            if service == "qrz":
                pos, problem, dups = await uploads.qrz_upload(self.s["qrz_key"], qsos, Logbook._record)
                done = [idxs[p] for p in pos]
                if dups:
                    note = "QRZ already had all of them" if dups == len(done) else f"QRZ already had {dups}"
            else:   # LoTW - not reachable while LoTW is off (0.9.60)
                text = Logbook.HEADER + "".join(Logbook._record(uploads.for_upload(q)) for q in qsos)
                try:
                    note = await uploads.lotw_upload(self.tqsl, self.s["lotw_location"], text)
                    done = idxs
                except uploads.UploadError as e:
                    problem = str(e)
            book.mark(done, {sent_field: "Y", date_field: utc_now().strftime("%Y%m%d")})
            n = len(done)
            sent = f"{n} QSO{'s' if n != 1 else ''}" if n else ""
            if problem:
                left = len(qsos) - n
                self.toast((f"Sent {sent} to {name}. " if sent else "")
                           + f"{problem}. {left} QSO{'s' if left != 1 else ''} still waiting - "
                           f"press the button again to retry.", "bad")
            else:
                if note.endswith("all of them"):
                    self.toast(f"{name} already had all {sent} - they're marked as uploaded now", "good")
                else:
                    self.toast(f"Uploaded {sent} to {name}" + (f" ({note})" if note else ""), "good")
        except Exception:  # noqa: BLE001 - never leave the button stuck
            log.exception("upload to %s", name)
            self.toast(f"Upload to {name} failed unexpectedly - your QSOs are still waiting", "bad")
        finally:
            self.uploading.discard(service)
            await self.push_state()

    async def _qrz_check(self, key: str) -> dict:
        """Answered inside Settings (notices would be hidden behind it)."""
        key = key or self.s["qrz_key"]
        if not key:
            return {"type": "qrz_check", "ok": False, "text": "Paste your QRZ Logbook API key first."}
        try:
            return {"type": "qrz_check", "ok": True, "text": await uploads.qrz_check(key)}
        except uploads.UploadError as e:
            return {"type": "qrz_check", "ok": False, "text": str(e)}

    # ---------------------------------------------------- importing QSOs

    def import_qsos(self, qsos: list[dict], source: str, on_qrz=False) -> dict:
        """Merge QSOs from another logbook into Procyon's (skipping the ones it
        already has), after a dated backup of the log. `on_qrz`: they came from
        QRZ, so the Upload button mustn't send them back."""
        def say(text, ok=True):
            return {"type": "log_import", "ok": ok, "text": text}
        if self.s["demo"]:
            return say("Not in demo mode - switch the demo band off first.", False)
        if self.uploading:
            return say("An upload is running - try again when it has finished.", False)
        incoming = [logimport.clean(q) for q in qsos if q.get("call")]
        if not incoming:
            return say(f"No QSOs found in {source}.", False)
        new = logimport.new_qsos(self.logbook.qsos, incoming)
        had = len(incoming) - len(new)
        if not new:
            return say(f"✓ Nothing to add: all {len(incoming)} QSOs from {source} are already in your log.")
        now = utc_now()
        backup = ""
        if os.path.exists(self.logbook.path):
            backup = f"procyon_log-before-import-{now:%Y%m%d-%H%M%S}.adi"
            shutil.copy2(self.logbook.path, os.path.join(self.data_dir, backup))
        today = now.strftime("%Y%m%d")
        not_on_qrz = 0
        for q in new:
            # From QRZ (or a file exported from QRZ): QRZ has them already.
            if on_qrz or q.get("app_qrzlog_logid"):
                q.update(qrzcom_qso_upload_status="Y", qrzcom_qso_upload_date=today)
            elif not uploads.is_sent(q, "qrz"):
                not_on_qrz += 1
            q[uploads.EXPORTED_FIELD] = today    # they came from another program
        self.logbook.add_many(new, logimport.sort_key)
        for q in new:
            if q.get("gridsquare"):
                self.grids.setdefault(q["call"], q["gridsquare"][:4].upper())
                self._grids_dirty = True
        self._save_grids()
        log.info("imported %d QSOs from %s (%d already in the log), backup %s", len(new), source, had, backup)
        text = (f"✓ Added {len(new)} QSO{'s' if len(new) != 1 else ''} from {source}"
                + (f" ({had} {'was' if had == 1 else 'were'} already in your log)" if had else "") + ".")
        if not_on_qrz and self.s["qrz_key"]:
            text += (f" {not_on_qrz} of them aren't marked as on QRZ, so the Upload to QRZ button now counts "
                     f"them (QRZ skips any it already has).")
        if backup:
            text += f" Your log as it was before is saved as {backup}."
        return say(text)

    def _start_qrz_import(self) -> dict:
        if not self.s["qrz_key"]:
            return {"type": "log_import", "ok": False,
                    "text": "Set up QRZ first: paste your Logbook API key in Settings → Logbook."}
        if self.importing:
            return {"type": "log_import", "busy": True, "text": "The import is already running…"}
        if self.s["demo"]:
            return {"type": "log_import", "ok": False, "text": "Not in demo mode - switch the demo band off first."}
        self.importing = True
        asyncio.get_event_loop().create_task(self._qrz_import())
        return {"type": "log_import", "busy": True, "text": "Asking QRZ for your logbook…"}

    async def _qrz_import(self):
        def whole(total):
            # QRZ's COUNT turned out to be the batch size (250), not the whole
            # logbook as its documentation says - only trust a bigger number.
            return total if total and total > uploads.FETCH_PAGE else None

        def progress(n, total):   # n None: the whole logbook in one go
            total = whole(total)
            if n is None:
                text = f"Downloading {f'all {total}' if total else 'all your'} QSOs from QRZ in one go…"
            elif total and total >= n:
                text = f"Downloading from QRZ: {n} of {total} QSOs…"
            else:
                text = f"Downloading from QRZ: {n} QSOs so far…"
            asyncio.get_event_loop().create_task(self.broadcast({"type": "log_import", "busy": True, "text": text}))
        try:
            qsos, total = await uploads.qrz_fetch(self.s["qrz_key"], logimport.parse_adif, progress)
            log.info("QRZ import: QRZ reports %s QSOs, received %d", total, len(qsos))
            if qsos:
                reply = self.import_qsos(qsos, "QRZ", on_qrz=True)
                if whole(total) and len(qsos) < total and reply.get("ok"):
                    reply["text"] += (f" Note: QRZ says your logbook holds {total} QSOs, but only {len(qsos)} "
                                      f"came through - press Import again to fetch the rest.")
            elif total:   # QRZ said there were QSOs, but none could be read
                reply = {"type": "log_import", "ok": False,
                         "text": f"QRZ says your logbook holds {total} QSOs, but Procyon couldn't read them. "
                                 f"Your log wasn't changed - please report this."}
            else:
                reply = {"type": "log_import", "ok": True, "text": "Your QRZ logbook is empty - nothing to import."}
        except uploads.UploadError as e:
            reply = {"type": "log_import", "ok": False, "text": str(e)}
        except Exception:  # noqa: BLE001 - never leave the button stuck
            log.exception("QRZ import")
            reply = {"type": "log_import", "ok": False, "text": "The import failed unexpectedly - your log wasn't changed."}
        finally:
            self.importing = False
        await self.broadcast(reply)
        await self.push_state()

    # ------------------------------------------------------------- commands

    async def command(self, m: dict, local: bool = True):
        t = m.get("type")
        q = self.qso
        if t == "settings":
            old = dict(self.s)
            for k in DEFAULTS:
                if k in m["settings"] and not k.startswith(LOCAL_ONLY + OWN_COMMANDS):
                    self.s[k] = m["settings"][k]
            if local:   # only the computer running Procyon may change remote access and logbook keys
                self._remote_settings(m["settings"])
                self._logbook_settings(m["settings"])
            self.s["mycall"] = self.s["mycall"].upper().strip()
            self.s["mygrid"] = self.s["mygrid"].strip()[:6]
            self.s["mygrid"] = self.s["mygrid"][:4].upper() + self.s["mygrid"][4:].lower()
            if self.s["units"] not in ("auto", "km", "mi"):
                self.s["units"] = "auto"
            if self.s["clock_sync"] not in CLOCK_SYNC_MODES:
                self.s["clock_sync"] = "computer"
            if self.s["clock_sync"] == "computer":
                self._set_offset(0.0)
            self._clean_wf_range()
            self.save()
            self._set_station()
            if self.s["mycall"] and old["mycall"] != self.s["mycall"]:
                if not ft8.is_std_call(self.s["mycall"]):
                    self.toast(f"{self.s['mycall']} is longer than FT8's standard callsign format: "
                               f"it's sent in full when you call CQ (without your grid) and as "
                               f"<{self.s['mycall']}> during QSOs, as WSJT-X does", "warn")
            if old["remote_enabled"] != self.s["remote_enabled"] and self.on_remote_change:
                await self.on_remote_change(self.s["remote_enabled"])
            if (old["remote_pin_hash"] != self.s["remote_pin_hash"]
                    or (old["remote_enabled"] and not self.s["remote_enabled"])):
                await self._kick_remote_clients()
            if any(old[k] != self.s[k] for k in ("audio_in", "demo", "mycall")):
                if old["demo"] != self.s["demo"]:
                    self.logbook = self._open_logbook()
                    await self.halt()
                await self.restart_audio()
            if any(old[k] != self.s[k] for k in DEFAULTS if k.startswith("udp_") or k == "demo"):
                await self._configure_udp()
            if any(old[k] != self.s[k] for k in DEFAULTS if k.startswith("rig_") or k == "ptt"):
                if all(old[k] == self.s[k] for k in DEFAULTS
                       if (k.startswith("rig_") or k == "ptt") and k != "rig_mode"):
                    await self._apply_rig_mode()          # only the mode changed
                else:
                    await self.rig.configure(self.s)      # reconnects; mode is set on connect
                    await self.rig.poll()
                    if self.rig.connected:
                        await self._apply_rig_mode()
        elif t == "reply":
            d = m["decode"]
            d["p"] = ft8.parse(d["msg"], self.s["mycall"])
            q.reply(d)
            self.s["rx_freq"] = d["freq"]
        elif t == "cq":
            q.call_cq()
        elif t == "tx_enable":
            q.tx_enabled = bool(m["on"])
            q.auto_disabled = False
            if q.tx_enabled:
                q.last_progress = time.time()
        elif t == "halt":
            await self.halt()
        elif t == "clock_sync":
            self.sync_clock_now()
        elif t == "select_msg":
            q.next = int(m["n"])
        elif t == "edit_msg":
            q.msgs[int(m["n"])] = ft8.normalize(m["text"])
        elif t == "set_dx":
            q.set_dx(m.get("call", ""), m.get("grid", ""))
            q.next = 0 if q.dxcall else 5
        elif t == "clear_activity":
            self.history.clear()
            self.udp.clear()   # companion apps (GridTracker) clear their lists too
            await self.broadcast({"type": "history", "batches": []})
        elif t == "clear_exchange":
            q.thread.clear()
        elif t == "clear_dx":
            q.reset()
            q.regen()
            q.next = 5
        elif t == "parity":
            q.parity = int(m["p"]) % 2
        elif t == "freq":
            key = "tx_freq" if m.get("which") == "tx" else "rx_freq"
            lo, hi = self._audio_range()
            self.s[key] = int(max(lo, min(hi, m["hz"])))
            self.save()
        elif t == "mode":
            await self._set_mode(str(m.get("mode", "")).upper())
        elif t == "band":
            self.s["dial_hz"] = int(m["hz"])
            self.save()
            if self.rig.enabled:
                await self.rig.set_freq(int(m["hz"]))
                await self._apply_rig_mode()
        elif t == "qso_note":
            q.note = str(m.get("text", ""))[:200]
            i = self._logged_entry(q)
            if i is not None:   # already logged: update the entry too
                self.logbook.update(i, {"comment": self._comment(self._qso_comment(q))})
                self._pota_save()
        elif t == "tx_watts":
            w = str(m.get("w", "")).strip()
            self.s["tx_watts"] = w if w.replace(".", "", 1).isdigit() else ""
            self.save()
        elif t == "log_edit":
            i = int(m["idx"])
            if 0 <= i < len(self.logbook.qsos):
                w = str(m.get("tx_pwr", "")).strip()
                self.logbook.update(i, {
                    "comment": self._comment(str(m.get("comment", ""))[:200]),
                    "tx_pwr": w if w.replace(".", "", 1).isdigit() else "",
                })
                self._pota_save()
                self.toast("Log entry updated", "good")
        elif t == "log_delete":
            self._log_delete(m)
        elif t == "pota_start":
            return await self._pota_start(m)
        elif t == "pota_end":
            self._pota_end()
        elif t == "upload":
            self._start_upload(str(m.get("service", "")))
        elif t == "qrz_import":
            if not local:
                return {"type": "log_import", "ok": False,
                        "text": "That can only be done on the computer running Procyon."}
            return self._start_qrz_import()
        elif t == "qrz_check":
            if not local:
                self.toast("That can only be done on the computer running Procyon", "warn")
                return None
            return await self._qrz_check(str(m.get("key", "")).strip())
        elif t == "export_mark_all":
            if self.s["demo"]:
                return {"type": "export_marked", "text": "Not in demo mode - switch the demo band off first."}
            out = self._export_mark_all()
            await self.push_state()
            return out
        elif t == "tx_level":
            self.s["tx_level"] = max(0, min(100, int(m["v"])))
            self.save()
            return None   # no full status update needed while the slider is being dragged
        elif t == "tune":
            await self.tune(bool(m["on"]))
        elif t == "log_now":
            if q.dxcall:
                q.logged = False
                q._log()
        elif t == "devices":
            return {"type": "devices", "inputs": self.audio.inputs(),
                    "outputs": self.audio.outputs(), "serial": serial_ports(),
                    "rigs": hamlib_rigs()}
        elif t == "wsjtx_import":
            found, notes = import_wsjtx()
            return {"type": "wsjtx_import", "settings": found, "notes": notes}
        elif t in ("detect_radio", "rig_test", "install_wsjtx", "quit") and not local:
            self.toast("That can only be done on the computer running Procyon", "warn")
            return None
        elif t == "quit":
            # Same careful shutdown as Ctrl-C: transmitting stops, the radio is
            # released, GridTracker is told. Every open tab is told first.
            await self.broadcast({"type": "stopped"})
            if self.on_quit:
                self.on_quit()
            return None
        elif t == "detect_radio":
            found = await self._radio_job(detect_radios)
            if found is None:   # no way to probe serial ports here
                return {"type": "detect_radio", "found": [], "unsupported": True}
            return {"type": "detect_radio", "found": found, "help": "" if found else blocked_ports()}
        elif t == "rig_test":
            return await self._rig_test(m)
        elif t == "install_wsjtx":
            self._install_wsjtx()
            return None
        await self.push_state()
        return None

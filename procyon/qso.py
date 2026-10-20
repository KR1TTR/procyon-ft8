"""QSO auto-sequencer, following the standard WSJT-X FT8 exchange:

    Tx1  DX MY GRID      (answer a CQ)
    Tx2  DX MY -05       (signal report)
    Tx3  DX MY R-05      (roger + report)
    Tx4  DX MY RR73      (roger, best regards)
    Tx5  DX MY 73
    Tx6  CQ MY GRID
"""

import time

from . import ft8
from .geo import utc_now

WATCHDOG_SEC = 6 * 60
STAGES = ["Idle", "Calling", "Reports", "Roger", "Complete"]


class Qso:
    def __init__(self, on_log, on_toast):
        self.on_log = on_log
        self.on_toast = on_toast
        self.mycall = ""
        self.mygrid = ""
        self.reset()
        self.next = 5
        self.tx_enabled = False
        self.parity = 0
        self.msgs = [""] * 6
        self.last_tx_period = None

    def reset(self):
        self.dxcall = ""
        self.dxgrid = ""
        self.snr = -10           # what we measured of the DX station
        self.report_rcvd = ""
        self.stage = 0
        self.logged = False
        self.auto_disabled = False
        self.started = None
        self.last_progress = time.time()
        self.thread = []         # what we sent and heard this QSO, oldest first
        self.note = ""           # comment for the log entry
        self.log_idx = None      # logbook index once this QSO has been logged

    # ----------------------------------------------------------- messages

    def set_station(self, mycall, mygrid):
        self.mycall = (mycall or "").upper().strip()
        self.mygrid = (mygrid or "").upper().strip()
        self.regen()

    def regen(self):
        my, dx, g4 = self.mycall, self.dxcall, self.mygrid[:4]
        rpt = ft8.fmt_report(self.snr)
        cq = f"CQ {my} {g4}".strip() if my else ""
        if dx and my:
            self.msgs = [f"{dx} {my} {g4}".strip(), f"{dx} {my} {rpt}",
                         f"{dx} {my} R{rpt}", f"{dx} {my} RR73", f"{dx} {my} 73", cq]
        else:
            self.msgs = ["", "", "", "", "", cq]

    def snapshot(self):
        return {
            "dxcall": self.dxcall, "dxgrid": self.dxgrid, "msgs": self.msgs,
            "next": self.next, "tx_enabled": self.tx_enabled, "parity": self.parity,
            "stage": self.stage, "stage_name": STAGES[self.stage],
            "report_sent": ft8.fmt_report(self.snr) if self.dxcall else "",
            "report_rcvd": self.report_rcvd, "logged": self.logged,
            "thread": self.thread, "note": self.note,
        }

    # ------------------------------------------------------- exchange log

    def _note(self, kind, period, msg, snr=None, busy=False):
        """Add a sent ("tx") or heard ("rx") message to the exchange log,
        counting how many times in a row the same message has gone that way."""
        prev = next((e for e in reversed(self.thread) if e["dir"] == kind and "msg" in e), None)
        repeat = prev["repeat"] + 1 if prev and prev["msg"] == msg else 1
        self.thread.append({"dir": kind, "period": period, "msg": msg, "snr": snr,
                            "repeat": repeat, "busy": busy})
        del self.thread[:-30]

    def note_tx(self, period, msg):
        self._note("tx", period, msg)
        self.last_tx_period = period

    def _note_heard(self, decodes, period):
        """Log what the DX station sent this cycle - or that we heard nothing
        right after transmitting to them."""
        from_dx = [d for d in decodes if d["p"]["from"] == self.dxcall]
        for d in from_dx:
            to = d["p"]["to"]
            self._note("rx", period, d["msg"], d["snr"], busy=bool(to) and to != self.mycall)
        if not from_dx and self.last_tx_period == period - 1 and self.stage < 4:
            self.thread.append({"dir": "rx", "period": period, "silent": True})

    # ------------------------------------------------------- user actions

    def set_dx(self, call, grid="", snr=None):
        call = (call or "").upper().strip()
        if call != self.dxcall:
            self.reset()
        self.dxcall = call
        if grid:
            self.dxgrid = grid.upper()
        if snr is not None:
            self.snr = snr
        self.regen()

    def reply(self, d: dict):
        """Answer a decoded message (the equivalent of double-clicking it in WSJT-X)."""
        p = d["p"]
        call = p["from"]
        if not call or call == self.mycall:
            return
        self.set_dx(call, p.get("grid") or "", d["snr"])
        if d.get("period") is not None and not self.thread:
            self._note("rx", d["period"], d["msg"], d["snr"], busy=bool(p["to"]) and p["to"] != self.mycall)
        self.started = utc_now()
        self.parity = 1 - d["parity"]
        self.next = 0
        need_tx = self._advance(p) if p["to"] == self.mycall else True
        if self.stage == 0:
            self.stage = 1
        self.tx_enabled = need_tx
        self.auto_disabled = False
        self.last_progress = time.time()

    def call_cq(self):
        self.reset()
        self.regen()
        self.next = 5
        self.tx_enabled = True
        self.last_progress = time.time()

    # ------------------------------------------------------------ automation

    def on_decodes(self, decodes, period):
        if not self.mycall:
            return
        if self.dxcall:
            self._note_heard(decodes, period)
        mine = [d for d in decodes if d["p"]["kind"] == "std" and d["p"]["to"] == self.mycall]
        if not mine:
            return
        hits = [d for d in mine if d["p"]["from"] == self.dxcall] if self.dxcall else []
        if not hits and self.next == 5 and self.tx_enabled:
            # Someone answered our CQ - pick the strongest caller.
            hits = sorted(mine, key=lambda d: -d["snr"])
            d = hits[0]
            self.set_dx(d["p"]["from"], d["p"].get("grid") or "", d["snr"])
            if self.last_tx_period == period - 1:
                self._note("tx", self.last_tx_period, self.msgs[5])
            self._note_heard(decodes, period)
            self.started = utc_now()
            self.on_toast(f"{self.dxcall} answered your CQ", "good")
        if not hits:
            return
        d = hits[0]
        self.snr = d["snr"]
        if d["p"].get("grid"):
            self.dxgrid = d["p"]["grid"]
        self.regen()
        if self._advance(d["p"]) and self.auto_disabled:
            self.tx_enabled = True
            self.auto_disabled = False

    def _advance(self, p) -> bool:
        """Pick the next message from what the DX station just sent us.
        Returns True if we need to transmit something in reply."""
        k = p["extra_kind"]
        self.last_progress = time.time()
        if k in ("grid", "none", "other"):
            self.next, self.stage = 1, max(self.stage, 1)
        elif k == "report":
            self.report_rcvd = p["extra"]
            self.next, self.stage = 2, 2
        elif k in ("rreport", "rgrid"):
            if k == "rreport":
                self.report_rcvd = p["extra"][1:]
            self.next, self.stage = 3, 3
        elif k == "rr73":
            self.next, self.stage = 4, 4
            self._log()
        elif k == "73":
            if self.stage >= 3:
                self.stage = 4
                self._log()
                self.tx_enabled = False
                self.auto_disabled = False
            return False
        return True

    def after_tx(self, idx):
        if idx in (3, 4):  # RR73 or 73 sent: the QSO is complete
            self.stage = 4
            self._log()
            self.tx_enabled = False
            self.auto_disabled = True

    def watchdog(self):
        if self.tx_enabled and time.time() - self.last_progress > WATCHDOG_SEC:
            self.tx_enabled = False
            self.on_toast("Transmit stopped: no reply for 6 minutes", "warn")

    def _log(self):
        if self.logged or not self.dxcall:
            return
        self.logged = True
        self.on_log(self)

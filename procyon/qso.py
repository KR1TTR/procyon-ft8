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
        self.cq_tag = ""         # "POTA" during a park activation: "CQ POTA KR1TTR EN80"
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
        self.msgs = ["", "", "", "", "", self._cq(my, g4)]
        if not (dx and my):
            return
        # Long/unusual calls go as <CALL> (e.g. "<3E40CDW> KR1TTR EN80")
        D, M = ft8.msg_call(dx), ft8.msg_call(my)
        plain = [f"{D} {M} {g4}".strip(), f"{D} {M} {rpt}", f"{D} {M} R{rpt}",
                 f"{D} {M} RR73", f"{D} {M} 73"]
        if "/" not in my + dx and (ft8.is_std_call(my) or ft8.is_std_call(dx)):
            self.msgs[:5] = plain          # the everyday case: always fits
            return
        # Portable calls on both sides (P4/WE9V working KR1TTR/P or CT2JWF/QRP):
        # FT8 can't carry both in full with a grid or report. The usual way:
        # full calls when calling and at the end (no grid), reports with the
        # sender's base call - "<P4/WE9V> CT2JWF R-02". Each candidate is
        # checked with ft8code; the first that goes out intact is used.
        MB, H = ft8.msg_call(ft8.base_call(my)), f"<{dx}>"
        full = lambda tail: [f"{H} {my} {tail}".strip(), f"{dx} <{my}> {tail}".strip()]  # noqa: E731
        self.msgs[:5] = [
            ft8.first_fit([plain[0], *full(""), f"{D} {MB} {g4}".strip()]),
            ft8.first_fit([plain[1], f"{D} {MB} {rpt}", f"{H} {MB} {rpt}"]),
            ft8.first_fit([plain[2], f"{D} {MB} R{rpt}", f"{H} {MB} R{rpt}"]),
            ft8.first_fit([plain[3], *full("RR73"), f"{D} {MB} RR73"]),
            ft8.first_fit([plain[4], *full("73"), f"{D} {MB} 73"]),
        ]

    def _cq(self, my, g4):
        """CQ, or CQ POTA during an activation. A non-standard call is sent in
        full but then without a grid - and can't carry "POTA" at all."""
        if not my:
            return ""
        plain = f"CQ {my} {g4}".strip() if ft8.is_std_call(my) else f"CQ {my}"
        if not self.cq_tag:
            return plain
        return ft8.first_fit([f"CQ {self.cq_tag} {my} {g4}".strip(), plain])

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
        from_dx = [d for d in decodes if ft8.same_station(d["p"]["from"], self.dxcall)]
        for d in from_dx:
            to = d["p"]["to"]
            self._note("rx", period, d["msg"], d["snr"],
                       busy=bool(to) and not ft8.same_station(to, self.mycall))
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
        if not call or ft8.same_station(call, self.mycall):
            return
        self.set_dx(call, p.get("grid") or "", d["snr"])
        if d.get("period") is not None and not self.thread:
            self._note("rx", d["period"], d["msg"], d["snr"],
                       busy=bool(p["to"]) and not ft8.same_station(p["to"], self.mycall))
        self.started = utc_now()
        self.parity = 1 - d["parity"]
        self.next = 0
        need_tx = self._advance(p) if ft8.same_station(p["to"], self.mycall) else True
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
        mine = [d for d in decodes if d["p"]["kind"] == "std" and ft8.same_station(d["p"]["to"], self.mycall)]
        if not mine:
            return
        hits = [d for d in mine if ft8.same_station(d["p"]["from"], self.dxcall)] if self.dxcall else []
        sure = [d for d in mine if not d.get("uncertain")]
        if not hits and sure and self.next == 5 and self.tx_enabled:
            # Someone answered our CQ - pick the strongest caller. Never a
            # guessed decode: that may be noise, and we'd be calling a ghost.
            hits = sorted(sure, key=lambda d: -d["snr"])
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
        frm = d["p"]["from"]
        if "/" in frm and "/" not in self.dxcall and not frm.startswith("..."):
            self.dxcall = frm      # first heard by base call: log the full call
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
            if p.get("dxped"):
                # DXpedition style: QSO complete, and they ask for no 73 -
                # keeping quiet leaves the frequency clear for the next caller
                self.stage = 4
                self._log()
                self.tx_enabled = False
                self.auto_disabled = False
                return False
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

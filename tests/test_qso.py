"""The QSO sequencer (procyon/qso.py): which message goes out next, when a
QSO is logged, and which decodes it must NOT act on."""

import time

from _common import MYCALL, MYGRID, decode

from procyon import qso as Q


class Station:
    """A Qso with its log and notices captured, like the engine sets it up."""

    def __init__(self):
        self.logged, self.toasts = [], []
        self.q = Q.Qso(on_log=lambda q: self.logged.append((q.dxcall, q.sent_report(), q.report_rcvd)),
                       on_toast=lambda text, kind: self.toasts.append(text))
        self.q.set_station(MYCALL, MYGRID)


def test_answering_a_cq_sends_our_grid():
    s = Station()
    s.q.reply(decode("CQ W9XYZ EN37", snr=-7, parity=0))
    assert s.q.dxcall == "W9XYZ" and s.q.dxgrid == "EN37"
    assert s.q.next == 0 and s.q.tx_enabled
    assert s.q.parity == 1, "we transmit in the other half of the minute"
    assert s.q.msgs[0] == "W9XYZ K1ABC FN42"


def test_full_qso_when_we_answer_a_cq():
    s = Station()
    s.q.reply(decode("CQ W9XYZ EN37", snr=-7, parity=0))
    s.q.note_tx(102, s.q.msgs[0])
    s.q.on_decodes([decode("K1ABC W9XYZ -05", snr=-9, period=103)], 103)
    assert s.q.next == 2 and s.q.report_rcvd == "-05"
    assert s.q.msgs[2] == "W9XYZ K1ABC R-09"
    s.q.note_tx(104, s.q.msgs[2])
    s.q.on_decodes([decode("K1ABC W9XYZ RR73", period=105)], 105)
    assert s.logged == [("W9XYZ", "-09", "-05")]
    assert s.q.next == 4, "we still send a 73"
    s.q.after_tx(4)
    assert s.logged == [("W9XYZ", "-09", "-05")], "logged once, not twice"
    assert not s.q.tx_enabled


def test_full_qso_when_someone_answers_our_cq():
    s = Station()
    s.q.call_cq()
    s.q.last_tx_period = 100
    s.q.on_decodes([decode("K1ABC W9XYZ EN37", snr=-12, period=101)], 101)
    assert s.q.dxcall == "W9XYZ" and s.q.next == 1
    assert any("answered your CQ" in t for t in s.toasts)
    s.q.note_tx(102, s.q.msgs[1])
    s.q.on_decodes([decode("K1ABC W9XYZ R-08", period=103)], 103)
    assert s.q.next == 3 and s.q.report_rcvd == "-08"
    s.q.after_tx(3)
    assert s.logged == [("W9XYZ", "-12", "-08")]
    assert not s.q.tx_enabled


def test_the_strongest_caller_is_picked():
    s = Station()
    s.q.call_cq()
    s.q.on_decodes([decode("K1ABC W9XYZ EN37", snr=-15), decode("K1ABC N0DEF EM48", snr=-3)], 101)
    assert s.q.dxcall == "N0DEF"


def test_a_guessed_caller_is_never_answered():
    """0.9.19's "ghost callers": a decoder guess (a2 = "MYCALL ??? ???")
    may be noise. Calling it would be calling a station that isn't there."""
    s = Station()
    s.q.call_cq()
    s.q.on_decodes([decode("K1ABC 6Y1GWX OP85", snr=-20, uncertain="a2")], 101)
    assert s.q.dxcall == "" and s.q.next == 5


def test_a_guessed_repeat_never_moves_the_qso_on():
    """A k3 guess only says "the same as last time": it mustn't log a QSO."""
    s = Station()
    s.q.reply(decode("CQ W9XYZ EN37", parity=0))
    s.q.on_decodes([decode("K1ABC W9XYZ -05")], 103)
    s.q.note_tx(104, s.q.msgs[2])
    s.q.on_decodes([decode("K1ABC W9XYZ RR73", uncertain="k3")], 105)
    assert s.logged == [] and s.q.next == 2
    s.q.on_decodes([decode("K1ABC W9XYZ RR73")], 107)
    assert len(s.logged) == 1


def test_the_report_logged_is_the_one_sent():
    """0.9.38: messages are rebuilt with the latest signal strength, but the
    log must show what the other station was actually told."""
    s = Station()
    s.q.reply(decode("CQ W9XYZ EN37", snr=-14, parity=0))
    s.q.on_decodes([decode("K1ABC W9XYZ -05", snr=-14)], 103)
    s.q.note_tx(104, s.q.msgs[2])                       # "W9XYZ K1ABC R-14"
    s.q.on_decodes([decode("K1ABC W9XYZ RR73", snr=-12)], 105)
    assert s.logged == [("W9XYZ", "-14", "-05")]


def test_dxpedition_rr73_logs_without_a_73():
    """0.9.12: "A RR73; B <DX> -06" style. Their RR73 to us completes the QSO,
    and we stay quiet so the next caller has the frequency."""
    s = Station()
    s.q.reply(decode("CQ W9XYZ EN37", parity=0))
    s.q.on_decodes([decode("K1ABC W9XYZ -05")], 103)
    s.q.note_tx(104, s.q.msgs[2])
    s.q.on_decodes([decode("K1ABC RR73; N0AAA <W9XYZ> -10")], 105)
    assert len(s.logged) == 1
    assert not s.q.tx_enabled


def test_a_long_callsign_goes_in_brackets():
    """0.9.10: written plainly, a 7-character call fell back to free text and
    got cut off on the air ("3E40CDW KR1TT")."""
    s = Station()
    s.q.set_dx("3E40CDW")
    assert s.q.msgs[0] == "<3E40CDW> K1ABC FN42"


def test_portable_calls_on_both_sides_still_fit():
    """0.9.17: KR1TTR/P working P4/WE9V - every message must go out intact."""
    from procyon import ft8
    s = Station()
    s.q.set_station("K1ABC/P", MYGRID)
    s.q.set_dx("P4/WE9V")
    assert all(ft8.fits(m) for m in s.q.msgs[:5]), s.q.msgs
    assert ft8.same_station("WE9V", "P4/WE9V")


def test_cq_pota():
    s = Station()
    s.q.cq_tag = "POTA"
    s.q.regen()
    assert s.q.msgs[5] == "CQ POTA K1ABC FN42"


def test_the_watchdog_stops_transmitting():
    s = Station()
    s.q.call_cq()
    s.q.last_progress = time.time() - Q.WATCHDOG_SEC - 1
    s.q.watchdog()
    assert not s.q.tx_enabled
    assert any("6 minutes" in t for t in s.toasts)

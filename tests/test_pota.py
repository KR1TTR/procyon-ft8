"""POTA activations: park references, which QSOs belong to a park, the
10-QSO count, file names, and keeping the demo band's practice activation
away from real operating."""

import asyncio
import json
import os

from _common import scratch_dir

from procyon import pota
from procyon.engine import Engine


def test_park_references():
    good, bad = pota.parse_parks("us-1234, US-5678 us-1234; K-0001 VE-0123 nonsense 12-34")
    assert good == ["US-1234", "US-5678", "K-0001", "VE-0123"]
    assert bad == ["NONSENSE", "12-34"]


def test_which_qsos_belong_to_a_park():
    def q(call, t, parks="US-1234", sig="POTA"):
        return {"call": call, "qso_date": "20261112", "time_on": t, "band": "20m",
                "my_sig": sig, "my_sig_info": parks}
    log = [q("A1AA", "120000"), q("B1BB", "130000", "US-1234,US-5678"),
           q("C1CC", "130500", "US-5678"), q("D1DD", "131000", sig=""), q("E1EE", "150000")]
    got = pota.activation_qsos(log, "US-1234", "20261112125900", "20261112140000")
    assert [x["call"] for x in got] == ["B1BB"]


def test_the_ten_qso_count_ignores_dupes():
    day = [{"call": c, "band": b, "qso_date": "20261112"} for c, b in
           [("A1AA", "20m"), ("a1aa", "20m"), ("A1AA", "40m"), ("B1BB", "20m")]]
    assert pota.count_on(day, "20261112") == 3


def test_park_file_names():
    assert pota.filename("K1ABC/P", "US-1234", "20261112") == "K1ABC_P@US-1234-20261112.adi"


def test_merge_keeps_earlier_activations_and_drops_deleted_qsos():
    a = {"call": "A1AA", "qso_date": "20261112", "time_on": "090000", "band": "20m"}
    b = {"call": "B1BB", "qso_date": "20261112", "time_on": "180000", "band": "20m"}
    c = {"call": "C1CC", "qso_date": "20261112", "time_on": "181000", "band": "20m"}
    assert [x["call"] for x in pota.merge([a, b], [c], drop=[b])] == ["A1AA", "C1CC"]


# ------------------------------------- the demo band's practice activation

async def _noop(*a, **k):
    return None


def _engine(folder, demo):
    e = Engine(folder, force_demo=demo)
    for name in ("restart_audio", "_configure_udp", "halt", "push_state", "_kick_remote_clients"):
        setattr(e, name, _noop)
    e.toast = lambda *a, **k: None
    return e


def test_a_practice_activation_never_reaches_real_operating():
    """0.9.69d5 bug: a POTA activation started on the demo band was still
    on after a normal start, and a real QSO got tagged with the practice park."""
    folder = scratch_dir("pota")
    with open(os.path.join(folder, "settings.json"), "w") as f:
        json.dump({"mycall": "K1ABC", "mygrid": "FN42", "rig_enabled": False}, f)

    def saved():
        s = json.load(open(os.path.join(folder, "settings.json")))
        return s["pota_active"], s["pota_parks"], s["pota_demo"]["pota_active"], s["pota_demo"]["pota_parks"]

    async def run():
        e = _engine(folder, demo=True)
        r = await e._pota_start({"parks": "US-1234", "grid": "FN42", "state": "MA"})
        assert r["ok"] and e.s["pota_active"]
        assert saved() == (False, [], True, ["US-1234"]), "saved as practice only"

        e = _engine(folder, demo=False)                       # next start: real operating
        assert not e.s["pota_active"], "no activation on a normal start"
        await e._pota_start({"parks": "K-0001", "grid": "FN42"})
        await e.command({"type": "settings", "settings": {"demo": True}})
        assert e.s["pota_parks"] == ["US-1234"], "demo switched on: the practice park"
        e._pota_end()
        assert saved() == (True, ["K-0001"], False, ["US-1234"]), "ending practice leaves the real one"
        await e.command({"type": "settings", "settings": {"demo": False}})
        assert e.s["pota_active"] and e.s["pota_parks"] == ["K-0001"]

    asyncio.run(run())

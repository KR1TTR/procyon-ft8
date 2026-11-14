"""The engine's rules around a QSO: changing band ends it, and demo QSOs
stay in their own practice log."""

import asyncio
import json
import os

from _common import scratch_dir

from procyon.engine import Engine


async def _noop(*a, **k):
    return None


def _engine(demo=False):
    folder = scratch_dir("engine")
    with open(os.path.join(folder, "settings.json"), "w") as f:
        json.dump({"mycall": "K1ABC", "mygrid": "FN42", "rig_enabled": False, "dial_hz": 14_074_000}, f)
    e = Engine(folder, force_demo=demo)
    for name in ("restart_audio", "_configure_udp", "push_state", "_kick_remote_clients"):
        setattr(e, name, _noop)
    e.toast = lambda *a, **k: None
    return e, folder


def test_changing_band_ends_the_qso():
    """0.9.65: the station on the old band can't hear us on the new one."""
    async def run():
        e, _ = _engine()
        e.qso.set_dx("W9XYZ")
        e.qso.tx_enabled = True
        await e._new_band("40m")
        assert e.qso.dxcall == "" and not e.qso.tx_enabled and e.qso.next == 5
    asyncio.run(run())


def test_moving_within_a_band_keeps_the_qso():
    """FT8 and FT4 dials on the same band are not a band change."""
    async def run():
        e, _ = _engine()
        e.qso.set_dx("W9XYZ")
        await e._new_band("20m")
        assert e.qso.dxcall == "W9XYZ"
    asyncio.run(run())


def test_the_demo_band_is_off_at_every_start():
    """0.9.23: a saved "demo on" never carries over - only the DEMO launcher turns it on."""
    e, folder = _engine()
    e.s["demo"] = True
    e.save()
    assert Engine(folder).s["demo"] is False


def test_demo_qsos_go_to_the_practice_log():
    e, folder = _engine(demo=True)
    assert e.logbook.path.endswith("procyon_demo_log.adi")
    e2, _ = _engine(demo=False)
    assert e2.logbook.path.endswith("procyon_log.adi")

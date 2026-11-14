"""The logbook: reading and writing ADIF, editing, deleting and importing."""

import os

from _common import scratch_dir

from procyon import geo, logimport


def qso(call, date="20261112", time_on="210000", band="20m", **more):
    return {"call": call, "qso_date": date, "time_on": time_on, "band": band, "mode": "FT8", **more}


def test_adif_round_trip_keeps_every_field():
    """Values are read by their length, so a note with < or > survives (0.9.41)."""
    path = os.path.join(scratch_dir("adif"), "log.adi")
    book = geo.Logbook(path)
    book.add(qso("W9XYZ", comment="nice <signal> & good op", gridsquare="EN37"))
    book.add(qso("N0DEF", time_on="211500"))
    again = geo.Logbook(path)
    assert [q["call"] for q in again.qsos] == ["W9XYZ", "N0DEF"]
    assert again.qsos[0]["comment"] == "nice <signal> & good op"
    assert again.qsos[0]["gridsquare"] == "EN37"


def test_edit_and_delete_keep_a_backup():
    path = os.path.join(scratch_dir("edit"), "log.adi")
    book = geo.Logbook(path)
    for c in ("W9XYZ", "N0DEF", "K2GHI"):
        book.add(qso(c))
    book.update(0, {"comment": "edited", "gridsquare": ""})
    gone = book.remove(1)
    assert gone["call"] == "N0DEF"
    assert os.path.exists(path + ".bak"), "a rewrite must back the old file up first"
    again = geo.Logbook(path)
    assert [q["call"] for q in again.qsos] == ["W9XYZ", "K2GHI"]
    assert again.qsos[0]["comment"] == "edited"


def test_worked_before():
    book = geo.Logbook(os.path.join(scratch_dir("worked"), "log.adi"))
    book.add(qso("W9XYZ", band="40m"))
    assert book.worked("w9xyz") and book.worked("W9XYZ", "40m")
    assert not book.worked("W9XYZ", "20m") and not book.worked("N0DEF")


def test_import_skips_qsos_already_in_the_log():
    """Importing the same logbook twice adds nothing (0.9.41); the same call
    and band within 15 minutes counts as the same QSO."""
    have = [qso("W9XYZ", time_on="210000")]
    incoming = [logimport.clean(q) for q in (
        qso("w9xyz", time_on="210800"),               # same QSO, logged 8 minutes later elsewhere
        qso("W9XYZ", time_on="223000"),               # a second QSO later on
        qso("W9XYZ", time_on="210000", band="40m"),   # another band
        qso("N0DEF"), qso("N0DEF"),                   # twice in the file itself
    )]
    new = logimport.new_qsos(have, incoming)
    assert [(q["call"], q["time_on"], q["band"]) for q in new] == [
        ("W9XYZ", "223000", "20m"), ("W9XYZ", "210000", "40m"), ("N0DEF", "210000", "20m")]


def test_import_tidies_qsos():
    q = logimport.clean({"call": "w9xyz", "gridsquare": "en37AB", "freq": "14.074", "qso_date": "20261112"})
    assert q["call"] == "W9XYZ" and q["gridsquare"] == "EN37ab" and q["band"] == "20m"


def test_bands():
    assert geo.band_of(14_074_000) == "20m" and geo.band_of(7_047_500) == "40m"
    assert geo.band_of(28_074_000) == "10m"

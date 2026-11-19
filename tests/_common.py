"""Shared setup for Procyon's tests.

Every test runs on this computer alone: no radio, no sound card, no
internet and no WSJT-X needed. Data files go into throw-away folders.
"""

import logging
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

# The decoder normally shares its work between helper processes; one
# process is enough for the short test signals and starts instantly.
os.environ.setdefault("PROCYON_DECODE_WORKERS", "0")

from procyon import ft8  # noqa: E402

# Don't ask WSJT-X's ft8code even if it happens to be installed: the tests
# check Procyon's own packer, and must give the same answer on every computer.
ft8.CROSS_CHECK = False

# No sound card: the engine gets a stand-in that knows no devices, so the
# tests never look at this computer's audio (and run on machines with no
# sound system at all, like GitHub's).
class NoSound:
    name = "none"

    def guess(self, kind):
        return ""

    def inputs(self):
        return []

    def outputs(self):
        return []

    def resolve(self, name, kind):
        from procyon.audio import match_device  # noqa: PLC0415
        return match_device(name, [])


from procyon import engine as _engine  # noqa: E402

_engine.choose_backend = NoSound

# Keep the output to PASS/FAIL lines (the engine logs what it does).
logging.disable(logging.INFO)

MYCALL, MYGRID = "K1ABC", "FN42"


def decode(msg, snr=-10, parity=1, period=101, mycall=MYCALL, uncertain=""):
    """A decoded message, shaped the way the engine hands it to the sequencer."""
    d = {"msg": msg, "snr": snr, "dt": 0.1, "freq": 1500, "parity": parity,
         "period": period, "p": ft8.parse(msg, mycall)}
    if uncertain:
        d["uncertain"] = uncertain
    return d


def scratch_dir(name):
    """A fresh empty folder for one test's files."""
    return tempfile.mkdtemp(prefix=f"procyon-test-{name}-")

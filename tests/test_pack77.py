"""Checks Procyon's own message packer (procyon/pack77.py) against WSJT-X.

pack77_cases.json holds thousands of messages with the 77 bits and the
read-back text that WSJT-X's ft8code gave for each (make_pack77_cases.py
made it). Every one must match exactly. No WSJT-X needed to run this:

    python3 tests/test_pack77.py        (or: python3 -m pytest tests)
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from procyon import pack77  # noqa: E402


def check_all():
    with open(os.path.join(HERE, "pack77_cases.json")) as f:
        cases = json.load(f)
    wrong = []
    for c in cases:
        try:
            bits, text = pack77.encode(c["msg"])
        except pack77.BadMessage:
            bits, text = None, None
        if c["type"] == "bad":                 # WSJT-X can't send it: we mustn't either
            ok = bits is None or text != " ".join(c["msg"].upper().split())
        else:
            ok = bits == c["bits"] and text == c["decoded"]
        if not ok:
            wrong.append((c, bits, text))
    return cases, wrong


def test_matches_ft8code():
    cases, wrong = check_all()
    assert not wrong, f"{len(wrong)} of {len(cases)} differ, first: {wrong[0]}"


def test_unknown_hash_reads_as_dots():
    bits = pack77.pack77("K1ABC <PJ4/W9XYZ> -11")
    assert pack77.unpack77(bits) == "K1ABC <...> -11"


if __name__ == "__main__":
    cases, wrong = check_all()
    for c, bits, text in wrong[:10]:
        print(f"{c['msg']!r}\n  ft8code {c['bits']} {c['decoded']!r}\n  ours    {bits} {text!r}")
    test_unknown_hash_reads_as_dots()
    print(f"{len(cases) - len(wrong)} of {len(cases)} match ft8code")
    sys.exit(1 if wrong else 0)

"""Make the reference cases for Procyon's own FT8/FT4 message packer.

Runs WSJT-X's ft8code (the referee) on thousands of messages and saves what
it says - the 77 bits, how a receiver reads the message back, and the
message type - in pack77_cases.json. tests/test_pack77.py then checks
Procyon's packer against that file, so the tests run without WSJT-X.

    python3 tests/make_pack77_cases.py          (needs ft8code on the PATH)

The random messages use a fixed seed, so the file comes out the same each run.
"""

import json
import os
import random
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "pack77_cases.json")
R = random.Random(77)

L = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
D = "0123456789"
FREE = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ+-./?"


def pick(s, n=1):
    return "".join(R.choice(s) for _ in range(n))


def std_call():
    """A standard-format call: 1-2 char prefix, a digit, 1-3 letters."""
    shape = R.random()
    if shape < 0.15:
        pre = pick(L)                      # K1ABC
    elif shape < 0.55:
        pre = pick(L, 2)                   # KA1ABC
    elif shape < 0.7:
        pre = pick(L) + pick(D)            # E51ABC / 4X... style is digit first below
    elif shape < 0.85:
        pre = pick(D) + pick(L)            # 4X1ABC, 3D2AB
    else:
        pre = pick(L)
    return pre + pick(D) + pick(L, R.randint(1, 3))


def odd_call():
    """Calls that don't fit the standard format."""
    kinds = [
        lambda: std_call() + "/" + R.choice(["P", "R", "QRP", "M", "MM", "AM", "1", "7", "VE3", "KH6", "A"]),
        lambda: R.choice(["PJ4", "KH6", "VP2E", "F", "DL", "VE3", "KP4", "3D2", "W1"]) + "/" + std_call(),
        lambda: pick(L, 2) + pick(D, 2) + pick(L, R.randint(2, 4)),          # YW18FIFA-ish
        lambda: pick(D) + pick(L) + pick(D, 2) + pick(L, 3),                 # 3E40CDW
        lambda: pick(L) + pick(D) + pick(L, 4),                              # K1ABCD (4-letter suffix)
        lambda: pick(L, 3) + pick(D) + pick(L, 2),                           # 3-letter prefix
        lambda: "3DA0" + pick(L, 2),                                         # Eswatini work-around
        lambda: "3X" + pick(L) + pick(D) + pick(L, 2),                       # Guinea work-around
        lambda: pick(L) + pick(D) * 1,                                       # too short: K1
        lambda: pick(D, 2) + pick(L, 3),                                     # all-digit prefix
    ]
    return R.choice(kinds)()


def grid():
    return pick("ABCDEFGHIJKLMNOPQR", 2) + pick(D, 2)


def grid6():
    return grid() + pick("ABCDEFGHIJKLMNOPQRSTUVWX", 2)


def report():
    return f"{R.randint(-30, 30):+03d}"


def gen():
    msgs = []
    add = msgs.append
    tails = ["", "RRR", "RR73", "73"]
    # Standard messages and their variations.
    for _ in range(900):
        a, b = std_call(), std_call()
        t = R.random()
        if t < 0.2:
            add(f"{a} {b} {grid()}")
        elif t < 0.4:
            add(f"{a} {b} {report()}")
        elif t < 0.55:
            add(f"{a} {b} R{report()}")
        elif t < 0.65:
            add(f"{a} {b} R {grid()}")
        elif t < 0.8:
            add(f"{a} {b} {R.choice(tails[1:])}")
        else:
            add(f"{a} {b}")
    # CQ, QRZ, DE in all their forms.
    for _ in range(300):
        c = std_call()
        word = R.choice(["CQ", "QRZ", "DE", "CQ DX", "CQ POTA", "CQ NA", "CQ EU", "CQ TEST", "CQ FD",
                         "CQ SOTA", "CQ " + pick(L, R.randint(1, 4)), "CQ " + pick(D, 3), "CQ " + pick(D, 2),
                         "CQ " + pick(L, 5), "CQ " + pick(D, 4)])
        add(f"{word} {c} {grid()}" if R.random() < 0.8 else f"{word} {c}")
    # Portable /P and /R calls in standard messages.
    for _ in range(200):
        a, b = std_call(), std_call()
        sfx = R.choice(["/P", "/R"])
        if R.random() < 0.5:
            a += sfx
        else:
            b += sfx
        if R.random() < 0.2:
            a, b = a + "/R", b + "/P"
        add(f"{a} {b} {R.choice([grid(), report(), 'R' + report(), 'RRR', 'RR73', '73', 'R ' + grid()])}")
    # Hashed calls inside standard messages, and the non-standard (type 4) kind.
    for _ in range(400):
        a, o = std_call(), odd_call()
        t = R.random()
        if t < 0.2:
            add(f"{a} <{o}> {R.choice([report(), 'R' + report(), 'RRR', 'RR73', '73', grid()])}")
        elif t < 0.4:
            add(f"<{o}> {a} {R.choice([report(), 'R' + report(), 'RRR', 'RR73', '73', grid()])}")
        elif t < 0.55:
            add(f"<{a}> {o}" + R.choice(["", " RRR", " RR73", " 73"]))
        elif t < 0.7:
            add(f"{o} <{a}>" + R.choice(["", " RRR", " RR73", " 73"]))
        elif t < 0.8:
            add(f"CQ {o}")
        elif t < 0.9:
            add(f"{a} {o} {R.choice([report(), grid(), 'RR73'])}")      # nothing bracketed
        else:
            add(f"<{a}> <{o}>")
    # DXpedition (Fox) messages.
    for _ in range(150):
        r = R.randrange(-30, 33, 2) if R.random() < 0.8 else R.randint(-35, 35)
        dx = R.choice([odd_call(), std_call()])
        add(f"{std_call()} RR73; {std_call()} <{dx}> {r:+03d}")
    # ARRL Field Day, RTTY Roundup, EU VHF contest, telemetry.
    secs = "AB AK AL AR AZ BC CO CT DE EB EMA ENY EPA EWA GA GH IA ID IL IN KS KY LA LAX NS MB MDC ME MI MN MO MS MT NC ND NE NFL NH NL NLI NM NNJ NNY TER NTX NV OH OK ONE ONN ONS OR ORG PAC PR QC RI SB SC SCV SD SDG SF SFL SJV SK SNJ STX SV TN UT VA VI VT WCF WI WMA WNY WPA WTX WV WWA WY DX PE NB".split()
    states = "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY NB NS QC ON MB SK AB BC NWT NF LB NU YT PEI DC DR FR GD GR OV ZH ZL".split()
    for _ in range(150):
        cls = f"{R.randint(1, 32)}{pick('ABCDEF')}"
        add(f"{std_call()} {std_call()} {'R ' if R.random() < 0.4 else ''}{cls} {R.choice(secs)}")
    for _ in range(150):
        rst = f"5{R.randint(2, 9)}9"
        ex = R.choice(states) if R.random() < 0.6 else f"{R.randint(1, 7999):04d}"
        tu = "TU; " if R.random() < 0.3 else ""
        add(f"{tu}{std_call()} {std_call()} {'R ' if R.random() < 0.4 else ''}{rst} {ex}")
    for _ in range(100):
        a, b = std_call() + R.choice(["", "/P"]), std_call() + R.choice(["", "/P"])
        add(f"<{a}> <{b}> {'R ' if R.random() < 0.4 else ''}5{R.randint(2, 9)}{R.randint(1, 2047):04d} {grid6()}")
    for _ in range(100):
        n = R.randint(1, 18)
        add(pick("01234567") + pick("0123456789ABCDEF", n - 1))
    # Free text: short, long (cut to 13), odd characters.
    for _ in range(400):
        n = R.randint(1, 20)
        add(" ".join(pick(FREE.strip(), R.randint(1, 6)) for _ in range(max(1, n // 4)))[:20])
    add("TNX BOB 73 GL")
    add("HELLO WORLD")
    add("GL ES 73")
    add("A B C D E F")
    add("K1ABC")
    add("K1ABC W9XYZ RR73 GL")
    add("K1ABC W9XYZ AA00")
    add("K1ABC W9XYZ RR99")
    add("K1ABC W9XYZ -51")
    add("K1ABC W9XYZ +50")
    add("K1ABC W9XYZ -50")
    add("K1ABC W9XYZ +49")
    add("K1ABC W9XYZ R+49")
    return list(dict.fromkeys(m for m in msgs if m.strip()))   # drop duplicates, keep order


def ask(msg):
    out = subprocess.run(["ft8code", msg], capture_output=True, text=True, timeout=10).stdout.splitlines()
    row = next((x for x in out if re.match(r"^\s*1\.\s", x)), None)
    bits = None
    for i, x in enumerate(out):
        if x.startswith("Source-encoded message") and i + 1 < len(out):
            bits = out[i + 1].strip()
    if row is None or bits is None or len(bits) != 77:
        return None
    rest = row[79:].split()
    err = bool(rest) and rest[0] == "*"     # ft8code's mark: decoded text differs from the message
    if err:
        rest = rest[1:]
    kind = rest[0].rstrip(".") if rest else ""
    return {"msg": msg, "bits": bits, "decoded": row[42:79].strip(),
            "type": "bad" if kind == "*" else kind, "err": err}


def main():
    cases = []
    for m in gen():
        r = ask(m)
        if r:
            cases.append(r)
        else:
            print("ft8code gave no answer for", repr(m), file=sys.stderr)
    with open(OUT, "w") as f:
        json.dump(cases, f, indent=0)
    types = {}
    for c in cases:
        types[c["type"]] = types.get(c["type"], 0) + 1
    print(len(cases), "cases ->", OUT)
    print(dict(sorted(types.items())))


if __name__ == "__main__":
    main()

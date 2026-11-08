"""FT8/FT4 message packing: text <-> the 77 message bits. Procyon's own.

Every FT8 and FT4 transmission carries 77 bits. This module turns a message
like "CQ K1ABC FN42" into those bits (pack77) and back into text
(unpack77), following the 77-bit message formats of WSJT-X (packjt77 by
Joe Taylor K1JT and the WSJT Development Group, GPLv3), so Procyon's
signals are read by every FT8/FT4 program. It was written to match WSJT-X's
ft8code bit for bit; tests/test_pack77.py checks it against thousands of
messages packed by ft8code.

Message types (i3.n3):
  0.0 free text (13 characters)     0.1 DXpedition "A RR73; B <DX> -08"
  0.3/0.4 ARRL Field Day            0.5 telemetry (18 hex digits)
  1   standard (calls, grid/report) 2   standard with /P calls (EU VHF)
  3   ARRL RTTY Roundup             4   one non-standard call + one hashed
  5   EU VHF contest (two hashed calls, report, serial, 6-character grid)

Hashed calls: long or odd calls travel as a 10/12/22-bit hash written <CALL>.
A receiver can only show the call if it has seen it in full before; the
caller passes a Hashes table for that (unpack77 shows <...> otherwise).
"""

import re

C38 = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ/"        # non-standard calls, hashing
C42 = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ+-./?"    # free text
A1 = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"           # standard call, character 1
A2 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"            # character 2
A3 = "0123456789"                                      # character 3: the call-area digit
A4 = " ABCDEFGHIJKLMNOPQRSTUVWXYZ"                     # characters 4-6 (suffix letters)
NTOKENS = 2063592          # 28-bit values below this are DE, QRZ, CQ and the CQ_xxx forms
MAX22 = 4194304            # then come the 22-bit hashes, then standard calls
MAXGRID4 = 32400           # 15-bit grid field: above this are reports, RRR, RR73, 73

# ARRL Field Day sections and RTTY Roundup states/provinces, in WSJT-X's order
# (their position is what gets sent).
SECTIONS = ("AB AK AL AR AZ BC CO CT DE EB EMA ENY EPA EWA GA GH IA ID IL IN KS KY LA LAX NS MB "
            "MDC ME MI MN MO MS MT NC ND NE NFL NH NL NLI NM NNJ NNY TER NTX NV OH OK ONE ONN ONS "
            "OR ORG PAC PR QC RI SB SC SCV SD SDG SF SFL SJV SK SNJ STX SV TN UT VA VI VT WCF WI "
            "WMA WNY WPA WTX WV WWA WY DX PE NB").split()
MULTS = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH "
         "NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY NB NS QC ON MB SK AB BC "
         "NWT NF LB NU YT PEI DC DR FR GD GR OV ZH ZL").split() + [f"X{i:02d}" for i in range(1, 100)]


class Hashes:
    """Callsigns heard in full, by their 10-, 12- and 22-bit hashes, so a
    hashed <CALL> can be shown by name. Packing a message adds its calls."""

    def __init__(self):
        self.h10, self.h12, self.h22 = {}, {}, {}

    def add(self, call: str):
        call = call.strip().strip("<>").upper()
        if len(call) < 3 or call.startswith("...") or not all(c in C38 for c in call):
            return
        self.h10[ihashcall(call, 10)] = call
        self.h12[ihashcall(call, 12)] = call
        self.h22[ihashcall(call, 22)] = call

    def name(self, table: dict, n: int) -> str:
        return f"<{table[n]}>" if n in table else "<...>"


def ihashcall(call: str, m: int) -> int:
    """WSJT-X's m-bit hash of a callsign (up to 11 characters)."""
    n8 = 0
    for ch in (call.upper() + " " * 11)[:11]:
        n8 = 38 * n8 + max(C38.find(ch), 0)
    return ((47055833459 * n8) & 0xFFFFFFFFFFFFFFFF) >> (64 - m)


def _letter(c):
    return "A" <= c <= "Z"


def _digit(c):
    return "0" <= c <= "9"


def is_grid4(w: str) -> bool:
    return len(w) == 4 and "A" <= w[0] <= "R" and "A" <= w[1] <= "R" and _digit(w[2]) and _digit(w[3])


def is_grid6(w: str) -> bool:
    return (len(w) == 6 and is_grid4(w[:4]) and "A" <= w[4] <= "X" and "A" <= w[5] <= "X")


def chkcall(w: str):
    """(ok, base call): could `w` be a standard call, or a compound one
    (prefix/call or call/suffix) built on a standard call?"""
    n = len(w)
    bc = w[:6]
    if n > 11 or any(c in w for c in ".+-?") or (n > 6 and "/" not in w):
        return False, bc
    i0 = w.find("/") + 1                       # 1-based like WSJT-X; 0 = none
    if max(i0 - 1, n - i0) > 6:
        return False, bc
    if 2 <= i0 <= n - 1:
        bc = w[i0:] if i0 - 1 <= n - i0 else w[:i0 - 1]
    nbc = len(bc)
    if nbc > 6:
        return False, bc
    b = bc + "   "
    if not _letter(b[0]) and not _letter(b[1]):
        return False, bc
    if b[0] == "Q":                            # calls don't start with Q
        return False, bc
    i1 = 0
    if _digit(b[1]):
        i1 = 2
    if _digit(b[2]):
        i1 = 3
    if i1 == 0 or i1 == nbc:
        return False, bc
    suffix = bc[i1:]
    if not suffix or len(suffix) > 3 or not all(_letter(c) for c in suffix):
        return False, bc
    return True, bc


# --------------------------------------------------------------- 28-bit calls

def pack28(word: str, hashes: Hashes | None = None) -> int:
    """A callsign or token (DE, QRZ, CQ, CQ_xxx, <hashed>) as 28 bits; -1 if impossible."""
    c = word
    if hashes is not None and not c.startswith(("CQ", "DE", "QRZ")):
        hashes.add(c)
    if c == "DE":
        return 0
    if c == "QRZ":
        return 1
    if c == "CQ":
        return 2
    if c.startswith("CQ_"):
        tail = c[3:]
        if len(tail) == 3 and tail.isdigit():
            return 3 + int(tail)
        if 1 <= len(tail) <= 4 and all(_letter(ch) for ch in tail):
            m = 0
            for ch in tail.rjust(4):
                m = 27 * m + A4.index(ch)
            return 3 + 1000 + m
        return -1
    if c.startswith("<"):
        inner = c[1:c.index(">")] if ">" in c else c[1:]
        return NTOKENS + ihashcall(inner, 22)
    # Work-arounds WSJT-X uses so these prefixes fit the standard format.
    if c.startswith("3DA0") and len(c) > 4:
        c = "3D0" + c[4:]
    elif c.startswith("3X") and len(c) > 2 and _letter(c[2]):
        c = "Q" + c[2:]
    n = len(c)
    iarea = 1
    for i in range(n, 1, -1):                  # last digit, 1-based
        if _digit(c[i - 1]):
            iarea = i
            break
    npdig = sum(_digit(ch) for ch in c[:iarea - 1])
    nplet = sum(_letter(ch) for ch in c[:iarea - 1])
    nslet = sum(_letter(ch) for ch in c[iarea:])
    if iarea < 2 or iarea > 3 or nplet == 0 or npdig >= iarea - 1 or nslet > 3:
        return NTOKENS + ihashcall(c, 22)      # not standard: send its hash
    cs = (" " + c[:5]) if iarea == 2 else c[:6]
    cs = cs.ljust(6)
    try:
        i1, i2, i3 = A1.index(cs[0]), A2.index(cs[1]), A3.index(cs[2])
        i4, i5, i6 = A4.index(cs[3]), A4.index(cs[4]), A4.index(cs[5])
    except ValueError:
        return NTOKENS + ihashcall(c, 22)
    n28 = ((((i1 * 36 + i2) * 10 + i3) * 27 + i4) * 27 + i5) * 27 + i6
    return n28 + NTOKENS + MAX22


def unpack28(n28: int, hashes: Hashes | None = None) -> str:
    if n28 < NTOKENS:
        if n28 <= 2:
            return ("DE", "QRZ", "CQ")[n28]
        if n28 <= 1002:
            return f"CQ_{n28 - 3:03d}"
        m = n28 - 1003
        s = ""
        for _ in range(4):
            s = A4[m % 27] + s
            m //= 27
        return "CQ_" + s.strip()
    n = n28 - NTOKENS
    if n < MAX22:
        h = hashes if hashes is not None else Hashes()
        return h.name(h.h22, n)
    n -= MAX22
    i6 = n % 27; n //= 27
    i5 = n % 27; n //= 27
    i4 = n % 27; n //= 27
    i3 = n % 10; n //= 10
    i2 = n % 36; n //= 36
    i1 = n
    if i1 >= len(A1):
        return ""
    call = (A1[i1] + A2[i2] + A3[i3] + A4[i4] + A4[i5] + A4[i6]).strip()
    if call.startswith("3D0") and len(call) > 3:
        call = "3DA0" + call[3:]
    elif call.startswith("Q") and len(call) > 1 and _letter(call[1]):
        call = "3X" + call[1:]
    return call


# ---------------------------------------------------------------- bit helpers

def _bits(value: int, width: int) -> str:
    return format(value, f"0{width}b")


def _split(msg: str):
    """Words of a message, with "CQ xxx CALL" joined as "CQ_xxx"."""
    w = msg.split()
    if len(w) >= 3 and w[0] == "CQ" and chkcall(w[2])[0] and len(w[1]) <= 4 and not chkcall(w[1])[0]:
        w = ["CQ_" + w[1]] + w[2:]
    return w


# ------------------------------------------------------------------ the types

def _pack_text(msg: str) -> str | None:
    t = msg[:13].rstrip()
    if not all(ch in C42 for ch in t):
        return None
    n = 0
    for ch in t.rjust(13):
        n = 42 * n + C42.index(ch)
    return _bits(n, 71) + "000" + "000"


class BadMessage(ValueError):
    """WSJT-X would call this message "*** bad message ***": it can't be sent."""


def _pack_dxped(w, h) -> str | None:
    """'A RR73; B <DX> -08': the Fox's two-in-one message. The report must be
    even, -30 to +32; WSJT-X calls anything else a bad message (or, when the
    first call has a slash, falls back to free text)."""
    if len(w) != 5 or w[1] != "RR73;" or not chkcall(w[0])[0]:
        return None
    ok = (w[3].startswith("<") and w[3].endswith(">") and len(w[3]) > 2
          and chkcall(w[0])[0] and chkcall(w[2])[0] and re.fullmatch(r"[+-]\d+", w[4]) is not None)
    if ok:
        irpt = int(w[4])
        ok = irpt % 2 == 0 and -30 <= irpt <= 32
    if not ok:
        if "/" in w[0]:
            return None
        raise BadMessage(f"can't encode {' '.join(w)!r}")
    n28a, n28b = pack28(w[0], h), pack28(w[2], h)
    h.add(w[3][1:-1])                  # last, as WSJT-X does: wins a hash clash
    return (_bits(n28a, 28) + _bits(n28b, 28) + _bits(ihashcall(w[3][1:-1], 10), 10)
            + _bits((irpt + 30) // 2, 5) + "001" + "000")


def _pack_fd(w, h) -> str | None:
    if not 4 <= len(w) <= 5:
        return None
    if not chkcall(w[0])[0] or not chkcall(w[1])[0]:
        return None
    if w[-1] not in SECTIONS:
        return None
    if len(w) == 5 and w[2] != "R":
        return None
    cls = w[-2]
    if not re.fullmatch(r"\d{1,2}[A-Z]", cls):
        return None
    ntx = int(cls[:-1])
    if not 1 <= ntx <= 32:
        return None
    nclass = ord(cls[-1]) - ord("A")
    if nclass > 7:
        return None
    n3, intx = 3, ntx - 1
    if intx >= 16:
        n3, intx = 4, ntx - 17
    ir = 1 if len(w) == 5 else 0
    isec = SECTIONS.index(w[-1]) + 1
    return (_bits(pack28(w[0], h), 28) + _bits(pack28(w[1], h), 28) + str(ir) + _bits(intx, 4)
            + _bits(nclass, 3) + _bits(isec, 7) + _bits(n3, 3) + "000")


def _report(word: str):
    """'-11' / 'R+05' -> (ir, dB); None if not a report."""
    m = re.fullmatch(r"(R?)([+-]\d+)", word)
    if not m:
        return None
    return (1 if m.group(1) else 0), int(m.group(2))


def _pack_std(w, h) -> str | None:
    nw = len(w)
    if nw < 2 or nw > 4:
        return None
    ok1, _ = chkcall(w[0])
    ok2, _ = chkcall(w[1])
    if w[0] in ("DE", "CQ", "QRZ") or w[0].startswith("CQ_"):
        ok1 = True
    if w[0].startswith("<") and w[0].find(">") >= 4:
        ok1 = True
    if w[1].startswith("<") and w[1].find(">") >= 4:
        ok2 = True
    if not ok1 or not ok2:
        return None
    if w[0].startswith("<") and "/" in w[1]:
        return None
    if w[1].startswith("<") and "/" in w[0]:
        return None
    # Compound calls (PJ4/K1ABC, K1ABC/1) don't fit here, only /P and /R.
    for c in w[:2]:
        if "/" in c and not c.startswith("<") and not c.endswith(("/P", "/R")):
            return None
    last = w[-1]
    ir = 0
    if nw == 2:
        igrid4 = MAXGRID4 + 1
    else:
        if nw == 4 and w[2] != "R":
            return None
        rep = _report(last)
        if is_grid4(last):                     # RR73 too: it goes out as that grid square
            ir = 1 if nw == 4 else 0
            igrid4 = ((ord(last[0]) - 65) * 18 + (ord(last[1]) - 65)) * 100 + int(last[2:4])
        elif nw == 4:
            return None
        elif last == "RRR":
            igrid4 = MAXGRID4 + 2
        elif last == "RR73":
            igrid4 = MAXGRID4 + 3
        elif last == "73":
            igrid4 = MAXGRID4 + 4
        elif rep:
            ir, db = rep
            if not -50 <= db <= 50:
                return None
            irpt = db + 35
            if irpt < 5:               # -31 ... -50 wrap round to the top of the range
                irpt += 101
            igrid4 = MAXGRID4 + irpt
        else:
            return None
    p1 = (w[0] + " ").find("/P ") >= 3
    p2 = (w[1] + " ").find("/P ") >= 3
    r1 = (w[0] + " ").find("/R ") >= 3
    r2 = (w[1] + " ").find("/R ") >= 3
    if (p1 or p2) and (r1 or r2):
        return None
    i3 = 2 if (p1 or p2) else 1
    c1 = w[0][:-2] if (p1 or r1) else w[0]
    c2 = w[1][:-2] if (p2 or r2) else w[1]
    n28a, n28b = pack28(c1, h), pack28(c2, h)
    if n28a < 0 or n28b < 0:
        return None
    return (_bits(n28a, 28) + str(int(p1 or r1)) + _bits(n28b, 28) + str(int(p2 or r2)) + str(ir)
            + _bits(igrid4, 15) + _bits(i3, 3))


def _pack_telemetry(w) -> str | None:
    if len(w) != 1 or not re.fullmatch(r"[0-9A-F]{1,18}", w[0]):
        return None
    t = w[0].rjust(18, "0")
    a, b, c = int(t[0:6], 16), int(t[6:12], 16), int(t[12:18], 16)
    if a >= 1 << 23:
        return None
    return _bits(a, 23) + _bits(b, 24) + _bits(c, 24) + "101" + "000"


def _pack_rtty(w, h) -> str | None:
    if not 4 <= len(w) <= 6:
        return None
    i1 = 1 if w[0] == "TU;" else 0
    if len(w) <= i1 + 1:
        return None
    if not chkcall(w[i1])[0] or not chkcall(w[i1 + 1])[0]:
        return None
    crpt = w[-2][:3]
    if "-" in crpt or "+" in crpt:
        return None
    nserial = 0
    if len(crpt) == 3 and crpt[0] == "5" and "2" <= crpt[1] <= "9" and crpt[2] == "9":
        if w[-1].isdigit():
            nserial = int(w[-1])
    imult = MULTS.index(w[-1]) + 1 if w[-1] in MULTS else -1
    if imult < 0 and nserial <= 0:
        return None
    nexch = 8000 + imult if imult > 0 else nserial
    if not re.fullmatch(r"\d+", w[-2]):
        return None
    irpt = (int(w[-2]) - 509) // 10 - 2
    irpt = max(0, min(7, irpt))
    ir = 1 if len(w) > i1 + 3 and w[i1 + 2] == "R" else 0
    return (str(i1) + _bits(pack28(w[i1], h), 28) + _bits(pack28(w[i1 + 1], h), 28) + str(ir)
            + _bits(irpt, 3) + _bits(nexch, 13) + "011")


def _pack_nonstd(w, h) -> str | None:
    if len(w) not in (2, 3):
        return None
    a, b = w[0], w[1]
    ha = a.startswith("<") and a.endswith(">")
    hb = b.startswith("<") and b.endswith(">")
    ca, cb = a.strip("<>"), b.strip("<>")
    icq = 0
    if a == "CQ":
        if len(w) != 2 or len(cb) < 5:
            return None
        icq, iflip, n12, c11 = 1, 0, ihashcall(cb, 12), cb
    elif ha and not hb:
        iflip, n12, c11 = 0, ihashcall(ca, 12), cb
    elif hb and not ha:
        iflip, n12, c11 = 1, ihashcall(cb, 12), ca
    else:
        return None
    if not c11 or len(c11) > 11 or not all(ch in C38 for ch in c11):
        return None
    nrpt = 0
    if len(w) == 3:
        nrpt = {"RRR": 1, "RR73": 2, "73": 3}.get(w[2], -1)
        if nrpt < 0:
            return None
    if h is not None:
        h.add(ca)
        h.add(cb)
    n58 = 0
    for ch in c11.rjust(11):
        n58 = 38 * n58 + C38.index(ch)
    return _bits(n12, 12) + _bits(n58, 58) + str(iflip) + _bits(nrpt, 2) + str(icq) + "100"


def _pack_euvhf(w, h) -> str | None:
    if not 4 <= len(w) <= 5:
        return None
    if not (w[0].startswith("<") and w[1].startswith("<")):
        return None
    ex, g = w[-2], w[-1]
    if len(ex) < 6 or not ex[1:2].isdigit() or not is_grid6(g):
        return None
    nx = int(ex[1])
    if not 2 <= nx <= 9 or not ex[2:7].isdigit():
        return None
    c1, c2 = w[0].strip("<>"), w[1].strip("<>")
    h.add(c1)
    h.add(c2)
    ir = 1 if len(w) == 5 and w[2] == "R" else 0
    iserial = int(ex[2:7])
    if iserial > 2047:
        iserial = 2047
    igrid6 = (((((ord(g[0]) - 65) * 18 + (ord(g[1]) - 65)) * 10 + int(g[2])) * 10 + int(g[3])) * 24
              + (ord(g[4]) - 65)) * 24 + (ord(g[5]) - 65)
    return (_bits(ihashcall(c1, 12), 12) + _bits(ihashcall(c2, 22), 22) + str(ir) + _bits(nx - 2, 3)
            + _bits(iserial, 11) + _bits(igrid6, 25) + "101")


def pack77(msg: str, hashes: Hashes | None = None) -> str:
    """The 77 bits (as a '0'/'1' string) for a message, chosen the way
    WSJT-X chooses: DXpedition, Field Day, standard, telemetry, RTTY,
    non-standard call, EU VHF, and free text last (cut to 13 characters)."""
    h = hashes if hashes is not None else Hashes()
    msg = " ".join(msg.upper().split())
    w = _split(msg)
    if not w:
        raise BadMessage("empty message")
    if not msg.startswith(("CQ ", "DE ", "QRZ ")):
        r = _pack_dxped(w, h)
        # WSJT-X messages are at most 37 characters; a DXpedition message whose
        # read-back (with <...> added around hashed calls) is longer isn't sent so.
        if r and len(unpack77(r, h)) <= 37:
            return r
        r = _pack_fd(w, h)
        if r:
            return r
    r = _pack_std(w, h)
    if r:
        return r
    for f in (lambda w, h: _pack_telemetry(w), _pack_rtty, _pack_nonstd, _pack_euvhf):
        r = f(w, h)
        if r:
            return r
    r = _pack_text(msg)
    if r is None:
        raise BadMessage(f"can't encode {msg!r}")
    return r


# ------------------------------------------------------------------ unpacking

def _fmt_db(db: int) -> str:
    return f"{db:+03d}"


def unpack77(bits: str, hashes: Hashes | None = None) -> str:
    """The text of a 77-bit message ('0'/'1' string). Hashed calls not in
    `hashes` show as <...>. Returns '' for bits that make no valid message."""
    h = hashes if hashes is not None else Hashes()
    i3 = int(bits[74:77], 2)
    n3 = int(bits[71:74], 2)
    v = lambda a, b: int(bits[a:b], 2)   # noqa: E731
    if i3 == 0 and n3 == 0:
        n = v(0, 71)
        s = ""
        for _ in range(13):
            s = C42[n % 42] + s
            n //= 42
        return s.strip()
    if i3 == 0 and n3 == 1:
        c1, c2 = unpack28(v(0, 28), h), unpack28(v(28, 56), h)
        dx = h.name(h.h10, v(56, 66))
        return f"{c1} RR73; {c2} {dx} {_fmt_db(2 * v(66, 71) - 30)}"
    if i3 == 0 and n3 in (3, 4):
        c1, c2 = unpack28(v(0, 28), h), unpack28(v(28, 56), h)
        ir, intx, ncls, isec = v(56, 57), v(57, 61), v(61, 64), v(64, 71)
        ntx = intx + 1 + (16 if n3 == 4 else 0)
        if not 1 <= isec <= len(SECTIONS):
            return ""
        return f"{c1} {c2} {'R ' if ir else ''}{ntx}{chr(65 + ncls)} {SECTIONS[isec - 1]}"
    if i3 == 0 and n3 == 5:
        return f"{v(0, 23):06X}{v(23, 47):06X}{v(47, 71):06X}"
    if i3 in (1, 2):
        c1, c2 = unpack28(v(0, 28), h), unpack28(v(29, 57), h)
        if not c1 or not c2:
            return ""
        sfx = "/R" if i3 == 1 else "/P"
        if v(28, 29) and not c1.startswith("<"):
            c1 += sfx
        if v(57, 58) and not c2.startswith("<"):
            c2 += sfx
        ir, igrid4 = v(58, 59), v(59, 74)
        c1 = c1.replace("CQ_", "CQ ")
        if igrid4 <= MAXGRID4:
            n = igrid4
            g = chr(65 + n // 1800) + chr(65 + (n // 100) % 18) + f"{n % 100:02d}"
            return f"{c1} {c2} {'R ' if ir else ''}{g}"
        irpt = igrid4 - MAXGRID4
        tail = {1: "", 2: "RRR", 3: "RR73", 4: "73"}.get(irpt)
        if tail is None:
            db = irpt - 35
            if db > 50:
                db -= 101
            tail = ("R" if ir else "") + _fmt_db(db)
        return f"{c1} {c2} {tail}".strip()
    if i3 == 3:
        itu = v(0, 1)
        c1, c2 = unpack28(v(1, 29), h), unpack28(v(29, 57), h)
        ir, irpt, nexch = v(57, 58), v(58, 61), v(61, 74)
        rst = f"5{irpt + 2}9"
        ex = MULTS[nexch - 8001] if nexch > 8000 and nexch - 8001 < len(MULTS) else f"{nexch:04d}"
        return f"{'TU; ' if itu else ''}{c1} {c2} {'R ' if ir else ''}{rst} {ex}"
    if i3 == 4:
        n12, n58, iflip, nrpt, icq = v(0, 12), v(12, 70), v(70, 71), v(71, 73), v(73, 74)
        s = ""
        for _ in range(11):
            s = C38[n58 % 38] + s
            n58 //= 38
        call = s.strip()
        h.add(call)
        if icq:
            return f"CQ {call}"
        other = h.name(h.h12, n12)
        a, b = (other, call) if iflip == 0 else (call, other)
        tail = {1: " RRR", 2: " RR73", 3: " 73"}.get(nrpt, "")
        return f"{a} {b}{tail}"
    if i3 == 5:
        n12, n22, ir, irpt, iserial, igrid6 = v(0, 12), v(12, 34), v(34, 35), v(35, 38), v(38, 49), v(49, 74)
        n = igrid6
        g6 = chr(65 + n % 24); n //= 24
        g5 = chr(65 + n % 24); n //= 24
        d4 = n % 10; n //= 10
        d3 = n % 10; n //= 10
        g2 = chr(65 + n % 18); n //= 18
        g1 = chr(65 + n)
        return (f"{h.name(h.h12, n12)} {h.name(h.h22, n22)} {'R ' if ir else ''}"
                f"5{irpt + 2}{iserial:04d} {g1}{g2}{d3}{d4}{g5}{g6}")
    return ""


def encode(msg: str):
    """(77 bits, text a receiver will read): pack, then read back exactly as
    a receiver that has heard every call in the message would."""
    h = Hashes()
    bits = pack77(msg, h)
    return bits, unpack77(bits, h)

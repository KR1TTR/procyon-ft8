"""JS8 message packing: typed text <-> 12-character frames (+ 3 flag bits).

Procyon's own port of JS8Call 2.2.0's varicode.cpp, jsc.cpp and the frame
unpacking in decodedtext.cpp (Jordan Sherer KN4CRD and contributors, GPLv3).
It follows the original closely - including its quirks - so what we send and
what we read match JS8Call exactly. Comments name the original functions.

    frames = build_frames("KR1TTR", "EN80", "N8GHI SNR?")   # [(frame, flags), ...]
    decode_frame(frame, flags)                               # {"type": ..., "text": ...}

A frame is 72 bits written as 12 characters of js8enc.ALPHABET; the 3 flag
bits say whether it's the first / last frame of a message, and (at the faster
speeds) whether it's a data frame. js8enc turns a frame + flags into audio.

Frame kinds (the first 3 bits of the 72):
  000 heartbeat / CQ        - callsign (up to 11 chars) + grid
  001 compound              - a long or /P-style callsign + grid
  010 compound directed     - a long callsign + a command (SNR?, ACK ...)
  011 directed              - from + to callsign + a command: "KR1TTR: N8GHI SNR?"
  1xx data (Normal speed)   - text, Huffman or word-list compressed
  data flag (other speeds)  - text, word-list compressed, all 72 bits
"""
import gzip
import json
import os
import re

# ---------------------------------------------------------------- tables

ALPHABET41 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ+-./?"          # alphabet (checksums)
ALPHABET72 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-+/?."
ALNUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ /@"                 # alphanumeric (calls, grids)

# Directed commands -> number. Order doesn't matter here: lookups by number use
# the first key in sorted order, like Qt's QMap::key().
DIRECTED_CMDS = {
    " HEARTBEAT": -1, " HB": -1, " CQ": -1,
    " SNR?": 0, "?": 0,
    " DIT DIT": 1,
    " HEARING?": 3,
    " GRID?": 4,
    ">": 5,
    " STATUS?": 6,
    " STATUS": 7,
    " HEARING": 8,
    " MSG": 9,
    " MSG TO:": 10,
    " QUERY": 11,
    " QUERY MSGS": 12, " QUERY MSGS?": 12,
    " QUERY CALL": 13,
    " GRID": 15,
    " INFO?": 16,
    " INFO": 17,
    " FB": 18,
    " HW CPY?": 19,
    " SK": 20,
    " RR": 21,
    " QSL?": 22,
    " QSL": 23,
    " CMD": 24,
    " SNR": 25,
    " NO": 26,
    " YES": 27,
    " 73": 28,
    " NACK": 2,
    " ACK": 14,
    " HEARTBEAT SNR": 29,
    " AGN?": 30,
    "  ": 31, " ": 31,
}
ALLOWED_CMDS = set(range(-1, 32))
AUTOREPLY_CMDS = {0, 2, 3, 4, 6, 9, 10, 11, 12, 13, 14, 16, 30}
BUFFERED_CMDS = {5, 9, 10, 11, 12, 13, 15, 24}
SNR_CMDS = {25, 29}
CHECKSUM_CMDS = {5: 16, 9: 16, 10: 16, 11: 16, 12: 16, 13: 16, 15: 0, 24: 16}


def _cmd_key(value):
    """QMap::key(value): the first key (in sorted order) with this value."""
    keys = sorted(k for k, v in DIRECTED_CMDS.items() if v == value)
    return keys[0] if keys else ""


HUFF = {
    " ": "01", "E": "100", "T": "1101", "A": "0011", "O": "11111", "I": "11100",
    "N": "10111", "S": "10100", "H": "00011", "R": "00000", "D": "111011",
    "L": "110011", "C": "110001", "U": "101101", "M": "101011", "W": "001011",
    "F": "001001", "G": "000101", "Y": "000011", "P": "1111011", "B": "1111001",
    ".": "1110100", "V": "1100101", "K": "1100100", "-": "1100001", "+": "1100000",
    "?": "1011001", "!": "1011000", '"': "1010101", "X": "1010100", "0": "0010101",
    "J": "0010100", "1": "0010001", "Q": "0010000", "2": "0001001", "Z": "0001000",
    "3": "0000101", "5": "0000100", "4": "11110101", "9": "11110100", "8": "11110001",
    "6": "11110000", "7": "11101011", "/": "11101010",
}

NBASECALL = 37 * 36 * 10 * 27 * 27 * 27
NBASEGRID = 180 * 180
NUSERGRID = NBASEGRID + 10
NMAXGRID = (1 << 15) - 1

_GROUPS = [
    "<....>", "@ALLCALL", "@JS8NET",
    "@DX/NA", "@DX/SA", "@DX/EU", "@DX/AS", "@DX/AF", "@DX/OC", "@DX/AN",
    "@REGION/1", "@REGION/2", "@REGION/3",
    "@GROUP/0", "@GROUP/1", "@GROUP/2", "@GROUP/3", "@GROUP/4",
    "@GROUP/5", "@GROUP/6", "@GROUP/7", "@GROUP/8", "@GROUP/9",
    "@COMMAND", "@CONTROL", "@NET", "@NTS",
    "@RESERVE/0", "@RESERVE/1", "@RESERVE/2", "@RESERVE/3", "@RESERVE/4",
    "@APRSIS", "@RAGCHEW", "@JS8", "@EMCOMM", "@ARES", "@MARS", "@AMRRON",
    "@RACES", "@RAYNET", "@RADAR", "@SKYWARN", "@CQ", "@HB", "@QSO",
    "@QSOPARTY", "@CONTEST", "@FIELDDAY", "@SOTA", "@IOTA", "@POTA", "@QRP", "@QRO",
]
BASECALLS = {name: NBASECALL + 1 + i for i, name in enumerate(_GROUPS)}
_BASECALL_BY_VALUE = {v: k for k, v in BASECALLS.items()}

CQS = {0: "CQ CQ CQ", 1: "CQ DX", 2: "CQ QRP", 3: "CQ CONTEST", 4: "CQ FIELD", 5: "CQ FD", 6: "CQ CQ", 7: "CQ"}
HBS = {i: "HB" for i in range(8)}   # status flags deprecated in 2.2: all "HB"

# frame types and flag bits
FRAME_HEARTBEAT, FRAME_COMPOUND, FRAME_COMPOUND_DIRECTED, FRAME_DIRECTED = 0, 1, 2, 3
FRAME_DATA, FRAME_DATA_COMPRESSED = 4, 6
FLAG_NONE, FLAG_FIRST, FLAG_LAST, FLAG_DATA = 0, 1, 2, 4

# submodes, as JS8Call numbers them
SUBMODES = {"normal": 0, "fast": 1, "turbo": 2, "slow": 4}

# ---------------------------------------------------------------- regular expressions
# (Qt's (?<name>...) is (?P<name>...) in Python; QRegularExpression::match
# finds the first match anywhere, like re.search.)

GRID_PATTERN = r"(?P<grid>[A-X]{2}[0-9]{2}(?:[A-X]{2}(?:[0-9]{2})?)*)+"
BASE_CALLSIGN_PATTERN = (r"(?P<callsign>\b(?P<base>([0-9A-Z])?([0-9A-Z])([0-9])([A-Z])?([A-Z])?([A-Z])?)"
                         r"(?P<portable>[/][P])?\b)")
COMPOUND_CALLSIGN_PATTERN = (r"(?P<callsign>(?:[@]?|\b)(?P<extended>[A-Z0-9\/@][A-Z0-9\/]{0,2}[\/]?"
                             r"[A-Z0-9\/]{0,3}[\/]?[A-Z0-9\/]{0,3})\b)")
PACK_CALLSIGN_PATTERN = r"([0-9A-Z ])([0-9A-Z])([0-9])([A-Z ])([A-Z ])([A-Z ])"

CALLSIGN_PATTERN = r"(?P<callsign>[@]?[A-Z0-9/]+)"
OPTIONAL_CMD_PATTERN = (r"(?P<cmd>\s?(?:AGN[?]|QSL[?]|HW CPY[?]|MSG TO[:]|SNR[?]|INFO[?]|GRID[?]|STATUS[?]|"
                        r"QUERY MSGS[?]|HEARING[?]|(?:(?:STATUS|HEARING|QUERY CALL|QUERY MSGS|QUERY|CMD|MSG|"
                        r"NACK|ACK|73|YES|NO|HEARTBEAT SNR|SNR|QSL|RR|SK|FB|INFO|GRID|DIT DIT)(?=[ ]|$))|[?> ]))?")
OPTIONAL_GRID_PATTERN = r"(?P<grid>\s?[A-R]{2}[0-9]{2})?"
OPTIONAL_NUM_PATTERN = r"(?P<num>(?<=SNR)\s?[-+]?(?:3[01]|[0-2]?[0-9]))?"

DIRECTED_RE = re.compile("^" + CALLSIGN_PATTERN + OPTIONAL_CMD_PATTERN + OPTIONAL_NUM_PATTERN)
HEARTBEAT_RE = re.compile(r"^\s*(?P<callsign>[@](?:ALLCALL|HB)\s+)?(?P<type>CQ CQ CQ|CQ DX|CQ QRP|CQ CONTEST|"
                          r"CQ FIELD|CQ FD|CQ CQ|CQ|HB|HEARTBEAT(?!\s+SNR))(?:\s(?P<grid>[A-R]{2}[0-9]{2}))?\b")
COMPOUND_RE = re.compile(r"^\s*[`]" + CALLSIGN_PATTERN + "(?P<extra>" + OPTIONAL_GRID_PATTERN
                         + OPTIONAL_CMD_PATTERN + OPTIONAL_NUM_PATTERN + ")")
_CALL_LIKE = re.compile(r"[0-9][A-Z]|[A-Z][0-9]")


def _cap(m, name):
    """QRegularExpressionMatch::captured(name): '' if the group didn't take part."""
    v = m.group(name)
    return v if v is not None else ""


# ---------------------------------------------------------------- bits and numbers

def int_to_bits(value, expected=0):
    """Varicode::intToBits: big-endian bits, padded to `expected` (never cut)."""
    bits = [int(b) for b in bin(value)[2:]] if value else []
    return [0] * (expected - len(bits)) + bits if len(bits) < expected else bits


def bits_to_int(bits):
    v = 0
    for b in bits:
        v = ((v << 1) + int(b)) & 0xFFFFFFFFFFFFFFFF   # quint64
    return v


def pack72(value64, rem8):
    """Varicode::pack72bits: 64 bits + 8 bits -> 12 characters."""
    n = ((value64 & 0xFFFFFFFFFFFFFFFF) << 8) | (rem8 & 0xFF)
    return "".join(ALPHABET72[(n >> (6 * (11 - i))) & 63] for i in range(12))


def unpack72(text):
    """Varicode::unpack72bits: 12 characters -> (64-bit value, 8-bit rest)."""
    n = 0
    for c in text[:12]:
        n = (n << 6) | ALPHABET72.index(c)
    return n >> 8, n & 0xFF


def pack16bits(packed):
    a = packed // (41 * 41)
    b = (packed - a * 41 * 41) // 41
    return ALPHABET41[a] + ALPHABET41[b] + ALPHABET41[packed % 41]


def pack32bits(packed):
    return pack16bits((packed >> 16) & 0xFFFF) + pack16bits(packed & 0xFFFF)


def _crc16_kermit(data: bytes):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc


def _crc32_bzip2(data: bytes):
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
    return crc ^ 0xFFFFFFFF


def checksum16(text):
    c = pack16bits(_crc16_kermit(text.encode("utf-8")))
    return c + " " * (3 - len(c))


def checksum32(text):
    c = pack32bits(_crc32_bzip2(text.encode("utf-8")))
    return c + " " * (6 - len(c))


# ---------------------------------------------------------------- callsigns and grids

def pack_alnum50(value):
    """Varicode::packAlphaNumeric50: up to 11 characters (calls like KN4CRD/QRP, @RACES)."""
    word = re.sub(r"[^A-Z0-9 /@]", "", value)
    if len(word) > 3 and word[3] != "/":
        word = word[:3] + " " + word[3:]
    if len(word) > 7 and word[7] != "/":
        word = word[:7] + " " + word[7:]
    word = word + " " * (11 - len(word)) if len(word) < 11 else word
    a = ALNUM.index
    return (38 * 38 * 38 * 2 * 38 * 38 * 38 * 2 * 38 * 38 * a(word[0])
            + 38 * 38 * 38 * 2 * 38 * 38 * 38 * 2 * 38 * a(word[1])
            + 38 * 38 * 38 * 2 * 38 * 38 * 38 * 2 * a(word[2])
            + 38 * 38 * 38 * 2 * 38 * 38 * 38 * int(word[3] == "/")
            + 38 * 38 * 38 * 2 * 38 * 38 * a(word[4])
            + 38 * 38 * 38 * 2 * 38 * a(word[5])
            + 38 * 38 * 38 * 2 * a(word[6])
            + 38 * 38 * 38 * int(word[7] == "/")
            + 38 * 38 * a(word[8])
            + 38 * a(word[9])
            + a(word[10]))


def unpack_alnum50(packed):
    w = [""] * 11
    for i, base in ((10, 38), (9, 38), (8, 38), (7, 2), (6, 38), (5, 38), (4, 38), (3, 2), (2, 38), (1, 38), (0, 39)):
        t = packed % base
        packed //= base
        w[i] = ("/" if t else " ") if base == 2 else ALNUM[t]
    return "".join(w).replace(" ", "")


def pack_callsign(value):
    """Varicode::packCallsign: a standard call (or group) -> 28 bits. Returns (packed, portable)."""
    callsign = value.upper().strip()
    if callsign in BASECALLS:
        return BASECALLS[callsign], False
    portable = False
    if callsign.endswith("/P"):
        callsign = callsign[:-2]
        portable = True
    if callsign.startswith("3DA0"):                       # Swaziland
        callsign = "3D0" + callsign[4:]
    if callsign.startswith("3X") and len(callsign) > 2 and "A" <= callsign[2] <= "Z":   # Guinea
        callsign = "Q" + callsign[2:]
    slen = len(callsign)
    if slen < 2 or slen > 6:
        return 0, portable
    perms = [callsign]
    if slen == 2:
        perms.append(" " + callsign + "   ")
    if slen == 3:
        perms += [" " + callsign + "  ", callsign + "   "]
    if slen == 4:
        perms += [" " + callsign + " ", callsign + "  "]
    if slen == 5:
        perms += [" " + callsign, callsign + " "]
    matched = ""
    for p in perms:   # the LAST permutation that matches wins (no break in the original)
        m = re.search(PACK_CALLSIGN_PATTERN, p)
        if m:
            matched = m.group(0)
    if len(matched) < 6:
        return 0, portable
    a = ALNUM.index
    packed = a(matched[0])
    packed = 36 * packed + a(matched[1])
    packed = 10 * packed + a(matched[2])
    packed = 27 * packed + a(matched[3]) - 10
    packed = 27 * packed + a(matched[4]) - 10
    packed = 27 * packed + a(matched[5]) - 10
    return packed, portable


def unpack_callsign(value, portable):
    if value in _BASECALL_BY_VALUE:
        return _BASECALL_BY_VALUE[value]
    w = [""] * 6
    w[5] = ALNUM[value % 27 + 10]; value //= 27
    w[4] = ALNUM[value % 27 + 10]; value //= 27
    w[3] = ALNUM[value % 27 + 10]; value //= 27
    w[2] = ALNUM[value % 10]; value //= 10
    w[1] = ALNUM[value % 36]; value //= 36
    w[0] = ALNUM[value] if value < len(ALNUM) else "?"
    call = "".join(w)
    if call.startswith("3D0"):
        call = "3DA0" + call[3:]
    if call.startswith("Q") and len(call) > 1 and "A" <= call[1] <= "Z":
        call = "3X" + call[1:]
    if portable:
        call = call.strip() + "/P"
    return call.strip()


def _f32(x):
    """Round to a C float, as the original's grid maths uses floats."""
    import struct
    return struct.unpack("f", struct.pack("f", x))[0]


def grid2deg(grid):
    g = grid if len(grid) >= 6 else grid[:4] + "mm"
    g = g[:4].upper() + g[-2:].lower()
    nlong = 180 - 20 * (ord(g[0]) - ord("A"))
    n20d = 2 * (ord(g[2]) - ord("0"))
    xminlong = _f32(5 * (ord(g[4]) - ord("a") + 0.5))
    dlong = _f32(nlong - n20d - xminlong / 60.0)
    nlat = -90 + 10 * (ord(g[1]) - ord("A")) + ord(g[3]) - ord("0")
    xminlat = _f32(2.5 * (ord(g[5]) - ord("a") + 0.5))
    dlat = _f32(nlat + xminlat / 60.0)
    return dlong, dlat


def deg2grid(dlong, dlat):
    if dlong < -180:
        dlong += 360
    if dlong > 180:
        dlong -= 360
    nlong = int(60.0 * (180.0 - dlong) / 5)
    n1, n2 = nlong // 240, (nlong - 240 * (nlong // 240)) // 24
    n3 = nlong - 240 * n1 - 24 * n2
    g = [chr(ord("A") + n1), "", chr(ord("0") + n2), "", chr(ord("a") + n3), ""]
    nlat = int(60.0 * (dlat + 90) / 2.5)
    n1, n2 = nlat // 240, (nlat - 240 * (nlat // 240)) // 24
    n3 = nlat - 240 * n1 - 24 * n2
    g[1], g[3], g[5] = chr(ord("A") + n1), chr(ord("0") + n2), chr(ord("a") + n3)
    return "".join(g)


def pack_grid(value):
    grid = value.strip()
    if len(grid) < 4:
        return NMAXGRID
    dlong, dlat = grid2deg(grid[:4])
    ilong = int(dlong)                 # C++ float -> int: toward zero
    ilat = int(_f32(dlat + 90))
    return ((ilong + 180) // 2) * 180 + ilat


def unpack_grid(value):
    if value > NBASEGRID:
        return ""
    dlat = value % 180 - 90
    dlong = value // 180 * 2 - 180 + 2
    return deg2grid(dlong, dlat)[:4]


def pack_num(num):
    """Varicode::packNum: a number or SNR (-30..31) -> 1..62. Returns (packed, ok)."""
    if not num:
        return 0, False
    try:
        n = int(num.strip())
    except ValueError:
        return 31, False          # toInt() fails -> 0, clamped -> 0 + 31
    return max(-30, min(n, 31)) + 31, True


def pack_cmd(cmd, num):
    """Varicode::packCmd: command (+ SNR number) -> 8 bits."""
    cmd_str = _cmd_key(cmd)
    if is_snr_command(cmd_str):
        value = ((1 << 1) | int(cmd_str == " HEARTBEAT SNR")) << 6
        return (value + (num & 63)) & 0xFF
    return cmd & 127


def unpack_cmd(value):
    """Varicode::unpackCmd -> (command number, number)."""
    if value & 128:
        cmd = DIRECTED_CMDS[" HEARTBEAT SNR"] if value & 64 else DIRECTED_CMDS[" SNR"]
        return cmd, value & 63
    return value & 127, 0


def is_snr_command(cmd):
    return cmd in DIRECTED_CMDS and DIRECTED_CMDS[cmd] in SNR_CMDS


def is_command_allowed(cmd):
    return cmd in DIRECTED_CMDS and DIRECTED_CMDS[cmd] in ALLOWED_CMDS


def is_command_buffered(cmd):
    return cmd in DIRECTED_CMDS and (" " in cmd or DIRECTED_CMDS[cmd] in BUFFERED_CMDS)


def is_command_checksummed(cmd):
    if cmd not in DIRECTED_CMDS or DIRECTED_CMDS[cmd] not in CHECKSUM_CMDS:
        return 0
    return CHECKSUM_CMDS[DIRECTED_CMDS[cmd]]


def is_command_autoreply(cmd):
    return cmd in DIRECTED_CMDS and DIRECTED_CMDS[cmd] in AUTOREPLY_CMDS


def format_snr(snr):
    if snr < -60 or snr > 60:
        return ""
    return f"{'+' if snr >= 0 else '-'}{abs(snr):02d}"


def _valid_compound(callsign):
    if len(callsign.replace("/", "")) > 9:
        return False
    if "/" in callsign:
        return callsign[:callsign.index("/")] not in BASECALLS
    if callsign.startswith("@"):
        return True
    return len(callsign) > 2 and bool(_CALL_LIKE.search(callsign))


def is_valid_callsign(callsign):
    """Varicode::isValidCallsign -> (valid, is_compound)."""
    if callsign in BASECALLS:
        return True, False
    m = re.search(BASE_CALLSIGN_PATTERN, callsign)
    if m and len(m.group(0)) == len(callsign):
        return len(callsign) > 2 and bool(_CALL_LIKE.search(callsign)), False
    m = re.search("^" + COMPOUND_CALLSIGN_PATTERN, callsign)
    if m and len(m.group(0)) == len(callsign):
        ok = _valid_compound(m.group(0))
        return ok, ok
    return False, False


def is_compound_callsign(callsign):
    if callsign in BASECALLS and not callsign.startswith("@"):
        return False
    m = re.search(BASE_CALLSIGN_PATTERN, callsign)
    if m and len(m.group(0)) == len(callsign):
        return False
    m = re.search("^" + COMPOUND_CALLSIGN_PATTERN, callsign)
    if not m or len(m.group(0)) != len(callsign):
        return False
    return _valid_compound(m.group(0))


def parse_callsigns(text):
    out = []
    for m in re.finditer(COMPOUND_CALLSIGN_PATTERN, text):
        call = _cap(m, "callsign").strip()
        if not is_valid_callsign(call)[0] or re.search(GRID_PATTERN, call):
            continue
        out.append(call)
    return out


def starts_with_cq(text):
    return any(text.startswith(c) for c in CQS.values())


def starts_with_hb(text):
    return any(text.startswith(h) for h in HBS.values())


# ---------------------------------------------------------------- heartbeat and compound frames

def pack_compound_frame(callsign, ftype, num, bits3):
    """Varicode::packCompoundFrame: [3 type][50 callsign][11 num high],[5 num low][3 bits]."""
    if ftype in (FRAME_DATA, FRAME_DIRECTED):
        return ""
    packed_call = pack_alnum50(callsign)
    if packed_call == 0:
        return ""
    packed_11 = (num & (2047 << 5)) >> 5
    packed_8 = (((num & 31) << 3) | bits3) & 0xFF
    bits = int_to_bits(ftype, 3) + int_to_bits(packed_call, 50) + int_to_bits(packed_11, 11)
    return pack72(bits_to_int(bits), packed_8)


def unpack_compound_frame(text):
    """-> (callsign, type, num, bits3) or None."""
    if len(text) < 12 or " " in text:
        return None
    value, packed_8 = unpack72(text)
    bits = int_to_bits(value, 64)
    ftype = bits_to_int(bits[0:3])
    if ftype in (FRAME_DATA, FRAME_DIRECTED):
        return None
    call = unpack_alnum50(bits_to_int(bits[3:53]))
    num = ((bits_to_int(bits[53:64]) << 5) | (packed_8 >> 3)) & 0xFFFF
    return call, ftype, num, packed_8 & 7


def pack_heartbeat(text, callsign):
    """Varicode::packHeartbeatMessage: "CQ CQ EN80" / "HB EN80" -> (frame, chars used)."""
    m = HEARTBEAT_RE.search(text)
    if not m or not callsign:
        return "", 0
    extra, ctype = _cap(m, "grid"), _cap(m, "type")
    is_alt = ctype.startswith("CQ")
    packed_extra = NMAXGRID
    if len(extra) == 4 and re.search(GRID_PATTERN, extra):
        packed_extra = pack_grid(extra)
    number = next((k for k in sorted(HBS) if HBS[k] == ctype), 0)
    if is_alt:
        packed_extra |= 1 << 15
        number = next((k for k in sorted(CQS) if CQS[k] == ctype), 0)
    frame = pack_compound_frame(callsign, FRAME_HEARTBEAT, packed_extra, number)
    return (frame, len(m.group(0))) if frame else ("", 0)


def pack_compound(text):
    """Varicode::packCompoundMessage: "`KN4CRD/P EM73" or "`J1Y/P ACK" -> (frame, chars used)."""
    m = COMPOUND_RE.search(text)
    if not m:
        return "", 0
    call, grid, cmd = _cap(m, "callsign"), _cap(m, "grid"), _cap(m, "cmd")
    num = _cap(m, "num").strip()
    if not call:
        return "", 0
    ftype, extra = FRAME_COMPOUND, NMAXGRID
    if cmd and cmd in DIRECTED_CMDS and is_command_allowed(cmd):
        inum = pack_num(num)[0]
        extra = (NUSERGRID + pack_cmd(DIRECTED_CMDS[cmd] & 0xFF, inum)) & 0xFFFF
        ftype = FRAME_COMPOUND_DIRECTED
    elif grid:
        extra = pack_grid(grid)
    return pack_compound_frame(call, ftype, extra, 0), len(m.group(0))


# ---------------------------------------------------------------- directed frames

def pack_directed(text, mycall):
    """Varicode::packDirectedMessage: "N8GHI SNR?" -> (frame, chars used, info)."""
    m = DIRECTED_RE.search(text)
    none = ("", 0, {})
    if not m:
        return none
    frm = "<....>" if is_compound_callsign(mycall) else mycall
    to, cmd, num = _cap(m, "callsign"), _cap(m, "cmd"), _cap(m, "num")
    if not cmd:
        return none
    valid, to_compound = is_valid_callsign(to)
    if to == mycall or not valid:
        return none
    info = {"to": to, "to_compound": to_compound, "cmd": "", "num": ""}
    none = ("", 0, info)   # (the original has already handed back "to" by now)
    if to_compound:
        to = "<....>"
    if not is_command_allowed(cmd) and not is_command_allowed(cmd.strip()):
        return none
    inum, ok = pack_num(num.strip())
    if ok:
        info["num"] = num
    packed_from, portable_from = pack_callsign(frm)
    packed_to, portable_to = pack_callsign(to)
    if packed_from == 0 or packed_to == 0:
        return none
    cmd_out, packed_cmd = "", 0
    if cmd in DIRECTED_CMDS:
        cmd_out, packed_cmd = cmd, DIRECTED_CMDS[cmd] & 0xFF
    if cmd.strip() in DIRECTED_CMDS:
        cmd_out, packed_cmd = cmd.strip(), DIRECTED_CMDS[cmd.strip()] & 0xFF
    extra = ((int(portable_from) << 7) + (int(portable_to) << 6) + inum) & 0xFF
    bits = (int_to_bits(FRAME_DIRECTED, 3) + int_to_bits(packed_from, 28)
            + int_to_bits(packed_to, 28) + int_to_bits(packed_cmd % 32, 5))
    info["cmd"] = cmd_out
    return pack72(bits_to_int(bits), extra), len(m.group(0)), info


def unpack_directed(text):
    """-> [from, to, cmd(, number)] or None."""
    if len(text) < 12 or " " in text:
        return None
    value, extra = unpack72(text)
    bits = int_to_bits(value, 64)
    if bits_to_int(bits[0:3]) != FRAME_DIRECTED:
        return None
    frm = unpack_callsign(bits_to_int(bits[3:31]), bool((extra >> 7) & 1))
    to = unpack_callsign(bits_to_int(bits[31:59]), bool((extra >> 6) & 1))
    cmd = _cmd_key(bits_to_int(bits[59:64]) % 32)
    out = [frm, to, cmd]
    extra %= 64
    if extra:
        out.append(format_snr(extra - 31) if is_snr_command(cmd) else str(extra - 31))
    return out


# ---------------------------------------------------------------- word-list compression (jsc.cpp)

class _JSC:
    """JS8's word list: common words, call parts and characters, each a number."""
    _data = None
    S, C = 7, 9   # (s,c)-dense coding with 4-bit bytes: s = 7 stoppers, c = 16 - 7 continuers

    @classmethod
    def data(cls):
        if cls._data is None:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jsc_words.json.gz")
            with gzip.open(path, "rt", encoding="utf-8") as f:
                d = json.load(f)
            cls._data = d
            cls.size = len(d["map"])
        return cls._data

    @classmethod
    def lookup(cls, w):
        """JSC::lookup: the longest-first word-list entry that w starts with -> (index, ok)."""
        d = cls.data()
        b = w.encode("latin-1", errors="replace").decode("latin-1")
        if not b:
            return 0, False
        for s, size, index in d["prefix"]:
            if b[0] != s[0]:
                continue
            if size == 1:
                return d["list"][index][2], True
            for ls, lsize, lindex in d["list"][index:index + size]:
                if b[:lsize] == ls[:lsize]:     # strncmp(b, str, size) == 0
                    return lindex, True
            return 0, False
        return 0, False

    @classmethod
    def codeword(cls, index, separate):
        out = int_to_bits(((index % cls.S) << 1) + int(separate), 5)
        x = index // cls.S
        while x > 0:
            x -= 1
            out = int_to_bits((x % cls.C) + cls.S, 4) + out
            x //= cls.C
        return out

    @classmethod
    def compress(cls, text):
        """JSC::compress -> [(bits, characters covered), ...]."""
        d = cls.data()
        out = []
        words = text.split(" ")
        for i, w in enumerate(words):
            last_word = i == len(words) - 1
            is_space = False
            if not w and not last_word:
                w, is_space = " ", True
            while w:
                index, ok = cls.lookup(w)
                if not ok:
                    break
                size = d["map"][index][1]
                w = w[size:]
                append_space = not w and not is_space and not last_word
                out.append((cls.codeword(index, append_space), size + (1 if append_space else 0)))
        return out

    @classmethod
    def decompress(cls, bits):
        """JSC::decompress."""
        d = cls.data()
        s, c = cls.S, cls.C
        base = [0, s]
        for k in range(2, 8):
            base.append(base[-1] + s * c ** (k - 1))
        nbytes, seps = [], []
        i, count = 0, len(bits)
        while i < count:
            b = bits[i:i + 4]
            if len(b) != 4:
                break
            byte = bits_to_int(b)
            nbytes.append(byte)
            i += 4
            if byte < s:
                if count - i > 0 and bits[i]:
                    seps.append(len(nbytes) - 1)
                i += 1
        out, start = [], 0
        while start < len(nbytes):
            k = j = 0
            while start + k < len(nbytes) and nbytes[start + k] >= s:
                j = j * c + (nbytes[start + k] - s)
                k += 1
            if j >= cls.size or start + k >= len(nbytes):
                break
            j = j * s + nbytes[start + k] + base[k]
            if j >= cls.size:
                break
            out.append(d["map"][j][0])
            if seps and seps[0] == start + k:
                out.append(" ")
                seps.pop(0)
            start += k + 1
        return "".join(out)


def _huff_encode(text):
    out, i = [], 0
    while i < len(text):
        if text[i] in HUFF:
            out.append((1, [int(b) for b in HUFF[text[i]]]))
        i += 1
    return out


def _huff_decode(bits):
    s = "".join(str(b) for b in bits)
    text = []
    by_code = sorted(HUFF.items())
    while s:
        for ch, code in by_code:
            if s.startswith(code):
                text.append(ch)
                s = s[len(code):]
                break
        else:
            break
    return "".join(text)


def _pad72(frame_bits):
    """Pad to 72 bits: a 0, then 1s (unpadding seeks back to the last 0)."""
    pad = 72 - len(frame_bits)
    return frame_bits + [0 if k == 0 else 1 for k in range(pad)]


def _pack_huff(text, prefix):
    if any(c.upper() not in HUFF for c in text):
        return "", 0
    bits, n = list(prefix), 0
    for chars, cb in _huff_encode(text):
        if len(bits) + len(cb) < 72:
            bits += cb
            n += chars
            continue
        break
    bits = _pad72(bits)
    return pack72(bits_to_int(bits[:64]), bits_to_int(bits[64:72])), n


def _pack_compressed(text, prefix):
    bits, n = list(prefix), 0
    for cb, chars in _JSC.compress(text):
        if len(bits) + len(cb) < 72:
            bits += cb
            n += chars
            continue
        break
    bits = _pad72(bits)
    return pack72(bits_to_int(bits[:64]), bits_to_int(bits[64:72])), n


def pack_data(text):
    """Varicode::packDataMessage (Normal speed): [1 data][1 compressed?][70 text]."""
    hf, hn = _pack_huff(text, [1, 0])
    cf, cn = _pack_compressed(text, [1, 1])
    return (hf, hn) if hn > cn else (cf, cn)


def unpack_data(text):
    if len(text) < 12 or " " in text:
        return ""
    value, rem = unpack72(text)
    bits = int_to_bits(value, 64) + int_to_bits(rem, 8)
    if not bits[0]:
        return ""
    bits = bits[1:]
    compressed = bits[0]
    n = len(bits) - 1 - bits[::-1].index(0) if 0 in bits else -1
    bits = bits[1:n] if n >= 1 else bits[1:]   # QVector::mid(1, n-1): a negative length means "to the end"
    return _JSC.decompress(bits) if compressed else _huff_decode(bits)


def pack_fast_data(text):
    """Varicode::packFastDataMessage (Fast/Turbo/Slow): all 72 bits, word-list compressed."""
    return _pack_compressed(text, [])


def unpack_fast_data(text):
    if len(text) < 12 or " " in text:
        return ""
    value, rem = unpack72(text)
    bits = int_to_bits(value, 64) + int_to_bits(rem, 8)
    n = len(bits) - 1 - bits[::-1].index(0) if 0 in bits else -1
    return _JSC.decompress(bits if n < 0 else bits[:n])   # mid(0, -1) = all


# ---------------------------------------------------------------- whole messages

def build_frames(mycall, mygrid, text, selected_call="", force_identify=False, force_data=False,
                 speed="normal"):
    """Varicode::buildMessageFrames: typed text -> [(frame, flags), ...].

    text is what you'd type in JS8Call ("N8GHI SNR?", "CQ CQ EN80",
    "N8GHI HELLO DAVE"); selected_call is put in front when the text
    doesn't start with a call. Raises ValueError if the text can't be sent.
    """
    submode = SUBMODES[speed]
    mycall_compound = is_compound_callsign(mycall)
    line = text
    frames = []
    has_directed = has_data = False
    if force_data:
        force_identify = False
        has_data = True
    if line.startswith(mycall + ":") or line.startswith(mycall + " "):
        line = line[len(mycall) + 1:].lstrip()
    if selected_call and not line.startswith(selected_call) and not line.startswith("`") and not force_data:
        base_start = line.startswith("@ALLCALL") or starts_with_cq(line) or starts_with_hb(line)
        calls = parse_callsigns(line)
        std_start = bool(calls) and line.startswith(calls[0]) and len(calls[0]) > 3
        if not (base_start or std_start):
            line = selected_call + ("" if line.startswith(" ") else " ") + line

    while line:
        frame = ""
        use_bcn = use_cmp = use_dir = use_dat = False
        bcn, l = pack_heartbeat(line, mycall)
        cmp_, o = pack_compound(line)
        dir_, n, info = pack_directed(line, mycall)
        likely_data = not frames and not selected_call and not info.get("to") and l == 0 and o == 0
        if force_identify and likely_data and mycall not in line:
            line = f"{mycall}: {line}"
        if submode == SUBMODES["normal"]:
            dat, m = pack_data(line)
            fast = False
        else:
            dat, m = pack_fast_data(line)
            fast = True

        if not has_directed and not has_data and l > 0:
            use_bcn, frame = True, bcn
        elif not has_directed and not has_data and o > 0:
            use_cmp, frame = True, cmp_
        elif not has_directed and not has_data and n > 0:
            use_dir, has_directed, frame = True, True, dir_
        elif m > 0:
            use_dat, has_data, frame = True, True, dat
        else:
            raise ValueError(f"JS8 can't send this text: {line!r}")

        if use_bcn:
            frames.append([frame, FLAG_NONE])
            line = line[l:]
        if use_cmp:
            frames.append([frame, FLAG_NONE])
            line = line[o:]
        if use_dir:
            if mycall_compound or info["to_compound"]:
                de = pack_compound(f"`{mycall} {mygrid}")[0]
                if de:
                    frames.append([de, FLAG_NONE])
                dc = pack_compound(f"`{info['to']}{info['cmd']}{info['num']}")[0]
                if dc:
                    frames.append([dc, FLAG_NONE])
            else:
                frames.append([frame, FLAG_NONE])
            line = line[n:]
            if is_command_buffered(info["cmd"]) and line:
                line = line.lstrip()
                size = is_command_checksummed(info["cmd"])
                if size == 32:
                    line = line + " " + checksum32(line)
                elif size == 16:
                    line = line + " " + checksum16(line)
        if use_dat:
            frames.append([frame, FLAG_DATA if fast else FLAG_NONE])
            line = line[m:]

    if frames:
        frames[0][1] |= FLAG_FIRST
        frames[-1][1] |= FLAG_LAST
    return [tuple(f) for f in frames]


def decode_frame(frame, flags):
    """DecodedText::tryUnpack: one received frame -> {"type", "text", ...}.

    "text" is what JS8Call shows for the frame; a message is the frames of
    one station joined in order (FLAG_FIRST starts it, FLAG_LAST ends it).
    Returns None when the frame makes no sense.
    """
    m = frame.strip()
    if len(m) < 12 or " " in m or any(c not in ALPHABET72[:64] for c in m):
        return None
    is_data_flag = (flags & FLAG_DATA) == FLAG_DATA
    if is_data_flag:
        data = unpack_fast_data(m)
        return {"type": FRAME_DATA, "text": data} if data else None
    data = unpack_data(m)
    if data:
        return {"type": FRAME_DATA, "text": data}
    c = unpack_compound_frame(m)
    if c and c[1] == FRAME_HEARTBEAT:
        call, _, num, bits3 = c
        grid = unpack_grid(num & 32767)
        if num & (1 << 15):
            return {"type": FRAME_HEARTBEAT, "cq": True, "from": call, "grid": grid,
                    "text": f"{call}: @ALLCALL {CQS.get(bits3, '')} {grid} "}
        return {"type": FRAME_HEARTBEAT, "cq": False, "from": call, "grid": grid,
                "text": f"{call}: @HB HEARTBEAT {grid} "}
    if c and c[1] in (FRAME_COMPOUND, FRAME_COMPOUND_DIRECTED):
        call, ftype, extra, _ = c
        parts = []
        if extra <= NBASEGRID:
            parts.append(" " + unpack_grid(extra))
        elif NUSERGRID <= extra < NMAXGRID:
            cmd, num = unpack_cmd((extra - NUSERGRID) & 0xFF)
            cmd_str = _cmd_key(cmd)
            parts.append(cmd_str)
            if is_snr_command(cmd_str):
                parts.append(format_snr(num - 31))
        extra_s = " ".join(parts)
        if ftype == FRAME_COMPOUND:
            return {"type": ftype, "from": call, "extra": extra_s, "text": f"{call}: "}
        return {"type": ftype, "to": call, "extra": extra_s, "text": f"{call}{extra_s} "}
    d = unpack_directed(m)
    if d:
        frm, to, cmd = d[:3]
        text = f"{frm}: {to}{cmd} " + (f"{d[3]} " if len(d) == 4 else "")
        return {"type": FRAME_DIRECTED, "from": frm, "to": to, "cmd": cmd,
                "num": d[3] if len(d) == 4 else "", "text": text}
    return None


def decode_message(frames):
    """Join a station's frames [(frame, flags), ...] into the text JS8Call shows."""
    return "".join((decode_frame(f, b) or {"text": ""})["text"] for f, b in frames)

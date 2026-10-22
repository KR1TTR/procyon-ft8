"""WSJT-X compatible UDP messages, so companion programs that listen to
WSJT-X (GridTracker, JTAlert, Log4OM, N1MM...) work with Procyon too.

Format: WSJT-X NetworkMessage.hpp - Qt QDataStream, big-endian, schema 2."""

import asyncio
import datetime as dt
import logging
import struct

from . import __version__

log = logging.getLogger("procyon.udp")

MAGIC = 0xADBCCBDA
SCHEMA = 2
HEARTBEAT, STATUS, DECODE, CLEAR, REPLY, QSO_LOGGED, CLOSE, REPLAY, HALT_TX, \
    FREE_TEXT, WSPR_DECODE, LOCATION, LOGGED_ADIF, HIGHLIGHT = range(14)
NULL = 0xFFFFFFFF
JULIAN_OFFSET = 1721425   # date.toordinal() + this = Julian day number


class Writer:
    def __init__(self, kind: int, ident: str):
        self.b = bytearray()
        self.u32(MAGIC).u32(SCHEMA).u32(kind).utf8(ident)

    def _p(self, fmt, v):
        self.b += struct.pack(">" + fmt, v)
        return self

    def u8(self, v): return self._p("B", v)
    def boolean(self, v): return self._p("B", 1 if v else 0)
    def u32(self, v): return self._p("I", v & 0xFFFFFFFF)
    def i32(self, v): return self._p("i", v)
    def u64(self, v): return self._p("Q", v)
    def i64(self, v): return self._p("q", v)
    def f64(self, v): return self._p("d", v)

    def utf8(self, s):
        if s is None:
            return self.u32(NULL)
        data = str(s).encode()
        self.u32(len(data))
        self.b += data
        return self

    def qtime(self, ms: int):
        return self.u32(ms)

    def qdatetime(self, t: dt.datetime):
        t = t.astimezone(dt.timezone.utc)
        ms = ((t.hour * 60 + t.minute) * 60 + t.second) * 1000 + t.microsecond // 1000
        return self.i64(t.date().toordinal() + JULIAN_OFFSET).u32(ms).u8(1)   # 1 = UTC


class Reader:
    def __init__(self, data: bytes):
        self.d, self.i = data, 0

    def _u(self, fmt):
        v = struct.unpack_from(">" + fmt, self.d, self.i)[0]
        self.i += struct.calcsize(">" + fmt)
        return v

    def u8(self): return self._u("B")
    def boolean(self): return self._u("B") != 0
    def u32(self): return self._u("I")
    def i32(self): return self._u("i")
    def f64(self): return self._u("d")

    def utf8(self):
        n = self.u32()
        if n == NULL:
            return None
        s = self.d[self.i:self.i + n].decode(errors="replace")
        self.i += n
        return s


class WsjtxUdp(asyncio.DatagramProtocol):
    """Sends WSJT-X messages to one address (GridTracker listens on
    127.0.0.1:2237 by default) and handles requests that come back."""

    def __init__(self, ident="Procyon", on_reply=None, on_halt=None):
        self.ident = ident
        self.on_reply = on_reply
        self.on_halt = on_halt
        self.transport = None
        self.target = None
        self.accept = True

    async def start(self, host: str, port: int):
        await self.stop()
        self.target = (host, int(port))
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(lambda: self, local_addr=("0.0.0.0", 0))
        log.info("sending WSJT-X messages to %s:%s", host, port)
        self.heartbeat()

    async def stop(self):
        if self.transport:
            self.close()
            self.transport.close()
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def error_received(self, exc):
        pass   # nobody listening (GridTracker not running): that's fine

    def _send(self, w: Writer):
        if self.transport and self.target:
            try:
                self.transport.sendto(bytes(w.b), self.target)
            except OSError:
                pass

    # ------------------------------------------------------------ outgoing

    def heartbeat(self):
        self._send(Writer(HEARTBEAT, self.ident).u32(3).utf8("2.6.1").utf8(f"Procyon {__version__}"))

    def status(self, s: dict):
        w = Writer(STATUS, self.ident)
        (w.u64(s["dial"]).utf8("FT8").utf8(s["dx_call"]).utf8(s["report"]).utf8("FT8")
         .boolean(s["tx_enabled"]).boolean(s["transmitting"]).boolean(s["decoding"])
         .u32(s["rx_df"]).u32(s["tx_df"]).utf8(s["de_call"]).utf8(s["de_grid"])
         .utf8(s["dx_grid"]).boolean(False).utf8("").boolean(False).u8(0)
         .u32(NULL).u32(15).utf8("Default").utf8(s["tx_message"]))
        self._send(w)

    def decode(self, period_start: float, snr: int, dt_: float, df: int, msg: str,
               low_confidence=False):
        t = dt.datetime.fromtimestamp(period_start, dt.timezone.utc)
        ms = ((t.hour * 60 + t.minute) * 60 + t.second) * 1000
        w = Writer(DECODE, self.ident)
        (w.boolean(True).qtime(ms).i32(snr).f64(dt_).u32(df).utf8("~").utf8(msg)
         .boolean(low_confidence).boolean(False))
        self._send(w)

    def qso_logged(self, rec: dict, time_on: dt.datetime, time_off: dt.datetime, adif: str):
        w = Writer(QSO_LOGGED, self.ident)
        (w.qdatetime(time_off).utf8(rec.get("call", "")).utf8(rec.get("gridsquare", ""))
         .u64(int(round(float(rec.get("freq", "0") or 0) * 1e6))).utf8("FT8")
         .utf8(rec.get("rst_sent", "")).utf8(rec.get("rst_rcvd", "")).utf8(rec.get("tx_pwr", ""))
         .utf8(rec.get("comment", "")).utf8("").qdatetime(time_on).utf8("")
         .utf8(rec.get("station_callsign", "")).utf8(rec.get("my_gridsquare", ""))
         .utf8("").utf8("").utf8(""))
        self._send(w)
        self._send(Writer(LOGGED_ADIF, self.ident).utf8(adif))

    def clear(self):
        self._send(Writer(CLEAR, self.ident))

    def close(self):
        self._send(Writer(CLOSE, self.ident))

    # ------------------------------------------------------------ incoming

    def datagram_received(self, data, addr):
        try:
            r = Reader(data)
            if r.u32() != MAGIC:
                return
            r.u32()                 # schema
            kind = r.u32()
            r.utf8()                # their id for us
            if kind == REPLY and self.accept and self.on_reply:
                ms, snr, dt_, df = r.u32(), r.i32(), r.f64(), r.u32()
                mode, msg = r.utf8(), r.utf8()
                self.on_reply({"ms": ms, "snr": snr, "dt": dt_, "freq": df, "mode": mode, "msg": msg})
            elif kind == HALT_TX and self.accept and self.on_halt:
                self.on_halt()
        except (struct.error, IndexError):
            log.debug("ignored malformed UDP message from %s", addr)

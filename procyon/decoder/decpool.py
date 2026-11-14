"""Share a decode pass between several processes (CPU cores).

Each pass of ft8search/ft4search.decode_cycle finds candidate signals and
then works on every candidate on its own (tune in, soft bits, BP, OSD,
a priori guesses, exact position for subtraction). That per-candidate part
is about three quarters of the time, and it is what this spreads out:
candidate i goes to worker i % n. Finding candidates, unpacking and
subtracting stay in the calling process, in the same order as before, so
the decodes are exactly the same as with one process.

Workers are separate processes (Python runs one thread at a time), started
once with the "spawn" method (same on Linux, Windows and macOS) and kept
waiting between cycles. Each keeps its share of the pass (spectrum, soft
bits) between the three steps, so only small answers travel back.

    pool = Pool(4)          # 4 worker processes; Pool(0) = no workers
    ft8search.decode_cycle(audio, pool=pool)

The default pool (default()) uses one worker per core minus one, at most
MAX_WORKERS; env PROCYON_DECODE_WORKERS sets the number (0 = no workers).
"""

import importlib
import multiprocessing as mp
import os
import pickle
import threading

MAX_WORKERS = 8


def _serve(conn):
    """Worker process: wait for a step, do it on our share, answer."""
    st = None
    mod = None
    while True:
        try:
            msg = pickle.loads(conn.recv_bytes())
        except (EOFError, OSError):
            return
        op = msg[0]
        try:
            if op == "listen":
                _, modname, spec, cands, osd, knobs = msg
                mod = importlib.import_module(modname)
                for name, values in knobs.items():      # the caller's current settings
                    m = importlib.import_module(name)
                    for k, v in values.items():
                        setattr(m, k, v)
                st = mod.listen(spec, cands, osd)
                ans = (st["tunes"], st["amps"], st["got"])
            elif op == "guess":
                mod.guess(st, msg[1])
                ans = st["got"]
            elif op == "locate":
                ans = [mod.locate(st, i, msg[2]) for i in msg[1]]
            elif op == "stop":
                return
            else:
                raise ValueError(op)
            conn.send_bytes(pickle.dumps(("ok", ans), pickle.HIGHEST_PROTOCOL))
        except Exception as e:                  # report it; the caller raises
            conn.send_bytes(pickle.dumps(("error", repr(e)), pickle.HIGHEST_PROTOCOL))


class Pool:
    def __init__(self, workers):
        self.n = max(0, int(workers))
        self.conns = []
        self.procs = []
        self.st = None                         # no workers: the pass is kept here
        if self.n:
            # one thread each for numpy's maths libraries, or 8 workers x 8 threads fight
            for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
                os.environ.setdefault(var, "1")
            ctx = mp.get_context("spawn")
            for _ in range(self.n):
                a, b = ctx.Pipe()
                p = ctx.Process(target=_serve, args=(b,), daemon=True)
                p.start()
                b.close()
                self.conns.append(a)
                self.procs.append(p)

    # -- helpers --
    def _ask(self, msgs):
        """Send one message per worker (None = skip that worker), collect answers."""
        for c, m in zip(self.conns, msgs):
            if m is not None:
                c.send_bytes(m)
        out = []
        for c, m in zip(self.conns, msgs):
            if m is None:
                out.append(None)
                continue
            kind, ans = pickle.loads(c.recv_bytes())
            if kind != "ok":
                raise RuntimeError(f"decoder worker failed: {ans}")
            out.append(ans)
        return out

    @staticmethod
    def _dump(msg):
        return pickle.dumps(msg, pickle.HIGHEST_PROTOCOL)

    # -- the three steps of a pass --
    def listen(self, modname, spec, cands, osd=True):
        """-> (tunes, amps, got) for every candidate, in order."""
        self.B = len(cands)
        if not self.n:
            mod = importlib.import_module(modname)
            self.mod = mod
            self.st = mod.listen(spec, cands, osd)
            return self.st["tunes"], self.st["amps"], self.st["got"]
        half = spec[:len(spec) // 2 + 1]       # signals only use positive frequencies
        k = min(self.n, len(cands))
        self.active = k
        kn = knobs()
        msgs = [self._dump(("listen", modname, half, cands[w::k], osd, kn)) if w < k else None
                for w in range(self.n)]
        parts = self._ask(msgs)
        import numpy as np
        tunes, amps, got = [None] * self.B, [None] * self.B, [None] * self.B
        for w in range(k):
            t, a, g = parts[w]
            tunes[w::k], amps[w::k], got[w::k] = t, list(a), g
        self.got = got
        return tunes, np.array(amps), got

    def guess(self, ap):
        """A priori guesses on every candidate not yet read -> got."""
        if not self.n:
            self.mod.guess(self.st, ap)
            return self.st["got"]
        k = self.active
        msg = self._dump(("guess", ap))
        parts = self._ask([msg if w < k else None for w in range(self.n)])
        for w in range(k):
            self.got[w::k] = parts[w]
        return self.got

    def locate(self, idx, n_audio):
        """(start, frequency offset, rebuilt signal) of decoded candidates idx."""
        if not self.n:
            return [self.mod.locate(self.st, i, n_audio) for i in idx]
        k = self.active
        mine = [[i // k for i in idx if i % k == w] for w in range(self.n)]
        parts = self._ask([self._dump(("locate", m, n_audio)) if m else None for m in mine])
        pos = [0] * self.n
        out = []
        for i in idx:
            w = i % k
            out.append(parts[w][pos[w]])
            pos[w] += 1
        return out

    def close(self):
        for c in self.conns:
            try:
                c.send_bytes(self._dump(("stop",)))
                c.close()
            except OSError:
                pass
        for p in self.procs:
            p.join(2)
        self.conns, self.procs, self.n = [], [], 0


KNOB_MODULES = tuple(f"{__package__}.{m}" for m in ("ft8decode", "ft8search", "ft8subtract", "ft4search"))
KNOB_TYPES = (bool, int, float, str, tuple)


def knobs():
    """Every UPPER-CASE plain setting (number, text, yes/no) of the decoder
    modules loaded here - sent with each pass, so a setting changed in the
    calling process (a test trying OSD_MIN_SYNC=1.5, say) is what the
    workers use too."""
    import sys
    out = {}
    for name in KNOB_MODULES:
        m = sys.modules.get(name)
        if m is not None:
            out[name] = {k: v for k, v in vars(m).items()
                         if k.isupper() and type(v) in KNOB_TYPES}
    return out


_default = None
_lock = threading.Lock()


def default_workers():
    env = os.environ.get("PROCYON_DECODE_WORKERS")
    if env not in (None, ""):
        return int(env)
    try:
        cores = len(os.sched_getaffinity(0))   # the cores this program may use (Linux)
    except (AttributeError, OSError):
        cores = os.cpu_count() or 1
    return min(MAX_WORKERS, cores - 1) if cores > 2 else 0


def default():
    global _default
    with _lock:
        if _default is None:
            _default = Pool(default_workers())
        return _default

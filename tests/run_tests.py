"""Run all of Procyon's tests:

    python3 tests/run_tests.py            (all of them, about a minute)
    python3 tests/run_tests.py qso pota   (only files whose name contains these words)

Each check prints PASS or FAIL; the last line sums it up. Nothing needs to
be installed beyond what Procyon itself uses (pytest works too, if you
have it: python3 -m pytest tests).
"""

import importlib
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main(words):
    files = sorted(f[:-3] for f in os.listdir(HERE) if f.startswith("test_") and f.endswith(".py"))
    if words:
        files = [f for f in files if any(w in f for w in words)]
    passed, failed = 0, []
    start = time.time()
    for name in files:
        mod = importlib.import_module(name)
        about = " ".join((mod.__doc__ or "").split()).split(": ")[0].split(". ")[0]
        print(f"\n{name}  -  {about}")
        for fname in [n for n in vars(mod) if n.startswith("test_") and callable(getattr(mod, n))]:
            t = time.time()
            try:
                getattr(mod, fname)()
            except Exception as e:  # noqa: BLE001 - report every kind of failure
                failed.append(f"{name}.{fname}")
                print(f"  FAIL  {fname}: {e.__class__.__name__}: {e}")
                traceback.print_exc(limit=-2)
            else:
                passed += 1
                print(f"  PASS  {fname}  ({time.time() - t:.1f} s)")
    took = time.time() - start
    print(f"\n{passed} passed, {len(failed)} failed  ({took:.0f} s)")
    for f in failed:
        print(f"  failed: {f}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""PIN protection for remote (network) access.

The PIN is never stored: settings keep only an HMAC of it, keyed with a
random secret kept in the data folder. A signed-in device holds a cookie
derived from that hash, so changing the PIN signs every device out."""

import hashlib
import hmac
import os
import re
import secrets
import socket
import time

COOKIE = "procyon_session"
SESSION_DAYS = 30
PIN_RE = re.compile(r"^\d{4,8}$")
FREE_TRIES = 5            # wrong PINs allowed before a lockout
MAX_LOCK_SEC = 15 * 60


def is_local(remote: str | None) -> bool:
    return remote in ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def lan_addresses():
    """This computer's address(es) on the local network."""
    addrs = set()
    try:  # the address used for outbound traffic (no packet is actually sent)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            addrs.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    return sorted(a for a in addrs if not a.startswith("127."))


class Auth:
    def __init__(self, data_dir: str):
        path = os.path.join(data_dir, "secret.key")
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(secrets.token_bytes(32))
            os.chmod(path, 0o600)
        with open(path, "rb") as f:
            self.secret = f.read()
        self.fails: dict[str, list] = {}   # ip -> [wrong tries, locked until]

    def _mac(self, *parts: str) -> str:
        return hmac.new(self.secret, "|".join(parts).encode(), hashlib.sha256).hexdigest()

    def hash_pin(self, pin: str) -> str:
        return self._mac("pin", pin)

    def token(self, pin_hash: str) -> str:
        return self._mac("session", pin_hash)

    def valid_session(self, cookie: str | None, pin_hash: str) -> bool:
        return bool(cookie and pin_hash) and hmac.compare_digest(cookie, self.token(pin_hash))

    # --------------------------------------------------- brute-force guard

    def locked_for(self, ip: str) -> int:
        """Seconds until this device may try again (0 = may try now)."""
        f = self.fails.get(ip)
        return max(0, int(f[1] - time.time() + 0.999)) if f else 0

    def check_pin(self, ip: str, pin: str, pin_hash: str) -> bool:
        ok = bool(pin_hash) and hmac.compare_digest(self.hash_pin(pin), pin_hash)
        if ok:
            self.fails.pop(ip, None)
            return True
        f = self.fails.setdefault(ip, [0, 0.0])
        f[0] += 1
        if f[0] >= FREE_TRIES:   # 30 s, 60 s, 120 s ... up to 15 minutes
            f[1] = time.time() + min(MAX_LOCK_SEC, 30 * 2 ** (f[0] - FREE_TRIES))
        return False

    def tries_left(self, ip: str) -> int:
        f = self.fails.get(ip)
        return max(0, FREE_TRIES - (f[0] if f else 0))

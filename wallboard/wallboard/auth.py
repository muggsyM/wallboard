"""Admin password hashing (scrypt), signed session cookie, and FastAPI guard dependencies."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import math
import os
import time
from collections import deque
from urllib.parse import quote, urlsplit

from fastapi import HTTPException, Request

COOKIE_NAME = "wb_session"
SESSION_SECONDS = 30 * 24 * 3600

_N, _R, _P = 2**14, 8, 1
_SALT_BYTES, _KEY_BYTES = 16, 32
_MAXMEM = 64 * 1024 * 1024
LOOPBACK_HOSTS = {"127.0.0.1", "::1"}


def hash_password(pw: str) -> str:
    salt = os.urandom(_SALT_BYTES)
    key = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_KEY_BYTES, maxmem=_MAXMEM)
    return "scrypt${}${}${}${}${}".format(
        _N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(key).decode()
    )


def verify_password(pw: str, stored: str | None) -> bool:
    """Constant-time check; returns False (never raises) for a malformed or missing stored value."""
    try:
        if not stored:
            return False
        scheme, n, r, p, salt_b64, key_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n), int(r), int(p)
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(key_b64, validate=True)
        if n < 2 or n > 2**20 or r < 1 or r > 32 or p < 1 or p > 16 or not salt or not expected:
            return False
        actual = hashlib.scrypt(
            pw.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected), maxmem=_MAXMEM
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, binascii.Error, MemoryError):
        return False


def _sign(secret: str, expiry: str) -> str:
    return hmac.new(secret.encode(), expiry.encode(), hashlib.sha256).hexdigest()


def make_session_cookie(secret: str, expiry: float) -> str:
    exp = str(int(expiry))
    return f"{exp}.{_sign(secret, exp)}"


def new_session_cookie(secret: str) -> str:
    return make_session_cookie(secret, time.time() + SESSION_SECONDS)


def valid_session_cookie(secret: str, value: str | None) -> bool:
    if not value:
        return False
    exp, sep, sig = value.partition(".")
    if not sep or not exp.isdigit():
        return False
    if not hmac.compare_digest(sig.encode(), _sign(secret, exp).encode()):
        return False
    return int(exp) > time.time()


def safe_next(target: str | None, default: str = "/settings") -> str:
    """Only allow local absolute paths ('/x', not '//host' or '/\\host')."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return default


def require_admin(request: Request) -> None:
    secret = request.app.state.store.secrets.session_secret
    if valid_session_cookie(secret, request.cookies.get(COOKIE_NAME)):
        return
    path = request.url.path
    if path.startswith("/api/"):
        raise HTTPException(status_code=401, detail="unauthorized")
    raise HTTPException(status_code=303, headers={"Location": f"/login?next={quote(path, safe='/')}"})


_HOST_HEADERS = {"localhost", "127.0.0.1", "[::1]"}
_ORIGIN_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}


def is_loopback_host_header(host: str | None) -> bool:
    """True only for localhost / 127.0.0.1 / [::1], with or without a numeric :port."""
    if not host:
        return False
    host = host.lower()
    if host in _HOST_HEADERS:
        return True
    name, sep, port = host.rpartition(":")
    return bool(sep) and port.isdigit() and name in _HOST_HEADERS


def is_allowed_display_origin(origin: str | None) -> bool:
    """No Origin (non-browser client) or an http(s) loopback origin on any port."""
    if origin is None:
        return True
    try:
        parts = urlsplit(origin)
        parts.port  # raises ValueError on a bad port
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and (parts.hostname or "") in _ORIGIN_HOSTNAMES


def require_loopback(request: Request) -> None:
    host = request.client.host if request.client else None
    if host not in LOOPBACK_HOSTS or not is_loopback_host_header(request.headers.get("host")):
        raise HTTPException(status_code=403, detail="forbidden")


class LoginLimiter:
    """In-memory login-attempt throttle per client IP.

    `begin()` is synchronous and counts the attempt up front (as a presumed failure), so
    concurrent requests cannot all slip past the check while verification is in flight.
    The `max_failures`-th attempt is admitted but triggers a lockout of `lockout_seconds`;
    each further lockout doubles the window, capped at `max_lockout`. `success()` resets the IP.
    """

    MAX_ENTRIES = 1024
    GLOBAL_MAX_FAILURES = 30
    GLOBAL_WINDOW = 900.0
    _MAX_EXPONENT = 20

    def __init__(
        self,
        max_failures: int = 5,
        lockout_seconds: float = 30.0,
        max_lockout: float = 900.0,
        now=time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self.max_lockout = max_lockout
        self._now = now
        # ip -> [attempts, locked_until, lockouts, last_seen]
        self._state: dict[str, list] = {}
        self._global: deque[float] = deque()  # reservation times not cleared by a success

    def _prune(self, now: float) -> None:
        """Drop only dead entries: unlocked and idle longer than max_lockout. Never evicts live ones."""
        stale = [ip for ip, st in self._state.items() if st[1] <= now and now - st[3] > self.max_lockout]
        for ip in stale:
            del self._state[ip]
        while self._global and self._global[0] <= now - self.GLOBAL_WINDOW:
            self._global.popleft()

    def retry_after(self, ip: str) -> int:
        """Whole seconds until another attempt is allowed (0 if allowed now)."""
        st = self._state.get(ip)
        if not st:
            return 0
        return max(0, math.ceil(st[1] - self._now()))

    def begin(self, ip: str) -> int:
        """Reserve an attempt. Returns 0 if admitted, else seconds to wait (attempt refused)."""
        now = self._now()
        self._prune(now)
        st = self._state.get(ip)
        if st is not None and st[1] > now:
            return math.ceil(st[1] - now)
        if st is None and len(self._state) >= self.MAX_ENTRIES:
            return math.ceil(self.lockout_seconds)  # table full of live entries: fail closed
        if len(self._global) >= self.GLOBAL_MAX_FAILURES:
            return max(1, math.ceil(self._global[0] + self.GLOBAL_WINDOW - now))
        if st is None:
            st = self._state[ip] = [0, 0.0, 0, now]
        st[3] = now
        self._global.append(now)
        st[0] += 1
        if st[0] >= self.max_failures:
            st[1] = now + self.max_lockout  # fail closed if the arithmetic below raises
            window = min(self.lockout_seconds * 2 ** min(st[2], self._MAX_EXPONENT), self.max_lockout)
            st[1] = now + window
            st[0] = 0
            st[2] += 1
        return 0

    def success(self, ip: str) -> None:
        self._state.pop(ip, None)
        if self._global:
            self._global.pop()  # the reserved attempt turned out to be a success

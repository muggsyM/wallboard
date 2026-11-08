"""Admin password hashing (scrypt), signed session cookie, and FastAPI guard dependencies."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import itertools
import math
import os
import time
from collections import deque
from urllib.parse import quote, urlsplit

from fastapi import HTTPException, Request
from starlette.requests import HTTPConnection

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


def is_same_origin(origin: str | None, host: str | None) -> bool:
    """True when a browser Origin names exactly the host (and port) the request was sent to."""
    if not origin or not host:
        return False
    try:
        parts = urlsplit(origin)
        parts.port  # raises ValueError on a bad port
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and parts.netloc.lower() == host.lower()


def is_loopback_request(conn: HTTPConnection) -> bool:
    """Loopback peer and loopback Host header: the kiosk browser on the Pi itself."""
    host = conn.client.host if conn.client else None
    return host in LOOPBACK_HOSTS and is_loopback_host_header(conn.headers.get("host"))


def has_admin_session(conn: HTTPConnection) -> bool:
    secret = conn.app.state.store.secrets.session_secret
    return valid_session_cookie(secret, conn.cookies.get(COOKIE_NAME))


def require_loopback(request: Request) -> None:
    if not is_loopback_request(request):
        raise HTTPException(status_code=403, detail="forbidden")


def is_lan_host_header(host: str | None) -> bool:
    """A Host that can only mean this box on the home network: an IP literal, a single-label
    name ("wallboard") or an mDNS name ("wallboard.local"), with an optional port.

    Anything with a public-looking domain is refused, so a DNS-rebinding site
    (evil.example resolving to the Pi) cannot read the password-free board through a
    visitor's browser."""
    if not host:
        return False
    host = host.strip().lower()
    if host.startswith("["):  # [IPv6] or [IPv6]:port
        name, _, rest = host[1:].partition("]")
        if rest and not (rest.startswith(":") and rest[1:].isdigit()):
            return False
    else:
        name, sep, port = host.rpartition(":")
        if not sep:
            name = host
        elif not port.isdigit():
            return False
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    if not name or not all(c.isalnum() or c in "-." for c in name):
        return False
    return "." not in name or name.endswith(".local")


def public_view_enabled(conn: HTTPConnection) -> bool:
    """Settings switch: anyone on the LAN may view (never control) the board without a login.

    Read live from the config store on every request or connection, so toggling it in Settings
    applies without a restart. Only honoured for LAN-style Host names (see is_lan_host_header)."""
    return bool(conn.app.state.store.config.public_view) and is_lan_host_header(conn.headers.get("host"))


def may_view_board(conn: HTTPConnection) -> bool:
    """The kiosk on the Pi, a logged-in admin, or anyone when public_view is on (HTTP only;
    WebSockets also need a same-origin Origin, see routes/display.py)."""
    return is_loopback_request(conn) or has_admin_session(conn) or public_view_enabled(conn)


def require_display_viewer(request: Request) -> None:
    """The kiosk on the Pi, a logged-in admin viewing the board from another device, or (with
    public_view on) any visitor; otherwise a redirect to the login page."""
    if is_loopback_request(request) or public_view_enabled(request):
        return
    require_admin(request)


class LoginLimiter:
    """In-memory login-attempt throttle per client IP.

    `begin()` is synchronous and counts the attempt up front (as a presumed failure), so
    concurrent requests cannot all slip past the check while verification is in flight.
    The `max_failures`-th attempt is admitted but triggers a lockout of `lockout_seconds`;
    each further lockout doubles the window, capped at `max_lockout`. `success()` resets the IP.

    A global budget (GLOBAL_MAX_FAILURES per GLOBAL_WINDOW, across all IPs) stops a spread-out
    guessing attack. Loopback peers (someone on the Pi itself, e.g. over an SSH tunnel) are
    exempt from that global budget only, so a LAN attack can't lock the owner out of the Pi;
    they stay limited per IP.
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
        # (reservation time, token) of attempts not cleared by a success, oldest first
        self._global: deque[tuple[float, int]] = deque()
        self._tokens = itertools.count(1)

    def _prune(self, now: float) -> None:
        """Drop only dead entries: unlocked and idle longer than max_lockout. Never evicts live ones."""
        stale = [ip for ip, st in self._state.items() if st[1] <= now and now - st[3] > self.max_lockout]
        for ip in stale:
            del self._state[ip]
        while self._global and self._global[0][0] <= now - self.GLOBAL_WINDOW:
            self._global.popleft()

    def retry_after(self, ip: str) -> int:
        """Whole seconds until another attempt is allowed (0 if allowed now)."""
        st = self._state.get(ip)
        if not st:
            return 0
        return max(0, math.ceil(st[1] - self._now()))

    def begin(self, ip: str) -> tuple[int, int | None]:
        """Reserve an attempt. Returns (wait, token): wait is 0 if admitted, else seconds to wait
        (attempt refused). Pass the token to `success()`; it is None when nothing was reserved
        in the global budget (refused, or a loopback peer)."""
        now = self._now()
        self._prune(now)
        st = self._state.get(ip)
        if st is not None and st[1] > now:
            return math.ceil(st[1] - now), None
        if st is None and len(self._state) >= self.MAX_ENTRIES:
            return math.ceil(self.lockout_seconds), None  # table full of live entries: fail closed
        counts_globally = ip not in LOOPBACK_HOSTS
        if counts_globally and len(self._global) >= self.GLOBAL_MAX_FAILURES:
            return max(1, math.ceil(self._global[0][0] + self.GLOBAL_WINDOW - now)), None
        if st is None:
            st = self._state[ip] = [0, 0.0, 0, now]
        st[3] = now
        token = None
        if counts_globally:
            token = next(self._tokens)
            self._global.append((now, token))
        st[0] += 1
        if st[0] >= self.max_failures:
            st[1] = now + self.max_lockout  # fail closed if the arithmetic below raises
            window = min(self.lockout_seconds * 2 ** min(st[2], self._MAX_EXPONENT), self.max_lockout)
            st[1] = now + window
            st[0] = 0
            st[2] += 1
        return 0, token

    def success(self, ip: str, token: int | None) -> None:
        """The attempt reserved by `begin()` (its `token`) succeeded: reset the IP and release
        exactly that global reservation (not whichever came last)."""
        self._state.pop(ip, None)
        if token is None:
            return
        for entry in self._global:
            if entry[1] == token:
                self._global.remove(entry)
                break

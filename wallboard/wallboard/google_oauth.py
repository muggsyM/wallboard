"""Google OAuth: auth URL, signed one-time `state`, code exchange, cached access tokens.

Nothing here logs or returns token values, authorization codes or the client secret.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import re
import secrets as _secrets
import time
from collections.abc import Callable
from urllib.parse import urlencode

import httpx

from .config import Secrets

REDIRECT_URI = "http://localhost:8080/api/google/callback"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly", "openid", "email"]
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
HTTP_TIMEOUT = 10.0
EXPIRY_MARGIN = 60.0
STATE_TTL = 600.0

NO_REFRESH_TOKEN_MSG = (
    "Google did not return a refresh token; remove the app's access in your Google account and try again"
)


class GoogleError(Exception):
    """Google could not be reached or answered with an unexpected error."""


class GoogleAuthExpired(GoogleError):
    """The refresh token was revoked or expired (invalid_grant); the member must sign in again."""

    def __init__(self, account_id: str) -> None:
        super().__init__(f"Google authorization expired for account {account_id}")
        self.account_id = account_id


class NoRefreshToken(GoogleError):
    def __init__(self) -> None:
        super().__init__(NO_REFRESH_TOKEN_MSG)


class InvalidState(Exception):
    """The OAuth `state` is forged, expired, replayed or refers to an unknown member."""


def account_id_for(email: str) -> str:
    return "g_" + hashlib.sha256(email.lower().encode()).hexdigest()[:12]


def auth_url(client_id: str, state: str) -> str:
    return AUTH_ENDPOINT + "?" + urlencode(
        {
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
    )


# ---- state ----


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class OAuthStates:
    """Issues HMAC-signed, single-use `state` values: b64url(member_id|nonce|expiry).hexsig."""

    def __init__(self, key: Callable[[], str], now: Callable[[], float] = time.time) -> None:
        self._key = key  # callable so a changed session_secret is picked up
        self._now = now
        self._nonces: dict[str, float] = {}  # nonce -> expiry

    def _sign(self, payload: str) -> str:
        return hmac.new(self._key().encode(), payload.encode(), hashlib.sha256).hexdigest()

    def issue(self, member_id: str) -> str:
        now = self._now()
        self._nonces = {n: e for n, e in self._nonces.items() if e > now}
        nonce = _secrets.token_urlsafe(16)
        expiry = int(now + STATE_TTL)
        self._nonces[nonce] = expiry
        payload = f"{member_id}|{nonce}|{expiry}"
        return f"{_b64e(payload.encode())}.{self._sign(payload)}"

    def consume(self, state: str, member_ids: set[str]) -> str:
        """Validate `state`, burn its nonce, and return the member_id. Raises InvalidState."""
        try:
            enc, sep, sig = state.partition(".")
            if not sep:
                raise InvalidState
            payload = _b64d(enc).decode()
            if not hmac.compare_digest(sig.encode(), self._sign(payload).encode()):
                raise InvalidState
            member_id, nonce, expiry = payload.rsplit("|", 2)
            expiry_ts = int(expiry)
        except (ValueError, binascii.Error, UnicodeError) as e:
            raise InvalidState from e
        known = self._nonces.pop(nonce, None)  # burn first: a replay never gets a second try
        if known is None or expiry_ts <= self._now():
            raise InvalidState
        if member_id not in member_ids:
            raise InvalidState
        return member_id


# ---- tokens ----


class TokenCache:
    """In-memory access-token cache {account_id: (token, expires_at)}."""

    def __init__(self, now: Callable[[], float] = time.time) -> None:
        self._now = now
        self._items: dict[str, tuple[str, float]] = {}

    def get(self, account_id: str) -> str | None:
        item = self._items.get(account_id)
        if item and item[1] - EXPIRY_MARGIN > self._now():
            return item[0]
        return None

    def put(self, account_id: str, token: str, expires_at: float) -> None:
        self._items[account_id] = (token, expires_at)

    def invalidate(self, account_id: str) -> None:
        self._items.pop(account_id, None)

    def clear(self) -> None:
        self._items.clear()


token_cache = TokenCache()


async def _post_token(data: dict[str, str]) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            return await client.post(TOKEN_ENDPOINT, data=data)
    except httpx.HTTPError as e:
        raise GoogleError(f"Google token request failed ({type(e).__name__})") from None


def _error_code(resp: httpx.Response) -> str | None:
    try:
        err = resp.json().get("error")
    except (ValueError, AttributeError):
        return None
    return err if isinstance(err, str) else None


async def exchange_code(secrets: Secrets, code: str) -> tuple[str, str]:
    """Trade an authorization code for (refresh_token, email)."""
    if not secrets.google_client_id or not secrets.google_client_secret:
        raise GoogleError("Google client not configured")
    resp = await _post_token(
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": secrets.google_client_id,
            "client_secret": secrets.google_client_secret,
            "redirect_uri": REDIRECT_URI,
        }
    )
    if resp.status_code != 200:
        raise GoogleError(f"Google rejected the authorization code (HTTP {resp.status_code})")
    try:
        body = resp.json()
        refresh = body.get("refresh_token")
    except (ValueError, AttributeError):
        raise GoogleError("Google returned an unreadable token response") from None
    if not refresh or not isinstance(refresh, str):
        raise NoRefreshToken
    try:
        email = json.loads(_b64d(body["id_token"].split(".")[1]))["email"]
    except (ValueError, KeyError, IndexError, AttributeError, TypeError, binascii.Error):
        raise GoogleError("Google returned an unreadable id_token") from None
    if not isinstance(email, str) or not email:
        raise GoogleError("Google did not return an email address")
    return refresh, email


async def access_token(secrets: Secrets, account_id: str, cache: TokenCache | None = None) -> str:
    cache = cache or token_cache
    cached = cache.get(account_id)
    if cached:
        return cached
    refresh = secrets.google_refresh_tokens.get(account_id)
    if not secrets.google_client_id or not secrets.google_client_secret:
        raise GoogleError("Google client not configured")
    if not refresh:
        raise GoogleAuthExpired(account_id)
    resp = await _post_token(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": secrets.google_client_id,
            "client_secret": secrets.google_client_secret,
        }
    )
    if resp.status_code == 400 and _error_code(resp) == "invalid_grant":
        cache.invalidate(account_id)
        raise GoogleAuthExpired(account_id)
    if resp.status_code != 200:
        raise GoogleError(f"Google token refresh failed (HTTP {resp.status_code})")
    try:
        body = resp.json()
        token = body["access_token"]
        ttl = float(body.get("expires_in", 3600))
    except (ValueError, KeyError, TypeError, AttributeError):
        raise GoogleError("Google returned an unreadable token response") from None
    cache.put(account_id, token, cache._now() + ttl)
    return token


_SENSITIVE_PARAM = re.compile(r"([?&](?:code|state)=)[^&\s]*")


class RedactCallbackParams(logging.Filter):
    """uvicorn's access log prints the request target; blank the OAuth code/state in it."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _SENSITIVE_PARAM.sub(r"\1REDACTED", a) if isinstance(a, str) else a for a in record.args
            )
        return True

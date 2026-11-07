"""Google Calendar service: poll each account, merge, cache on disk, publish to the hub.

One account failing (expired sign-in, network) never affects the others: each account has its own
cache entry, which survives failed fetches for up to 24 hours. Tokens are never logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from . import google_oauth
from .config import ConfigStore, Secrets
from .events import display_range, normalize_google_event, to_json
from .google_oauth import GoogleError, TokenCache
from .hub import Hub

log = logging.getLogger(__name__)

API = "https://www.googleapis.com/calendar/v3"
CACHE_NAME = "calendar_cache.json"
POLL_SECONDS = 300.0
HTTP_TIMEOUT = 15.0
CACHE_MAX_AGE = timedelta(hours=24)

TokenFn = Callable[[Secrets, str], Awaitable[str]]


def _local_midnight(day: date, tz: ZoneInfo) -> str:
    return datetime.combine(day, time(0), tzinfo=tz).isoformat()


def _in_range(ev: dict, first: date, last: date, tz: ZoneInfo) -> bool:
    """Does the EventJSON touch any local day in [first, last]?"""
    try:
        if ev["all_day"]:
            start, end_excl = date.fromisoformat(ev["start"]), date.fromisoformat(ev["end"])
            last_day = end_excl - timedelta(days=1)
        else:
            start = datetime.fromisoformat(ev["start"]).astimezone(tz).date()
            end = datetime.fromisoformat(ev["end"]).astimezone(tz)
            last_day = (end - timedelta(microseconds=1)).date()
    except (KeyError, ValueError, TypeError):
        return False
    return start <= last and max(last_day, start) >= first


class GoogleCalendarService:
    def __init__(
        self,
        store: ConfigStore,
        hub: Hub,
        *,
        token_fn: TokenFn | None = None,
        now: Callable[[], datetime] | None = None,
        token_cache: TokenCache | None = None,
        interval: float = POLL_SECONDS,
    ) -> None:
        self.store = store
        self.hub = hub
        self.status: dict[str, bool] = {}
        self._token_cache = token_cache or google_oauth.token_cache
        self._token_fn = token_fn or (lambda s, a: google_oauth.access_token(s, a, self._token_cache))
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._interval = interval
        self._cache_path = Path(store.data_dir) / CACHE_NAME
        self._accounts: dict[str, dict] = {}  # account_id -> {"fetched_at": iso, "events": [EventJSON]}
        self._client: httpx.AsyncClient | None = None
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    # ---- lifecycle ----

    async def start(self) -> None:
        self._load_cache()
        self.status = {a.id: False for a in self.store.config.google_accounts}
        await self._publish()
        self._task = asyncio.create_task(self._run(), name="gcal-poll")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._client:
            await self._client.aclose()
            self._client = None

    def refresh_now(self) -> None:
        self._wake.set()

    async def _run(self) -> None:
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # keep polling whatever happens
                log.warning("calendar poll failed (%s)", type(e).__name__)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self._interval)
            self._wake.clear()

    # ---- cache ----

    def _load_cache(self) -> None:
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
            accounts = raw["accounts"]
            self._accounts = {
                k: {"fetched_at": v["fetched_at"], "events": list(v["events"])}
                for k, v in accounts.items()
                if isinstance(v, dict)
            }
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self._accounts = {}
            return
        self._expire()

    def _expire(self) -> None:
        now = self._now()
        for account_id in list(self._accounts):
            try:
                age = now - datetime.fromisoformat(self._accounts[account_id]["fetched_at"])
            except (KeyError, ValueError, TypeError):
                age = CACHE_MAX_AGE
            if age >= CACHE_MAX_AGE:
                del self._accounts[account_id]

    def _save_cache(self, first: date, last: date) -> None:
        data = {
            "accounts": self._accounts,
            "range_start": first.isoformat(),
            "range_end": last.isoformat(),
        }
        tmp = self._cache_path.with_name(CACHE_NAME + ".tmp")
        try:
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self._cache_path)
        except OSError as e:
            log.warning("could not write calendar cache (%s)", type(e).__name__)

    # ---- polling ----

    def _tz(self) -> ZoneInfo:
        return ZoneInfo(self.store.config.timezone)

    def _range(self) -> tuple[date, date]:
        return display_range(self._now().astimezone(self._tz()).date())

    async def poll_once(self) -> None:
        cfg = self.store.config
        tz = self._tz()
        first, last = self._range()
        ids = [a.id for a in cfg.google_accounts]
        for gone in set(self._accounts) - set(ids):
            del self._accounts[gone]
        self.status = {i: self.status.get(i, False) for i in ids}
        for account_id in ids:
            try:
                events = await self._fetch_account(account_id, first, last, tz)
            except Exception as e:  # GoogleError or anything else: never let one account stop the others
                self._record_failure(account_id, e)
            else:
                self.status[account_id] = True
                self._accounts[account_id] = {"fetched_at": self._now().isoformat(), "events": events}
        self._expire()
        self._save_cache(first, last)
        await self._publish()

    def _record_failure(self, account_id: str, exc: Exception) -> None:
        self.status[account_id] = False
        log.warning("google calendar fetch failed for account %s (%s)", account_id, type(exc).__name__)

    async def _fetch_account(self, account_id: str, first: date, last: date, tz: ZoneInfo) -> list[dict]:
        cfg = self.store.config
        members = {m.id: m for m in cfg.members}
        params = {
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "2500",
            "timeMin": _local_midnight(first, tz),
            "timeMax": _local_midnight(last + timedelta(days=1), tz),
        }
        out: list[dict] = []
        for ref in cfg.calendars:
            member = members.get(ref.member_id)
            if ref.account_id != account_id or not ref.enabled or member is None:
                continue
            url = f"{API}/calendars/{quote(ref.calendar_id, safe='')}/events"
            async for item in self._paged(account_id, url, params):
                event = normalize_google_event(item, member, tz)
                if event is not None:
                    out.append(to_json(event))
        return out

    async def _publish(self) -> None:
        first, last = self._range()
        tz = self._tz()
        events = [
            e
            for entry in self._accounts.values()
            for e in entry["events"]
            if _in_range(e, first, last, tz)
        ]
        events.sort(key=lambda e: (e["start"], e["title"]))
        payload = {
            "range_start": first.isoformat(),
            "range_end": last.isoformat(),
            "events": events,
            "holidays": {},
        }
        await self.hub.publish({"type": "calendar", "calendar": payload})
        await self.hub.set_status("google", dict(self.status))

    # ---- HTTP ----

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=HTTP_TIMEOUT)
        return self._client

    async def _get(self, account_id: str, url: str, params: dict) -> dict:
        for attempt in (0, 1):
            token = await self._token_fn(self.store.secrets, account_id)
            try:
                resp = await self._http().get(url, params=params, headers={"Authorization": f"Bearer {token}"})
            except httpx.HTTPError as e:
                raise GoogleError(f"Google request failed ({type(e).__name__})") from None
            if resp.status_code == 401 and attempt == 0:
                self._token_cache.invalidate(account_id)
                continue
            break
        if resp.status_code != 200:
            raise GoogleError(f"Google request failed (HTTP {resp.status_code})")
        try:
            body = resp.json()
        except ValueError:
            raise GoogleError("Google returned an unreadable response") from None
        if not isinstance(body, dict):
            raise GoogleError("Google returned an unreadable response")
        return body

    async def _paged(self, account_id: str, url: str, params: dict):
        page: str | None = None
        while True:
            q = {**params, "pageToken": page} if page else params
            body = await self._get(account_id, url, q)
            for item in body.get("items") or []:
                yield item
            page = body.get("nextPageToken")
            if not page:
                return

    async def list_calendars(self, account_id: str) -> list[dict]:
        out = []
        async for item in self._paged(account_id, f"{API}/users/me/calendarList", {}):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            out.append(
                {
                    "id": item["id"],
                    "name": item.get("summaryOverride") or item.get("summary") or item["id"],
                    "primary": bool(item.get("primary", False)),
                }
            )
        return out

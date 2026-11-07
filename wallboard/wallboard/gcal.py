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
from .config import ConfigStore, Member, Secrets
from .events import display_range, normalize_google_event, to_json
from .google_oauth import GoogleAuthExpired, GoogleError, TokenCache
from .holidays_cz import czech_holidays
from .hub import Hub

log = logging.getLogger(__name__)

API = "https://www.googleapis.com/calendar/v3"
CACHE_NAME = "calendar_cache.json"
POLL_SECONDS = 300.0
HTTP_TIMEOUT = 15.0
CACHE_MAX_AGE = timedelta(hours=24)
CACHE_REFRESH = timedelta(hours=6)  # rewrite an unchanged cache only when its fetched_at is this old
MAX_PAGES = 50
EVENT_KEYS = ('id', 'title', 'start', 'end', 'all_day')

TokenFn = Callable[[Secrets, str], Awaitable[str]]


def _local_midnight(day: date, tz: ZoneInfo) -> str:
    return datetime.combine(day, time(0), tzinfo=tz).isoformat()


def _valid_event(e: object) -> bool:
    return isinstance(e, dict) and all(k in e for k in EVENT_KEYS)


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


def holidays_for_range(first: date, last: date, language: str) -> dict[str, str]:
    """Czech public holidays in [first, last] as {iso date: name in `language`}.

    The range may span two years (a December month view reaches into January).
    """
    out: dict[str, str] = {}
    for year in range(first.year, last.year + 1):
        for day, names in czech_holidays(year).items():
            if first <= day <= last:
                out[day.isoformat()] = names.get(language) or names["cs"]
    return dict(sorted(out.items()))


def expand_dev_calendar(spec: dict, today: date, tz: ZoneInfo, members: list[Member]) -> list[dict]:
    """Dev only: turn dev/sample_calendar.json (days relative to today) into EventJSON.

    Each event: {"day": n, "title", "member_id", "location"?} plus either
    {"start": "HH:MM", "end": "HH:MM", "end_day"?: n} (timed, local time in tz) or
    {"all_day": true, "days"?: n} (n days, default 1). Events of unknown members are skipped.
    """
    by_id = {m.id: m for m in members}
    out = []
    for i, item in enumerate(spec.get("events", [])):
        member = by_id.get(item.get("member_id"))
        if member is None:
            continue
        day = today + timedelta(days=int(item.get("day", 0)))
        if item.get("all_day"):
            start: str = day.isoformat()
            end: str = (day + timedelta(days=int(item.get("days", 1)))).isoformat()
        else:
            end_day = today + timedelta(days=int(item.get("end_day", item.get("day", 0))))
            start = datetime.combine(day, time.fromisoformat(item["start"]), tzinfo=tz).isoformat()
            end = datetime.combine(end_day, time.fromisoformat(item["end"]), tzinfo=tz).isoformat()
        out.append(
            {
                "id": f"dev-{i}",
                "title": item.get("title") or "",
                "location": item.get("location"),
                "start": start,
                "end": end,
                "all_day": bool(item.get("all_day")),
                "member_id": member.id,
                "color": member.color,
            }
        )
    return out


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
        dev_calendar: Path | None = None,
    ) -> None:
        self.store = store
        # Dev only: publish this sample file (see expand_dev_calendar) instead of polling Google.
        self.dev_calendar = dev_calendar
        self.hub = hub
        self.status: dict[str, str] = {}  # account_id -> "ok" | "expired" | "error"
        self._token_cache = token_cache or google_oauth.token_cache
        self._token_fn = token_fn or (lambda s, a: google_oauth.access_token(s, a, self._token_cache))
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._interval = interval
        self._cache_path = Path(store.data_dir) / CACHE_NAME
        self._accounts: dict[str, dict] = {}  # account_id -> {"fetched_at": iso, "events": [EventJSON]}
        self._client: httpx.AsyncClient | None = None
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        # What the cache file on disk holds, to skip rewrites (SD card wear): its content without
        # fetched_at, and each account's on-disk fetched_at.
        self._saved_content: dict | None = None
        self._saved_fetched: dict[str, str] = {}

    # ---- lifecycle ----

    async def start(self) -> None:
        if self.dev_calendar is not None:
            log.warning("dev calendar mode: publishing %s instead of Google Calendar", self.dev_calendar)
            await self._publish_dev()
            self._task = asyncio.create_task(self._run(), name="gcal-poll")
            return
        try:
            self._load_cache()
            self.status = {a.id: "error" for a in self.store.config.google_accounts}  # until the first fetch
            await self._publish()
        except Exception as e:  # a disk or data fault must not fail the app lifespan
            log.error("initial calendar publish failed (%s); polling anyway", type(e).__name__)
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
        self._accounts = {}
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
            accounts = raw["accounts"]
            configured = {a.id for a in self.store.config.google_accounts}
            for account_id, entry in accounts.items():
                # the old flat format ("events") is discarded
                if account_id not in configured or not isinstance(entry.get("calendars"), dict):
                    continue
                calendars = {
                    str(cal): [e for e in events if _valid_event(e)]
                    for cal, events in entry["calendars"].items()
                    if isinstance(events, list)
                }
                self._accounts[account_id] = {"fetched_at": str(entry["fetched_at"]), "calendars": calendars}
            range_ = (str(raw.get("range_start")), str(raw.get("range_end")))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self._accounts = {}
            return
        self._remember_saved(self._cache_content(*range_))
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

    def _cache_content(self, range_start: str, range_end: str) -> dict:
        """The cache file's content minus fetched_at (deep-copied via JSON)."""
        accounts = {a: e["calendars"] for a, e in self._accounts.items()}
        return json.loads(json.dumps({"accounts": accounts, "range": [range_start, range_end]}))

    def _remember_saved(self, content: dict) -> None:
        self._saved_content = content
        self._saved_fetched = {a: e["fetched_at"] for a, e in self._accounts.items()}

    def _disk_fresh(self) -> bool:
        """Is every on-disk fetched_at under CACHE_REFRESH old? (Keeps the 24 h expiry honest
        after a restart.)"""
        now = self._now()
        for fetched in self._saved_fetched.values():
            try:
                if now - datetime.fromisoformat(fetched) >= CACHE_REFRESH:
                    return False
            except (ValueError, TypeError):
                return False
        return True

    def _save_cache(self, first: date, last: date) -> None:
        content = self._cache_content(first.isoformat(), last.isoformat())
        if content == self._saved_content and self._disk_fresh():
            return  # same events and the file's fetched_at is recent enough
        data = {
            "accounts": self._accounts,
            "range_start": first.isoformat(),
            "range_end": last.isoformat(),
        }
        tmp = self._cache_path.with_name(CACHE_NAME + ".tmp")
        try:
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self._cache_path)
            self._remember_saved(content)
        except OSError as e:
            log.warning("could not write calendar cache (%s)", type(e).__name__)

    # ---- polling ----

    def _tz(self) -> ZoneInfo:
        return ZoneInfo(self.store.config.timezone)

    def _range(self) -> tuple[date, date]:
        return display_range(self._now().astimezone(self._tz()).date())

    async def poll_once(self) -> None:
        if self.dev_calendar is not None:
            await self._publish_dev()
            return
        cfg = self.store.config
        tz = self._tz()
        first, last = self._range()
        ids = [a.id for a in cfg.google_accounts]
        for gone in set(self._accounts) - set(ids):
            del self._accounts[gone]
        self.status = {i: self.status.get(i, "error") for i in ids}
        for account_id in ids:
            try:
                calendars = await self._fetch_account(account_id, first, last, tz)
            except Exception as e:  # GoogleError or anything else: never let one account stop the others
                self._record_failure(account_id, e)
            else:
                if account_id not in {a.id for a in self.store.config.google_accounts}:
                    continue  # deleted while fetching
                self.status[account_id] = "ok"
                self._accounts[account_id] = {"fetched_at": self._now().isoformat(), "calendars": calendars}
        current = {a.id for a in self.store.config.google_accounts}
        self.status = {k: v for k, v in self.status.items() if k in current}
        self._expire()
        self._save_cache(first, last)
        await self._publish()

    def _record_failure(self, account_id: str, exc: Exception) -> None:
        # "expired" needs the owner to sign in again; "error" (network, Google down) retries by itself.
        self.status[account_id] = "expired" if isinstance(exc, GoogleAuthExpired) else "error"
        log.warning("google calendar fetch failed for account %s (%s)", account_id, type(exc).__name__)

    async def _fetch_account(self, account_id: str, first: date, last: date, tz: ZoneInfo) -> dict[str, list[dict]]:
        cfg = self.store.config
        members = {m.id: m for m in cfg.members}
        params = {
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "2500",
            "timeMin": _local_midnight(first, tz),
            "timeMax": _local_midnight(last + timedelta(days=1), tz),
        }
        out: dict[str, list[dict]] = {}
        for ref in cfg.calendars:
            member = members.get(ref.member_id)
            if ref.account_id != account_id or not ref.enabled or member is None:
                continue
            url = f"{API}/calendars/{quote(ref.calendar_id, safe='')}/events"
            events = out.setdefault(ref.calendar_id, [])
            async for item in self._paged(account_id, url, params):
                event = normalize_google_event(item, member, tz)
                if event is not None:
                    events.append(to_json(event))
        return out

    async def _publish_dev(self) -> None:
        tz = self._tz()
        first, last = self._range()
        try:
            spec = json.loads(Path(self.dev_calendar).read_text(encoding="utf-8"))
            events = expand_dev_calendar(spec, self._now().astimezone(tz).date(), tz, self.store.config.members)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            log.warning("dev calendar unreadable (%s)", type(e).__name__)
            events = []
        events = [e for e in events if _in_range(e, first, last, tz)]
        events.sort(key=lambda e: (e["start"], e["title"]))
        await self.hub.publish({"type": "calendar", "calendar": self._payload(first, last, events)})
        await self.hub.set_status("google", {})

    def _payload(self, first: date, last: date, events: list[dict]) -> dict:
        # Holidays are recomputed on every publish, so a language change shows after the next one.
        return {
            "range_start": first.isoformat(),
            "range_end": last.isoformat(),
            "events": events,
            "holidays": holidays_for_range(first, last, self.store.config.language),
        }

    async def _publish(self) -> None:
        first, last = self._range()
        tz = self._tz()
        cfg = self.store.config
        members = {m.id: m for m in cfg.members}
        events = []
        for ref in cfg.calendars:
            member = members.get(ref.member_id)
            entry = self._accounts.get(ref.account_id)
            if not ref.enabled or member is None or entry is None:
                continue
            for e in entry["calendars"].get(ref.calendar_id, []):
                if _in_range(e, first, last, tz):
                    events.append({**e, "member_id": member.id, "color": member.color})
        events.sort(key=lambda e: (e["start"], e["title"]))
        await self.hub.publish({"type": "calendar", "calendar": self._payload(first, last, events)})
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
        if resp.status_code == 401:
            self._token_cache.invalidate(account_id)
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
        seen: set[str] = set()
        for _ in range(MAX_PAGES):
            q = {**params, "pageToken": page} if page else params
            body = await self._get(account_id, url, q)
            for item in body.get("items") or []:
                yield item
            page = body.get("nextPageToken")
            if not page:
                return
            if page in seen:
                raise GoogleError("Google returned a repeated page token")
            seen.add(page)
        raise GoogleError("Google returned too many pages")

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

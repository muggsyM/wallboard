import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from wallboard import auth, google_oauth
from wallboard.app import create_app
from wallboard.config import CalendarRef, GoogleAccount, Member
from wallboard.gcal import CACHE_NAME, GoogleCalendarService
from wallboard.google_oauth import GoogleAuthExpired, GoogleError
from wallboard.hub import Hub

API = "https://www.googleapis.com/calendar/v3"
NOW = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)


def ev(id, summary, day="2026-11-10", end_day="2026-11-11"):
    return {"id": id, "summary": summary, "start": {"date": day}, "end": {"date": end_day}}


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


class Env:
    def __init__(self, data_dir):
        self.app = create_app(data_dir)
        self.store = self.app.state.store
        self.hub = self.app.state.hub
        self.clock = Clock()
        self.tokens = []  # account ids token_fn was asked for
        self.fail = {}  # account_id -> exception to raise

        self.store.save_config(
            self.store.config.model_copy(
                update={
                    "members": [
                        Member(id="anna", name="Anna", initial="A", color="#111111"),
                        Member(id="bob", name="Bob", initial="B", color="#222222"),
                    ],
                    "google_accounts": [
                        GoogleAccount(id="A", email="a@x.cz", member_id="anna"),
                        GoogleAccount(id="B", email="b@x.cz", member_id="bob"),
                    ],
                    "calendars": [
                        CalendarRef(account_id="A", calendar_id="a@x.cz", name="A", member_id="anna"),
                        CalendarRef(account_id="B", calendar_id="b@x.cz", name="B", member_id="bob"),
                    ],
                }
            )
        )

    async def token_fn(self, secrets, account_id):
        self.tokens.append(account_id)
        if account_id in self.fail:
            raise self.fail[account_id]
        return f"tok-{account_id}-{len(self.tokens)}"

    def service(self, hub=None, **kw):
        return GoogleCalendarService(
            self.store,
            hub or self.hub,
            token_fn=self.token_fn,
            now=self.clock,
            token_cache=google_oauth.TokenCache(),
            **kw,
        )

    def published(self, hub=None):
        return (hub or self.hub).snapshot()["calendar"]

    def titles(self, hub=None):
        return [e["title"] for e in self.published(hub)["events"]]

    def status(self, hub=None):
        return (hub or self.hub).snapshot()["status"]["google"]

    def only_calendar_a(self):
        self.store.save_config(self.store.config.model_copy(update={"calendars": self.store.config.calendars[:1]}))


@pytest.fixture
def env(data_dir):
    return Env(data_dir)


def mock_events(cal, items):
    return respx.get(f"{API}/calendars/{cal.replace('@', '%40')}/events").mock(
        return_value=httpx.Response(200, json={"items": items})
    )


@respx.mock
async def test_merges_accounts_and_colors(env):
    mock_events("a@x.cz", [ev("1", "Zubar")])
    mock_events("b@x.cz", [ev("2", "Anglictina", day="2026-11-09", end_day="2026-11-10")])
    svc = env.service()
    await svc.poll_once()
    p = env.published()
    assert (p["range_start"], p["range_end"]) == ("2026-10-26", "2026-12-06")
    assert p["holidays"] == {
        "2026-10-28": "Den vzniku samostatného československého státu",
        "2026-11-17": "Den boje za svobodu a demokracii",
    }
    assert [(e["title"], e["member_id"], e["color"]) for e in p["events"]] == [
        ("Anglictina", "bob", "#222222"),
        ("Zubar", "anna", "#111111"),
    ]
    assert env.status() == {"A": True, "B": True}
    assert svc.status == {"A": True, "B": True}
    await svc.stop()


@respx.mock
async def test_paging(env):
    env.only_calendar_a()
    route = respx.get(f"{API}/calendars/a%40x.cz/events").mock(
        side_effect=[
            httpx.Response(200, json={"items": [ev("1", "P1")], "nextPageToken": "tok2"}),
            httpx.Response(200, json={"items": [ev("2", "P2")]}),
        ]
    )
    svc = env.service()
    await svc.poll_once()
    assert env.titles() == ["P1", "P2"]
    assert route.calls[0].request.url.params.get("pageToken") is None
    assert route.calls[1].request.url.params["pageToken"] == "tok2"
    q = route.calls[0].request.url.params
    assert q["singleEvents"] == "true" and q["orderBy"] == "startTime" and q["maxResults"] == "2500"
    assert route.calls[0].request.headers["authorization"].startswith("Bearer tok-A")
    await svc.stop()


@respx.mock
async def test_time_range_is_prague_local_midnight(env):
    route = mock_events("a@x.cz", [])
    mock_events("b@x.cz", [])
    svc = env.service()
    await svc.poll_once()
    q = route.calls[0].request.url.params
    assert q["timeMin"] == "2026-10-26T00:00:00+01:00"
    assert q["timeMax"] == "2026-12-07T00:00:00+01:00"
    # summer: DST offset +02:00
    env.clock.t = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
    await svc.poll_once()
    q = route.calls[-1].request.url.params
    assert q["timeMin"] == "2026-06-29T00:00:00+02:00"
    assert q["timeMax"] == "2026-08-03T00:00:00+02:00"
    await svc.stop()


@respx.mock
async def test_one_account_expired_keeps_others(env):
    mock_events("a@x.cz", [ev("1", "A-event")])
    mock_events("b@x.cz", [ev("2", "B-event")])
    svc = env.service()
    await svc.poll_once()
    assert env.titles() == ["A-event", "B-event"]

    env.fail["A"] = GoogleAuthExpired("A")
    env.clock.t = NOW + timedelta(hours=23, minutes=59)
    await svc.poll_once()
    assert env.status() == {"A": False, "B": True}
    assert env.titles() == ["A-event", "B-event"]  # kept from cache, under 24h

    env.clock.t = NOW + timedelta(hours=24, minutes=1)
    await svc.poll_once()
    assert env.status() == {"A": False, "B": True}
    assert env.titles() == ["B-event"]  # older than 24h: dropped
    cached = json.loads((env.store.data_dir / CACHE_NAME).read_text())
    assert set(cached["accounts"]) == {"B"}

    # stays False until a fetch succeeds, then recovers
    await svc.poll_once()
    assert env.status()["A"] is False
    del env.fail["A"]
    await svc.poll_once()
    assert env.status() == {"A": True, "B": True}
    assert env.titles() == ["A-event", "B-event"]
    await svc.stop()


@respx.mock
async def test_plain_google_error_and_network_error_set_status_false(env, caplog):
    mock_events("a@x.cz", [ev("1", "A-event")])
    respx.get(f"{API}/calendars/b%40x.cz/events").mock(side_effect=httpx.ConnectTimeout("boom"))
    svc = env.service()
    with caplog.at_level("WARNING"):
        await svc.poll_once()
    assert env.status() == {"A": True, "B": False}
    assert env.titles() == ["A-event"]
    assert any("B" in r.getMessage() and "GoogleError" in r.getMessage() for r in caplog.records)
    assert not any("tok-" in r.getMessage() for r in caplog.records)
    env.fail["A"] = GoogleError("Google client not configured")
    await svc.poll_once()
    assert env.status() == {"A": False, "B": False}
    await svc.stop()


@respx.mock
async def test_401_invalidates_token_and_retries_once(env):
    env.only_calendar_a()
    route = respx.get(f"{API}/calendars/a%40x.cz/events").mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json={"items": [ev("1", "Retried")]})]
    )
    svc = env.service()
    await svc.poll_once()
    assert env.titles() == ["Retried"]
    assert env.status()["A"] is True
    assert route.calls[0].request.headers["authorization"] != route.calls[1].request.headers["authorization"]

    route.mock(side_effect=[httpx.Response(401), httpx.Response(401)])
    await svc.poll_once()
    assert len(route.calls) == 4
    assert env.status()["A"] is False
    await svc.stop()


@respx.mock
async def test_deleted_account_removed_from_status_and_cache(env):
    mock_events("a@x.cz", [ev("1", "A-event")])
    mock_events("b@x.cz", [ev("2", "B-event")])
    svc = env.service()
    await svc.poll_once()
    cfg = env.store.config
    env.store.save_config(
        cfg.model_copy(
            update={
                "google_accounts": [a for a in cfg.google_accounts if a.id != "A"],
                "calendars": [c for c in cfg.calendars if c.account_id != "A"],
            }
        )
    )
    await svc.poll_once()
    assert env.status() == {"B": True}
    assert env.titles() == ["B-event"]
    assert set(json.loads((env.store.data_dir / CACHE_NAME).read_text())["accounts"]) == {"B"}
    await svc.stop()


@respx.mock
async def test_cache_written_and_loaded_on_start(env):
    mock_events("a@x.cz", [ev("1", "A-event")])
    mock_events("b@x.cz", [ev("2", "B-event")])
    await env.service().poll_once()
    assert (env.store.data_dir / CACHE_NAME).exists()

    respx.clear()  # no network: every request raises
    hub2 = Hub()
    svc = env.service(hub=hub2)
    await svc.start()
    try:
        assert env.titles(hub2) == ["A-event", "B-event"]
        assert env.status(hub2) == {"A": False, "B": False}
    finally:
        await svc.stop()


@respx.mock
async def test_disabled_calendar_ignored(env):
    cfg = env.store.config
    env.store.save_config(
        cfg.model_copy(
            update={
                "calendars": [
                    cfg.calendars[0],
                    CalendarRef(account_id="A", calendar_id="off@x.cz", name="Off", member_id="anna", enabled=False),
                ]
            }
        )
    )
    mock_events("a@x.cz", [ev("1", "On")])
    off = mock_events("off@x.cz", [ev("9", "Off")])
    mock_events("b@x.cz", [])
    svc = env.service()
    await svc.poll_once()
    assert env.titles() == ["On"]
    assert not off.called
    await svc.stop()


@respx.mock
async def test_events_outside_display_range_filtered(env):
    mock_events("a@x.cz", [ev("1", "In"), ev("2", "Old", day="2026-10-01", end_day="2026-10-02")])
    mock_events("b@x.cz", [])
    svc = env.service()
    await svc.poll_once()
    assert env.titles() == ["In"]
    await svc.stop()


@respx.mock
async def test_no_accounts_idles_and_publishes_empty(env):
    env.store.save_config(env.store.config.model_copy(update={"google_accounts": [], "calendars": []}))
    svc = env.service()
    await svc.start()
    await asyncio.sleep(0.05)
    assert env.published()["events"] == []
    assert env.status() == {}
    await svc.stop()


@respx.mock
async def test_refresh_now_wakes_loop(env):
    route = mock_events("a@x.cz", [])
    mock_events("b@x.cz", [])
    svc = env.service(interval=3600)
    await svc.start()

    async def wait_calls(n):
        for _ in range(100):
            if route.call_count >= n:
                return
            await asyncio.sleep(0.01)

    await wait_calls(1)
    assert route.call_count == 1
    svc.refresh_now()
    await wait_calls(2)
    assert route.call_count == 2
    await svc.stop()


@respx.mock
async def test_list_calendars_pages_and_names(env):
    route = respx.get(f"{API}/users/me/calendarList").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "items": [{"id": "a@x.cz", "summary": "Anna", "summaryOverride": "Moje", "primary": True}],
                    "nextPageToken": "n",
                },
            ),
            httpx.Response(200, json={"items": [{"id": "fam", "summary": "Family"}]}),
        ]
    )
    svc = env.service()
    assert await svc.list_calendars("A") == [
        {"id": "a@x.cz", "name": "Moje", "primary": True},
        {"id": "fam", "name": "Family", "primary": False},
    ]
    assert route.calls[1].request.url.params["pageToken"] == "n"
    await svc.stop()


def admin_client(env):
    c = TestClient(env.app)
    c.cookies.set(auth.COOKIE_NAME, auth.new_session_cookie(env.store.secrets.session_secret))
    return c


@respx.mock
def test_calendars_route(env):
    env.app.state.gcal = env.service()
    route = respx.get(f"{API}/users/me/calendarList").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "x", "summary": "X"}]})
    )
    c = admin_client(env)
    assert c.get("/api/admin/google/A/calendars").json() == [{"id": "x", "name": "X", "primary": False}]
    assert c.get("/api/admin/google/ZZ/calendars").status_code == 404
    env.fail["A"] = GoogleAuthExpired("A")
    r = c.get("/api/admin/google/A/calendars")
    assert (r.status_code, r.json()) == (409, {"detail": "sign-in expired"})
    del env.fail["A"]
    route.mock(return_value=httpx.Response(500))
    r = c.get("/api/admin/google/A/calendars")
    assert r.status_code == 502 and "tok-" not in r.text


@respx.mock
async def test_disabled_calendar_dropped_from_kept_cache(env):
    mock_events("a@x.cz", [ev("1", "A-event")])
    mock_events("b@x.cz", [ev("2", "B-event")])
    svc = env.service()
    await svc.poll_once()
    cfg = env.store.config
    env.store.save_config(
        cfg.model_copy(
            update={
                "calendars": [
                    cfg.calendars[0].model_copy(update={"enabled": False}),
                    cfg.calendars[1],
                    CalendarRef(account_id="A", calendar_id="a2@x.cz", name="A2", member_id="anna"),
                ]
            }
        )
    )
    env.fail["A"] = GoogleAuthExpired("A")
    await svc.poll_once()
    assert env.status()["A"] is False
    assert env.titles() == ["B-event"]
    await svc.stop()


@respx.mock
async def test_member_color_change_applies_without_fetch(env):
    route = mock_events("a@x.cz", [ev("1", "A-event")])
    mock_events("b@x.cz", [])
    svc = env.service()
    await svc.poll_once()
    cfg = env.store.config
    env.store.save_config(
        cfg.model_copy(update={"members": [cfg.members[0].model_copy(update={"color": "#abcdef"}), cfg.members[1]]})
    )
    calls = route.call_count
    await svc._publish()
    assert route.call_count == calls
    assert env.published()["events"][0]["color"] == "#abcdef"
    await svc.stop()


@respx.mock
async def test_cached_account_not_in_config_not_published_on_start(env):
    mock_events("a@x.cz", [ev("1", "A-event")])
    mock_events("b@x.cz", [ev("2", "B-event")])
    await env.service().poll_once()
    cfg = env.store.config
    env.store.save_config(
        cfg.model_copy(
            update={
                "google_accounts": cfg.google_accounts[1:],
                "calendars": cfg.calendars[1:],
            }
        )
    )
    hub2 = Hub()
    svc = env.service(hub=hub2)
    await svc.start()
    try:
        assert env.titles(hub2) == ["B-event"]
        assert env.status(hub2) == {"B": False}
    finally:
        await svc.stop()


@respx.mock
async def test_repeated_page_token_and_page_cap(env):
    env.only_calendar_a()
    respx.get(f"{API}/calendars/a%40x.cz/events").mock(
        return_value=httpx.Response(200, json={"items": [], "nextPageToken": "same"})
    )
    svc = env.service()
    await svc.poll_once()
    assert env.status()["A"] is False
    counter = iter(range(1000))
    respx.get(f"{API}/calendars/a%40x.cz/events").mock(
        side_effect=lambda req: httpx.Response(200, json={"items": [], "nextPageToken": f"t{next(counter)}"})
    )
    await svc.poll_once()
    assert env.status()["A"] is False
    assert next(counter) <= 51
    await svc.stop()


@pytest.mark.parametrize("content", ["not json", "[]", '{"accounts": 5}', '{"accounts": {"A": 3}}'])
async def test_garbage_cache_does_not_crash_start(env, content):
    (env.store.data_dir / CACHE_NAME).write_text(content)
    svc = env.service()
    await svc.start()
    try:
        assert env.published()["events"] == []
    finally:
        await svc.stop()


async def test_bad_cached_events_dropped_on_load(env):
    good = {
        "id": "1", "title": "Good", "location": None, "start": "2026-11-10", "end": "2026-11-11",
        "all_day": True, "member_id": "anna", "color": "#111111",
    }
    data = {
        "accounts": {
            "A": {
                "fetched_at": NOW.isoformat(),
                "calendars": {"a@x.cz": [good, "junk", {"id": "2", "title": "no start"}]},
            }
        }
    }
    (env.store.data_dir / CACHE_NAME).write_text(json.dumps(data))
    svc = env.service()
    await svc.start()
    try:
        assert env.titles() == ["Good"]
    finally:
        await svc.stop()


@respx.mock
async def test_account_deleted_mid_fetch_not_stored(env):
    env.only_calendar_a()

    def handler(request):
        cfg = env.store.config
        env.store.save_config(cfg.model_copy(update={"google_accounts": cfg.google_accounts[1:], "calendars": []}))
        return httpx.Response(200, json={"items": [ev("1", "Ghost")]})

    respx.get(f"{API}/calendars/a%40x.cz/events").mock(side_effect=handler)
    svc = env.service()
    await svc.poll_once()
    assert "A" not in svc._accounts and "A" not in svc.status
    assert env.titles() == []
    await svc.stop()


@respx.mock
async def test_no_cache_rewrite_when_unchanged(env):
    env.store.save_config(env.store.config.model_copy(update={"google_accounts": [], "calendars": []}))
    svc = env.service()
    await svc.poll_once()
    path = env.store.data_dir / CACHE_NAME
    before = path.stat().st_mtime_ns
    await svc.poll_once()
    assert path.stat().st_mtime_ns == before
    await svc.stop()


@respx.mock
async def test_second_401_invalidates_token_cache(env):
    env.only_calendar_a()
    respx.get(f"{API}/calendars/a%40x.cz/events").mock(return_value=httpx.Response(401))
    cache = google_oauth.TokenCache()
    svc = GoogleCalendarService(env.store, env.hub, token_fn=env.token_fn, now=env.clock, token_cache=cache)
    cache.put("A", "stale", 1e12)
    await svc.poll_once()
    assert cache.get("A") is None
    await svc.stop()


def test_calendars_route_requires_admin(env):
    r = TestClient(env.app, follow_redirects=False).get("/api/admin/google/A/calendars")
    assert r.status_code in (401, 303, 403)


@respx.mock
async def test_payload_includes_holidays(env):
    mock_events("a@x.cz", [])
    mock_events("b@x.cz", [])
    svc = env.service()
    await svc.poll_once()
    holidays = env.published()["holidays"]
    assert holidays == {
        "2026-10-28": "Den vzniku samostatného československého státu",
        "2026-11-17": "Den boje za svobodu a demokracii",
    }
    await svc.stop()


@respx.mock
async def test_holidays_span_two_years(env):
    mock_events("a@x.cz", [])
    mock_events("b@x.cz", [])
    env.clock.t = datetime(2026, 12, 30, 10, 0, tzinfo=timezone.utc)
    svc = env.service()
    await svc.poll_once()
    p = env.published()
    assert (p["range_start"], p["range_end"]) == ("2026-11-30", "2027-01-05")
    assert list(p["holidays"]) == ["2026-12-24", "2026-12-25", "2026-12-26", "2027-01-01"]
    assert p["holidays"]["2027-01-01"] == "Den obnovy samostatného českého státu, Nový rok"
    await svc.stop()


@respx.mock
async def test_holidays_follow_language_on_next_publish(env):
    mock_events("a@x.cz", [])
    mock_events("b@x.cz", [])
    svc = env.service()
    await svc.poll_once()
    assert env.published()["holidays"]["2026-11-17"] == "Den boje za svobodu a demokracii"
    env.store.save_config(env.store.config.model_copy(update={"language": "en"}))
    await svc.poll_once()
    assert env.published()["holidays"] == {"2026-10-28": "Independence Day", "2026-11-17": "Freedom and Democracy Day"}
    await svc.stop()

# Family Wallboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A self-hosted portrait wall display on a Raspberry Pi 4 touchscreen showing rotating Today / Next 7 days / Month / Home pages from per-person Google calendars, uploaded photos and Home Assistant (with tap control), configured from a password-protected browser Settings page, in Czech or English.

**Architecture:** One Python FastAPI server (systemd service) holds all secrets, talks to Google Calendar (REST, one refresh token per family member) and Home Assistant (WebSocket), stores photos and config under `data/`, and pushes everything to the kiosk display over a loopback-only WebSocket. The display and admin UIs are plain HTML/CSS/ES-module JS with no build step; each display page type is one module registered in `pages/registry.js`.

**Tech Stack:** Python ≥ 3.11, FastAPI, uvicorn[standard], pydantic v2, httpx, websockets ≥ 13, Pillow, pillow-heif, python-multipart; pytest, pytest-asyncio, respx. Front end: vanilla JS (ES modules), Outfit font (Google Fonts), @mdi/font 7.4.47. JS tests: `node --test` (Node ≥ 20, dev machine only).

**Spec:** `docs/superpowers/specs/2026-11-07-family-wallboard-design.md` (visual reference: `docs/superpowers/specs/mockups/portrait-v1.html`, `mockups/month-v2.html`).

## Global Constraints

- All code lives under `wallboard/`; do not modify `www/dakboard/` or `custom_components/dakboard/`.
- Python ≥ 3.11 (Raspberry Pi OS Bookworm ships 3.11). No Docker.
- Server listens on `0.0.0.0:8080`. Display URL: `http://localhost:8080/`.
- Secrets (HA token, Google client secret, refresh tokens, admin password hash, session secret) never leave the server: never in any HTTP response, WebSocket message or log line.
- Display endpoints (`/`, `/ws`, display static files) accept loopback clients only (`127.0.0.1`, `::1`); everything else under `/settings`, `/photos`, `/api/admin/*` requires the admin session cookie.
- HA service calls only for entities listed in `config.tiles` with `controllable: true`, using the fixed per-domain map in Task 5. No other service calls from the display path.
- Portrait 1080×1920 target; all display sizes in `cqw`/`vw` relative units. No CSS `backdrop-filter` and no CSS `filter: blur` on the display (Pi 4 GPU budget).
- Display language `cs` (default) or `en`; 24-hour time and Monday-first weeks in both; dates via `Intl` with `cs-CZ` / `en-GB`.
- Timezone: `Europe/Prague` (config field `timezone`, default `"Europe/Prague"`).
- Visual style per mockups: Outfit (weights 200–600), MDI icons, dark translucent panels, person colours on events.
- Commit after every task with message prefix `wallboard:`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Multi-day and all-day events across day boundaries and DST** (e.g. ski trip Fri 20 – Sun 22, an event 23:00–01:00, the 25 Oct DST change): each must appear on every day it covers, all-day end dates are exclusive in Google. Tests in Task 7.
2. **A Google refresh token gets revoked/expired for one member** while the others still work: that member's events disappear only after the cache ages out, Settings shows "sign-in expired" for that account, other members keep updating. Tests in Task 9.
3. **HA unreachable or token wrong** at startup and mid-run: server keeps running, reconnects with backoff (1 s → 60 s), display keeps last states dimmed with red dot. Tests in Task 4 and Task 13.
4. **Tap on an entity not on the allowlist, or sent from a non-loopback client:** rejected, no HA call. Tests in Task 5 and Task 3.
5. **Odd uploads:** iPhone HEIC, a 40 MB file, a PNG with transparency, a non-image renamed `.jpg`, EXIF orientation 6/8: processed upright or rejected with a clear message; nothing half-written left on disk. Tests in Task 10.

---

## File map

```
wallboard/
  pyproject.toml
  wallboard/
    __main__.py          CLI: `serve`, `set-password`
    app.py               create_app(data_dir) – wires routers, services, static files
    config.py            pydantic models Config, Secrets; ConfigStore (atomic JSON, secrets 0600)
    auth.py              password hashing, session cookie, require_admin / require_loopback deps
    hub.py               Hub: latest state per topic + broadcast to display sockets
    ha_client.py         HAClient: WS auth, subscribe_entities, call_service, forecasts, reconnect
    actions.py           resolve_action(): allowlist + per-domain service map
    google_oauth.py      auth URL, code exchange, token refresh
    gcal.py              GoogleCalendarService: list calendars, fetch events, poll, cache
    events.py            Event model, normalize_google_event(), events_for_range(), bucket_by_day()
    holidays_cz.py       czech_holidays(year)
    imaging.py           process_image() → display, background, thumbnail
    photos.py            PhotoStore: albums, order, ingest, delete, zip
    routes/
      display.py         GET /, WS /ws, GET /media/*
      admin.py           login/logout, settings API, HA test, google connect
      photos_api.py      photo upload/list/reorder/delete/zip
  web/
    shared/ha-format.js  describe(entity, state) + icon maps (extracted from www/dakboard/index.html)
    shared/i18n.js       t(key), fmt helpers; strings in shared/i18n/{cs,en}.json
    display/index.html, display.css, shell.js, store.js, header.js, photo.js
    display/pages/registry.js, today.js, week.js, month.js, home.js
    display/lib/layout.js  pure calendar layout helpers for pages (tested with node)
    admin/login.html, settings.html, settings.js, photos.html, photos.js, admin.css
  deploy/install.sh, wallboard.service, labwc-autostart, README.md
  tests/  (pytest)   web/tests/ (node --test)
dev/mock_ha.py        extended in Task 4 (subscribe_entities with entity_ids, call_service, get_forecasts)
```

## Data model (fixed; used across tasks)

`data/config.json` ← `Config`; `data/secrets.json` (mode 0600) ← `Secrets`; `data/calendar_cache.json`; `data/photos/` + `data/photos.json`.

```python
class Member(BaseModel):     id: str; name: str; initial: str; color: str  # "#4f8cff"
                             person_entity: str | None = None
class GoogleAccount(BaseModel): id: str; email: str; member_id: str
class CalendarRef(BaseModel):  account_id: str; calendar_id: str; name: str; member_id: str; enabled: bool = True
class Room(BaseModel):       name: str; temperature_entity: str; humidity_entity: str | None = None
                             light_entities: list[str] = []
class Tile(BaseModel):       entity_id: str; label: str | None = None; icon: str | None = None
                             group: Literal["lights", "devices"]; controllable: bool = False
class PageRef(BaseModel):    type: str; enabled: bool = True; seconds: int | None = None
                             options: dict[str, Any] = {}
class Config(BaseModel):
    language: Literal["cs", "en"] = "cs"; timezone: str = "Europe/Prague"
    rotation_seconds: int = 30; touch_pause_seconds: int = 120
    night: NightMode = NightMode()          # enabled=False, start="23:00", end="06:00"
    members: list[Member] = []; google_accounts: list[GoogleAccount] = []
    calendars: list[CalendarRef] = []
    ha_url: str | None = None; weather_entity: str | None = None
    rooms: list[Room] = []; tiles: list[Tile] = []; status_entities: list[str] = []
    quick_tiles: list[str] = []             # ≤ 4 entity_ids, each must be in tiles
    pages: list[PageRef] = [PageRef(type="today"), PageRef(type="week"),
                            PageRef(type="month"), PageRef(type="home")]
    photos_shuffle: bool = False
class Secrets(BaseModel):
    admin_password_hash: str | None = None; session_secret: str   # generated on first load
    ha_token: str | None = None; google_client_id: str | None = None
    google_client_secret: str | None = None; google_refresh_tokens: dict[str, str] = {}  # account_id → token
```

Display WebSocket messages (server → display), JSON:

```
{"type":"snapshot","config":PublicConfig,"ha":{entity_id: HAState},"calendar":CalendarPayload,
 "weather":WeatherPayload|null,"photos":[PhotoItem],"status":{"ha":bool,"google":{account_id:bool}}}
{"type":"ha","entity_id":str,"state":HAState|null}
{"type":"calendar","calendar":CalendarPayload}   {"type":"weather","weather":WeatherPayload}
{"type":"photos","photos":[PhotoItem]}            {"type":"config","config":PublicConfig}
{"type":"status","status":{...}}                  {"type":"action_result","id":str,"ok":bool,"error":str|null}
display → server: {"type":"action","id":str,"entity_id":str}
```

`HAState = {"state":str,"attributes":dict,"last_changed":iso}` (attributes filtered with the same rule as `custom_components/dakboard/__init__.py`: drop names containing token/picture/password/secret and string values containing `token=`). `PublicConfig` = `Config` minus `google_accounts[].email`, plus `members`, `pages`, etc. `CalendarPayload = {"range_start":date,"range_end":date,"events":[EventJSON],"holidays":{iso_date: name}}` (holidays added in Task 15), `EventJSON = {"id","title","location","start","end","all_day","member_id","color"}` (start/end ISO; all-day as dates, end exclusive). `WeatherPayload = {"current":{"condition","temperature"},"daily":[{"date","condition","high","low","precip_probability"}]}`. `PhotoItem = {"id","url","bg_url","caption","landscape"}`.

---

## Milestone 1 – Server skeleton

### Task 1: Project, config store, CLI

**Files:** Create `wallboard/pyproject.toml`, `wallboard/wallboard/{__init__,__main__,config,app}.py`, `wallboard/tests/test_config.py`, `wallboard/tests/conftest.py`

**Interfaces – Produces:** `ConfigStore(data_dir: Path)` with `.config: Config`, `.secrets: Secrets`, `save_config(cfg: Config) -> None`, `save_secrets(s: Secrets) -> None`, `public_config() -> dict`; `create_app(data_dir: Path) -> FastAPI` (stores services on `app.state`); CLI `python -m wallboard serve --data DIR [--port 8080]`. Fixture `data_dir` (tmp_path) in conftest.

- [ ] **Step 1: Write failing tests** in `test_config.py`:
  - `test_defaults_created`: new store on empty dir → `config.language == "cs"`, four default pages in order today/week/month/home, `secrets.session_secret` is ≥ 32 chars, both files exist.
  - `test_secrets_file_mode`: `oct(stat(secrets.json).st_mode & 0o777) == "0o600"`.
  - `test_atomic_save_roundtrip`: save config with `language="en"`, reload new store → `"en"`; no `*.tmp` left.
  - `test_public_config_has_no_secrets`: set every secret field to `"S3CRET"` → `"S3CRET" not in json.dumps(store.public_config())`.
  - `test_quick_tiles_must_be_tiles`: `Config(quick_tiles=["light.x"], tiles=[])` raises `ValidationError`; more than 4 also raises.
- [ ] **Step 2:** `cd wallboard && python -m venv .venv && .venv/bin/pip install -e .[dev] && .venv/bin/pytest tests/test_config.py -v` → FAIL (import errors).
- [ ] **Step 3:** Implement models exactly as in *Data model*; atomic write = write `name.tmp` then `os.replace`; secrets file created with `os.open(..., 0o600)`; `session_secret = secrets.token_urlsafe(32)` on first load. `create_app` builds `ConfigStore` and returns a FastAPI app with a `GET /healthz → {"ok": true}`.
- [ ] **Step 4:** Run tests → PASS. `python -m wallboard serve --data /tmp/wb` then `curl localhost:8080/healthz` → `{"ok":true}`.
- [ ] **Step 5:** Commit `wallboard: project skeleton and config store`.

### Task 2: Admin auth

**Files:** Create `wallboard/wallboard/auth.py`, `wallboard/wallboard/routes/admin.py` (login/logout only for now), `wallboard/web/admin/login.html`, `wallboard/web/admin/admin.css`; modify `__main__.py`, `app.py`. Test `tests/test_auth.py`.

**Interfaces – Produces:** `hash_password(pw: str) -> str` (scrypt, `"scrypt$n$r$p$salt$hash"`), `verify_password(pw, stored) -> bool`; FastAPI deps `require_admin(request) -> None` (401 JSON for `/api/*`, 303 redirect to `/login?next=` for pages) and `require_loopback(request) -> None` (403). Cookie `wb_session` = `"<expiry>.<hmac-sha256(session_secret, expiry)>"`, 30 days, HttpOnly, SameSite=Strict. CLI `python -m wallboard set-password --data DIR` (prompts twice via getpass).

- [ ] **Step 1: Failing tests:** `test_hash_verify` (right/wrong password); `test_login_sets_cookie` (POST `/login` form `password=` correct → 303 + `wb_session` cookie; wrong → 401 and login page shows error; no cookie); `test_tampered_or_expired_cookie_rejected`; `test_admin_api_requires_cookie` (`GET /api/admin/config` → 401 without, 200 with); `test_require_loopback` (TestClient with `client=("192.168.1.20", 5000)` → 403 on `/`, `("127.0.0.1", 5000)` → 200). If no password is set, `/login` shows "Run `python -m wallboard set-password`" and refuses all logins.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. `GET /api/admin/config` returns `store.config` (no secrets) — the full settings API comes in Task 18. Use `hmac.compare_digest`.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `wallboard: admin password and session auth`.

### Task 3: Hub and display WebSocket

**Files:** Create `wallboard/wallboard/hub.py`, `wallboard/wallboard/routes/display.py`; test `tests/test_hub.py`.

**Interfaces – Produces:** `class Hub: async def connect(ws) ; def disconnect(ws); async def publish(msg: dict) ; set_topic(name: str, value) ; snapshot() -> dict ; on_action: Callable[[str, str], Awaitable[dict]] | None` (args `entity_id`, `action_id`; returns `action_result` message). Topics: `config, ha, calendar, weather, photos, status`; `publish` of `{"type":"ha",...}` also updates `ha[entity_id]` in the snapshot (`state: null` deletes). `GET /` serves `web/display/index.html`; `/static/display`, `/static/shared` mounted; all loopback-only. `WS /ws`: on connect send `snapshot`; receive `action` messages → `hub.on_action`, reply only to that socket.

- [ ] **Step 1: Failing tests:** `test_snapshot_on_connect` (set config/ha topics → first message is `snapshot` with them); `test_ha_delta_updates_snapshot` (publish ha delta → connected client receives it; a new client's snapshot contains it); `test_ws_rejects_non_loopback` (close code 4403); `test_action_routed` (fake `on_action` returns `{"type":"action_result","id":"1","ok":true,"error":null}`; client sends action → receives it); `test_dead_socket_dropped` (publish after a client vanished does not raise).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement (sockets in a set; send with `asyncio.gather(..., return_exceptions=True)`, drop failures). **Step 4:** PASS. **Step 5:** Commit `wallboard: display hub and websocket`.

## Milestone 2 – Home Assistant

### Task 4: HA client (+ mock HA)

**Files:** Create `wallboard/wallboard/ha_client.py`; modify `dev/mock_ha.py`; test `tests/test_ha_client.py` (runs mock HA in-process on a free port).

**Interfaces – Consumes:** `Hub.publish`, `Hub.set_topic`, `ConfigStore`. **Produces:** `class HAClient(store, hub)`: `async start()`, `async stop()`, `async restart()` (call after HA settings change), `wanted_entities() -> set[str]` (union of rooms' entities, tiles, status_entities, quick_tiles, members' person_entity, weather_entity), `async call_service(domain, service, entity_id) -> None` (raises `HAError` on HA error/timeout 5 s), `async get_forecast() -> WeatherPayload | None`, `connected: bool`, `filter_attributes(attrs: dict) -> dict`.

Mock HA additions: `subscribe_entities` honours `entity_ids`; `call_service` toggles light/switch state, vacuum `start`→`cleaning`, `return_to_base`→`docked`, and responds with an error for unknown entities; `call_service` with `return_response: true` for `weather.get_forecasts` returns 7 daily entries; `--token` option (default `dev-token`); entities include `light.kitchen`, `light.hallway`, `vacuum.robot`, `scene.good_night`, `weather.home`, `person.dad`, `sensor.living_temperature`, `sensor.living_humidity`, `binary_sensor.front_door`.

- [ ] **Step 1: Failing tests:** `test_subscribes_only_wanted` (snapshot `ha` contains exactly wanted entities present in mock); `test_state_change_published` (mock changes `light.kitchen` → hub receives `{"type":"ha","entity_id":"light.kitchen",...}` within 2 s); `test_attributes_filtered` (`entity_picture`, `access_token` dropped, value `"x?token=1"` dropped); `test_bad_token_sets_status_false_and_retries` (status topic `ha:false`; after fixing token + `restart()` → `true`); `test_reconnect_after_drop` (stop mock, status false, start mock → status true, snapshot refreshed); `test_call_service_unknown_entity_raises_HAError`; `test_forecast_payload_shape` (7 daily entries with keys `date, condition, high, low, precip_probability`).
- [ ] **Step 2:** FAIL. **Step 3:** Implement with `websockets` client: `auth` → `subscribe_entities` (compressed `a/c/r` diffs, as the existing widget handles) → publish deltas; backoff 1,2,4…60 s; forecast via `call_service` `weather.get_forecasts` `type: daily` `return_response: true`, refreshed every 30 min and on connect, published as `weather` topic. Map HA `temperature`/`templow`/`precipitation_probability`. **Step 4:** PASS. **Step 5:** Commit `wallboard: home assistant client`.

### Task 5: Action allowlist

**Files:** Create `wallboard/wallboard/actions.py`; modify `app.py` (wire `hub.on_action`); test `tests/test_actions.py`.

**Interfaces – Consumes:** `HAClient.call_service`, hub snapshot. **Produces:** `resolve_action(config: Config, entity_id: str, current_state: str | None) -> tuple[str, str]` raising `ActionNotAllowed`; `async handle_action(entity_id, action_id) -> dict` (action_result).

Fixed map: `light`, `switch`, `fan`, `input_boolean` → `(domain, "toggle")`; `vacuum` → `return_to_base` if state in `{"cleaning","returning"}` else `start`; `scene` → `("scene","turn_on")`; `script` → `("script","turn_on")`; `cover` → `close_cover` if state in `{"open","opening"}` else `open_cover`. Anything else → not allowed.

- [ ] **Step 1: Failing tests:** each mapping row; `test_not_in_tiles_rejected`; `test_tile_not_controllable_rejected`; `test_unsupported_domain_rejected` (`climate.x` controllable tile); `test_handle_action_reports_ha_error` (`ok: false, error: "<message>"`, never raises); `test_handle_action_calls_ha_once` (spy).
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. **Step 5:** Commit `wallboard: tap action allowlist`.

## Milestone 3 – Calendar

### Task 6: Czech holidays

**Files:** `wallboard/wallboard/holidays_cz.py`, `tests/test_holidays.py`.
**Produces:** `czech_holidays(year: int) -> dict[date, dict[str, str]]` → `{"cs": ..., "en": ...}` names. Fixed dates: 1 Jan (Den obnovy samostatného českého státu / Restoration Day of the Czech State; also Nový rok), 1 May (Svátek práce / Labour Day), 8 May (Den vítězství / Liberation Day), 5 Jul (Den slovanských věrozvěstů Cyrila a Metoděje / Saints Cyril and Methodius Day), 6 Jul (Den upálení mistra Jana Husa / Jan Hus Day), 28 Sep (Den české státnosti / Statehood Day), 28 Oct (Den vzniku samostatného československého státu / Independence Day), 17 Nov (Den boje za svobodu a demokracii / Freedom and Democracy Day), 24 Dec (Štědrý den / Christmas Eve), 25 Dec (1. svátek vánoční / Christmas Day), 26 Dec (2. svátek vánoční / St Stephen's Day); movable: Good Friday (Velký pátek), Easter Monday (Velikonoční pondělí) via the anonymous Gregorian algorithm.

- [ ] Tests: `test_2026_count_13` (13 entries); `test_easter_2026` (Good Friday 2026-04-03, Easter Monday 2026-04-06); `test_easter_2027` (2027-03-26, 2027-03-29); `test_nov_17_names`. Run → FAIL → implement → PASS → commit `wallboard: czech public holidays`.

### Task 7: Event model and day bucketing

**Files:** `wallboard/wallboard/events.py`, `tests/test_events.py`.
**Produces:** `@dataclass Event(id, title, location, start: datetime|date, end: datetime|date, all_day: bool, member_id, color)`; `normalize_google_event(item: dict, member: Member, tz: ZoneInfo) -> Event | None` (skips `status == "cancelled"`; title fallback `"(bez názvu)"`, kept as given by Google otherwise); `event_days(ev, tz) -> list[date]` (every local date the event covers; all-day `end` exclusive; a timed event ending exactly at 00:00 does not cover that day); `bucket_by_day(events, first: date, days: int, tz) -> dict[date, list[Event]]` (per day: all-day first, then by start time, then title); `display_range(today: date) -> tuple[date, date]` = (Monday on/before the 1st of today's month, max(Sunday on/after last day of month, today + 6 days)) inclusive; `to_json(ev) -> dict` (EventJSON).

- [ ] Tests (tz Europe/Prague): `test_all_day_end_exclusive` (2026-11-20 → 2026-11-23 covers 20, 21, 22); `test_overnight_timed` (23:00–01:00 covers both days); `test_ends_at_midnight` (22:00–00:00 covers one day); `test_dst_day` (event 2026-10-25 01:30–03:30 local covers only 25th); `test_utc_input_converted` (`"2026-11-07T08:00:00Z"` → 09:00 local); `test_sort_order`; `test_cancelled_skipped`; `test_display_range_nov_2026` (today 2026-11-07 → (2026-10-26, 2026-12-06)); `test_display_range_end_of_month` (today 2026-11-28 → end = 2026-12-06 since today+6 = 2026-12-04 < Sunday 2026-12-06). FAIL → implement → PASS → commit `wallboard: calendar event model`.

### Task 8: Google OAuth

**Files:** `wallboard/wallboard/google_oauth.py`; add routes to `routes/admin.py`; test `tests/test_google_oauth.py` (respx mocks `https://oauth2.googleapis.com/token`).

**Interfaces – Produces:** `REDIRECT_URI = "http://localhost:8080/api/google/callback"`; `SCOPES = ["https://www.googleapis.com/auth/calendar.readonly", "openid", "email"]`; `auth_url(client_id, state) -> str` (`access_type=offline`, `prompt=consent`, `include_granted_scopes=true`); `async exchange_code(secrets, code) -> tuple[str, str]` (refresh_token, email from id_token payload, no signature check needed — fetched directly over TLS from Google); `async access_token(secrets, account_id) -> str` (cached until 60 s before expiry; raises `GoogleAuthExpired` on `invalid_grant`).
Routes (admin): `POST /api/admin/google/start {member_id}` → `{"url": ...}` with `state` = signed `member_id` + nonce (10 min); `GET /api/google/callback?code&state` (admin cookie **or** loopback) → stores token, creates/updates `GoogleAccount` for that member, redirects to `/settings#google`; `POST /api/admin/google/paste {url}` → parses `code`/`state` from a pasted `http://localhost:8080/api/google/callback?...` URL and does the same (for sign-in done on a phone, where localhost does not resolve); `DELETE /api/admin/google/{account_id}` removes account, its calendars and token.

- [ ] Tests: `test_auth_url_params`; `test_exchange_stores_token_and_account`; `test_paste_url_flow`; `test_bad_state_rejected`; `test_invalid_grant_raises_expired`; `test_access_token_cached`. FAIL → implement → PASS → commit `wallboard: google sign-in per family member`.

### Task 9: Calendar service

**Files:** `wallboard/wallboard/gcal.py`; wire in `app.py`; add `GET /api/admin/google/{account_id}/calendars` in `routes/admin.py`; test `tests/test_gcal.py` (respx mocks `https://www.googleapis.com/calendar/v3/...`).

**Interfaces – Consumes:** `access_token`, `normalize_google_event`, `display_range`, `to_json`, `Hub`. **Produces:** `class GoogleCalendarService(store, hub)`: `async start()` (loads `calendar_cache.json`, publishes it, then polls every 5 min and right after config changes via `refresh_now()`), `async list_calendars(account_id) -> list[{"id","name","primary"}]`, `status: dict[str, bool]` (per account, published in `status` topic as `google`).
Fetch per enabled `CalendarRef`: `events.list` with `singleEvents=true`, `orderBy=startTime`, `timeMin/timeMax` = display range in local tz, `maxResults=2500`, paging via `nextPageToken`. Event `color` = member colour.

- [ ] Tests: `test_merges_accounts_and_colors`; `test_paging`; `test_one_account_expired_keeps_others` (account A `invalid_grant` → status `{A: false, B: true}`, B's events published, A's events kept from cache until cache older than 24 h); `test_cache_written_and_loaded_on_start` (start with no network → cached events published); `test_disabled_calendar_ignored`. FAIL → implement → PASS → commit `wallboard: google calendar polling and cache`.

## Milestone 4 – Photos

### Task 10: Image processing

**Files:** `wallboard/wallboard/imaging.py`, `tests/test_imaging.py` (test images generated with Pillow in the test; one tiny HEIC fixture `tests/fixtures/sample.heic` created via pillow-heif in conftest).

**Produces:** `process_image(data: bytes) -> ProcessedImage(display: bytes, background: bytes, thumb: bytes, landscape: bool)`; raises `UnsupportedImage(msg)`. Rules: register pillow-heif opener; `ImageOps.exif_transpose`; convert to RGB (transparent pixels composited on `#111`); display = fit within 1080×1920, JPEG q85, no EXIF; background = cover-crop to 1080×1920, Gaussian blur radius 40, darkened to 55 % brightness, JPEG q80; thumb = fit within 360×360, JPEG q80; `landscape = width > height` after transpose. Input limit 40 MB (`MAX_UPLOAD_BYTES = 40 * 1024 * 1024`) checked by caller; `Image.MAX_IMAGE_PIXELS = 80_000_000`.

- [ ] Tests: `test_orientation_6_rotated` (4000×3000 with EXIF orientation 6 → display 1080×1440, landscape False); `test_no_gps_in_output` (input with GPS EXIF → `Image.open(display).getexif()` has no 0x8825); `test_png_alpha`; `test_heic_accepted`; `test_garbage_rejected` (`b"not an image"` → `UnsupportedImage`); `test_background_size` (1080×1920). FAIL → implement → PASS → commit `wallboard: photo processing`.

### Task 11: Photo store and API

**Files:** `wallboard/wallboard/photos.py`, `wallboard/wallboard/routes/photos_api.py`; media route in `routes/display.py`; tests `tests/test_photos.py`.

**Interfaces – Consumes:** `process_image`, `Hub`. **Produces:** `class PhotoStore(data_dir, hub)`: `albums() -> list[Album]` (`Album(id, name, hidden, photo_ids)`), `create_album(name) -> Album`, `rename_album`, `set_hidden`, `reorder_albums(ids)`, `reorder_photos(album_id, ids)`, `move_photo(photo_id, album_id, index)`, `async ingest(data, album_id) -> str` (photo id = `uuid4().hex`; writes `{id}.jpg`, `{id}.bg.jpg`, `{id}.thumb.jpg` to `data/photos/` via temp files then rename; appends to album end), `delete(photo_id)`, `display_items() -> list[PhotoItem]` (visible albums in order, photos in order; caption = album name; urls `/media/{id}.jpg`, `/media/{id}.bg.jpg`), `zip_stream()`. Every mutation saves `photos.json` atomically and publishes `{"type":"photos",...}`.
API (admin): `GET /api/admin/photos` (albums with thumb urls `/media/{id}.thumb.jpg`), `POST /api/admin/photos/upload` (multipart `files[]`, `album_id`; per-file result `{"name","ok","error"}`; > 40 MB → error "Soubor je příliš velký (max 40 MB)" / English equivalent per `Accept-Language`), `POST /api/admin/albums`, `PATCH /api/admin/albums/{id}` (`name`/`hidden`), `PUT /api/admin/albums/order`, `PUT /api/admin/albums/{id}/order`, `POST /api/admin/photos/{id}/move`, `DELETE /api/admin/photos/{id}`, `GET /api/admin/photos.zip`. `GET /media/{file}` allowed for loopback or admin; filename must match `^[0-9a-f]{32}(\.bg|\.thumb)?\.jpg$`.

- [ ] Tests: `test_upload_appends_in_order`; `test_reorder_and_move`; `test_hidden_album_excluded_from_display`; `test_failed_ingest_leaves_no_files`; `test_oversize_rejected`; `test_media_path_traversal_rejected` (`/media/../secrets.json` → 404); `test_zip_contains_display_files`; `test_mutation_publishes_photos`. FAIL → implement → PASS → commit `wallboard: photo albums and upload api`.

## Milestone 5 – Display

JS tests: `cd wallboard/web && node --test tests/`. Pure logic lives in importable modules without DOM access.

### Task 12: Shared JS — i18n and HA formatting

**Files:** Create `web/shared/i18n.js`, `web/shared/i18n/cs.json`, `web/shared/i18n/en.json`, `web/shared/ha-format.js`, `web/tests/i18n.test.mjs`, `web/tests/ha-format.test.mjs`.

**Produces:** `setLanguage(lang)`, `t(key, vars?) -> string` (falls back to `en`, then the key), `fmtTime(date) -> "07:42"`, `fmtLongDate(date) -> "sobota 7. listopadu"` / `"Saturday 7 November"`, `fmtWeekdayShort(date)`, `fmtMonthYear(date)`, `fmtShortDayMonth(date)`, all with `timeZone` from config (`setTimezone(tz)`). `describe(entityId, state) -> {name, value, unit, icon, on: bool, kind}` — move the logic of `describe()` and the maps `BINARY_LABELS`, `BINARY_ICONS`, `SENSOR_ICONS`, `WEATHER`, `DOMAIN_ICONS`, `fmtNumber` from `www/dakboard/index.html:204-389`, with user-visible strings going through `t()`; add `WEATHER_ICONS` (HA condition → `mdi-weather-*`). Keys needed by later tasks: `today, tomorrow, this_week, more_n ("+{n} další"/"+{n} more"), no_events, home, rooms, lights, devices, status, locked, unlocked, open, closed, on, off, docked, cleaning, returning, starting, action_failed, offline, high_low, all_day`.

- [ ] Tests: `fmtLongDate(new Date("2026-11-07T07:42:00+01:00"))` → `"sobota 7. listopadu"` (cs) and `"Saturday 7 November"` (en); `fmtTime` → `"07:42"` in both; `t("more_n",{n:2})` → `"+2 další"`; missing key fallback; `describe("binary_sensor.front_door", {state:"off", attributes:{device_class:"door"}})` → value `t("closed")`, icon `mdi-door-closed`; `describe("vacuum.robot",{state:"docked"})` → `on:false`; every key in `en.json` exists in `cs.json` and vice versa. FAIL → implement → PASS → commit `wallboard: shared i18n and HA formatting`.

### Task 13: Display shell (store, rotation, gestures, header, photo, offline)

**Files:** `web/display/index.html`, `display.css`, `store.js`, `shell.js`, `header.js`, `photo.js`, `pages/registry.js`; `web/display/lib/rotation.js` + `web/tests/rotation.test.mjs`.

**Interfaces – Consumes:** WS messages (see Data model), `i18n.js`, `ha-format.js`. **Produces for pages:**
- `store.js`: `store.get() -> {config, ha, calendar, weather, photos, status, connected}`, `store.subscribe(fn) -> unsubscribe`, `store.sendAction(entityId) -> Promise<{ok, error}>` (rejects after 5 s with `{ok:false,error:"timeout"}`).
- Page module contract (`registry.js` maps `type` → module): `export default { type, titleKey, settings: [{key, type: "number"|"boolean", labelKey, default}], mount(el, ctx) -> { update(state), destroy() } }` where `ctx = {store, t, fmt, photo}`.
- `header.js`: `renderHeader(el, state, {titleKey, subtitle})` — clock (updates each minute on the minute), date, current weather icon + temperature, who's-home avatars (member colour, initial, green ring if `person` state `home`, faded otherwise).
- `photo.js`: `nextPhoto() -> PhotoItem | null` (respects user order; `photos_shuffle` → random permutation without repeats until all shown), `backgroundEl()`.
- `lib/rotation.js` (pure): `class Rotator({pages, defaultSeconds, pauseSeconds, now})` with `current()`, `next()`, `prev()`, `touch(t)`, `tick(t) -> boolean` (true when page should advance).

Behaviour: page dots at bottom; swipe (≥ 60 px horizontal, < 600 ms) → next/prev; tap not on `[data-tap]` element → next; any touch → pause rotation for `touch_pause_seconds`; night mode (if enabled, between start/end) → black overlay with dim clock, a touch wakes for `touch_pause_seconds`; when WS disconnected or `status.ha === false`, page content gets class `stale` (opacity .55) and a red dot in the header; WS reconnect with backoff 1–30 s; on `config` message pages are re-mounted. Hide cursor (`cursor:none`). No `backdrop-filter`/`filter:blur`; panels use `rgba(18,20,26,.62)`; page background = current photo's `bg_url` behind a dark veil.

- [ ] Tests (`rotation.test.mjs`): advances after `seconds`; per-page `seconds` override; disabled pages skipped; touch pauses for `pauseSeconds` then resumes; `prev()` wraps. FAIL → implement → PASS.
- [ ] Manual check: run `python3 dev/mock_ha.py --port 8123` and the server (config `ha_url=http://localhost:8123`, token `dev-token`), open `http://localhost:8080/` in Chromium with device toolbar 1080×1920: header clock/weather/people render, dots show 4 pages (pages may be placeholder `div`s until Tasks 14–17), rotation and swipe work, stopping mock HA dims the page and shows the red dot within 10 s.
- [ ] Commit `wallboard: display shell`.

### Task 14: Calendar layout helpers + Week page

**Files:** `web/display/lib/layout.js`, `web/tests/layout.test.mjs`, `web/display/pages/week.js`.

**Produces (pure, used by Today/Week/Month):** `daysOf(event, tz) -> string[]` (ISO local dates; same rules as Python `event_days`), `bucket(events, firstISO, days, tz) -> Map<iso, Event[]>` (same ordering as Python), `isPast(event, now)`, `spanPart(event, iso) -> "single"|"start"|"middle"|"end"` (for multi-day bars), `monthGrid(todayISO) -> iso[42]` (Monday-first, 6 weeks).

Week page: header title `this_week` + `"7.–13. 11."`-style subtitle via `fmtShortDayMonth`; 7 rows from today; each row: weekday, day number, weather icon + high/low for that date (if in forecast), chips (`time title`, member-colour left border; all-day solid fill; past at 40 % opacity); rows share height equally, and a row that overflows ends with `t("more_n")`; weekend weekday label tinted `#ffb3c0`; today highlighted. Matches `mockups/portrait-v1.html` screen 2.

- [ ] Tests: same cases as Task 7 (all-day exclusive, overnight, midnight end, DST), `spanPart` for 20–22 Nov trip, `monthGrid("2026-11-07")[0] === "2026-10-26"` and `[41] === "2026-12-06"`. FAIL → implement → PASS. Manual visual check against mockup with mock data (add a `dev/sample_calendar.json` loaded by the server when `WALLBOARD_DEV_CALENDAR` env var points to it — dev only, documented in `deploy/README.md`). Commit `wallboard: calendar layout and week page`.

### Task 15: Month page

**Files:** `web/display/pages/month.js`.
Monday-first 6-week grid filling the height below header + colour legend (member colour dot + name); out-of-month days 28 % opacity, past days 50 %; weekends tinted; today highlighted with blue ring and filled number; holiday label in pink from `calendar.holidays` (add `"holidays": {iso: name}` for the display range in the `CalendarPayload` from Task 9 — server-side, using `czech_holidays` and config language; add a test `test_payload_includes_holidays` in `tests/test_gcal.py`); weather icon on days in forecast; per day up to `options.max_events_per_day` (setting, number, default 3) entries `time title` (Roboto Condensed 2.15cqw, 2-line clamp, member-colour left edge), all-day solid; multi-day all-day events drawn as a continuous bar using `spanPart`; then `t("more_n")`. Matches `mockups/month-v2.html`.

- [ ] Add the Python test, FAIL → implement holidays in payload → PASS. Manual visual check against mockup. Commit `wallboard: month page`.

### Task 16: Today page

**Files:** `web/display/pages/today.js`.
Matches `mockups/portrait-v1.html` screen 1: header (large clock, long date, weather icon + temp, `high_low`, people); framed photo (`photo.nextPhoto()`, changes every page visit; landscape photo shown `object-fit: contain` over its own `bg_url`, portrait `cover`) with caption; "Today" timeline: all-day chips, then timed events with member avatar initial, title, location line (no travel time), past events faded, red "now" line placed between the last past and the next event, updated each minute; `no_events` text when empty; three cards for the next 3 days (weekday, weather icon, high; up to 2 event titles with colour dot, then `more_n`); quick tiles row from `config.quick_tiles` (uses the same tile component as Home – create it here as `web/display/tile.js`: `renderTile(el, tileCfg, haState, onTap)`, `data-tap` attribute, optimistic toggle → spinner → revert + toast `action_failed` on `{ok:false}`).

- [ ] Manual check with mock HA + sample calendar: tapping Kitchen toggles it in mock HA and the tile follows; stopping mock HA then tapping shows the failure toast and reverts. Commit `wallboard: today page and tiles`.

### Task 17: Home page

**Files:** `web/display/pages/home.js`.
Matches `mockups/portrait-v1.html` screen 4: header title `home` + subtitle summarising attention items (open doors/windows count, running appliances); room cards (3 columns; name, temperature, humidity, number of lights on from `light_entities`); "Lights" tile group and "Devices" tile group from `config.tiles` by `group`, using `tile.js`; status rows from `status_entities` via `describe()` with warning colour for `open`/`on` binary sensors of class door/window/garage_door; empty sections hidden.

- [ ] Manual check with mock HA: vacuum tile → `Starting…` spinner → `cleaning`; second tap → `return_to_base`. Commit `wallboard: home page`.

## Milestone 6 – Admin UI

### Task 18: Settings API and page

**Files:** extend `routes/admin.py`; create `web/admin/settings.html`, `web/admin/settings.js`; test `tests/test_settings_api.py`.

**API (admin):** `GET /api/admin/config` → `{config, secrets_set: {ha_token: bool, google_client: bool, admin_password: bool}, status}` (the settings page imports `web/display/pages/registry.js` itself to list page types and their `settings`; `/static/display` is therefore also served to admin sessions, not only loopback); `PUT /api/admin/config` (full `Config`; validation errors → 422 with field paths; on success: save, publish `config`, `HAClient.restart()` if `ha_url`/entities changed, `gcal.refresh_now()` if calendars changed); `PUT /api/admin/secrets` (`ha_token`, `google_client_id`, `google_client_secret`, `admin_password` — write-only, empty string = unchanged); `POST /api/admin/ha/test {url, token?}` → `{ok, error, version}`; `GET /api/admin/ha/entities` → `[{entity_id, name, domain}]` from current HA connection (for pickers).

**Page sections** (phone-friendly, single column, cs/en via `i18n.js`): General (language, timezone, rotation seconds, touch pause, night mode) · Family (members: name, initial, colour picker, person entity picker) · Google (client id/secret fields with a link to the setup steps in `deploy/README.md`; per member "Connect Google" button → opens auth URL; if the page is not on `localhost`, show a paste box "Paste the address you ended up on" → `/google/paste`; per account status badge `OK` / `Přihlášení vypršelo – přihlaste se znovu`; calendar checkboxes from `/google/{id}/calendars` mapped to a member) · Home Assistant (URL, token, Test button, weather entity, rooms editor, tiles editor with group/label/icon/controllable — controllable checkbox disabled for domains not in the Task 5 map, status entities, quick tiles ≤ 4) · Pages (drag to reorder, enable, seconds, generic per-page settings from each module's `settings`) · link to Photos · Log out. Save button sends the whole config; shows 422 errors next to fields.

- [ ] Tests: `test_put_config_validates_and_publishes`; `test_secrets_write_only` (GET never contains token; `secrets_set.ha_token` true after PUT); `test_empty_secret_keeps_old`; `test_ha_change_restarts_client` (spy); `test_ha_test_endpoint` against mock HA (ok + bad token). FAIL → implement → PASS. Manual: full setup from a phone-width browser against mock HA. Commit `wallboard: settings api and page`.

### Task 19: Photos page

**Files:** `web/admin/photos.html`, `web/admin/photos.js`.
Phone-first: album list (drag handle to reorder, rename inline, hide toggle, "new album"); inside album: thumbnail grid with drag-to-reorder (pointer events, works with touch; long-press 300 ms to start drag on touch), delete with undo toast (5 s, delete request sent after), upload button `<input type="file" accept="image/*,.heic" multiple>` with per-file progress (XHR upload progress) and per-file error message from API; shuffle switch (saves `photos_shuffle` via settings API); "Download all (zip)" button.

- [ ] Manual check on a phone (or device emulation): upload 5 photos incl. one HEIC and one non-image → 4 OK, 1 error shown; reorder → display order on the wall follows within 2 s; hide album → its photos stop appearing. Commit `wallboard: photos page`.

## Milestone 7 – Deployment

### Task 20: Pi install, kiosk, docs

**Files:** `wallboard/deploy/install.sh`, `wallboard/deploy/wallboard.service`, `wallboard/deploy/labwc-autostart`, `wallboard/deploy/README.md`; modify root `README.md` (one section linking to `wallboard/deploy/README.md`).

`install.sh` (idempotent, run as the desktop user with sudo available, Raspberry Pi OS Bookworm 64-bit desktop): apt install `python3-venv chromium` (fall back to `chromium-browser` on older images; the autostart uses whichever binary exists) `wlr-randr libheif1 unclutter-xfixes`(skip if unavailable); create venv in `~/wallboard/.venv`, `pip install ./wallboard`; data dir `~/wallboard-data`; `python -m wallboard set-password` if no password yet; install systemd unit (`Restart=always`, `User=<user>`, `ExecStart=<venv>/bin/python -m wallboard serve --data ~/wallboard-data --port 8080`); install labwc autostart (`~/.config/labwc/autostart`): `wlr-randr --output <OUTPUT> --transform <ROTATION>` then a loop that waits for `/healthz` and runs `chromium-browser --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble --check-for-update-interval=31536000 --overscroll-history-navigation=0 --disable-pinch http://localhost:8080/`, restarting it if it exits; disable screen blanking (`raspi-config nonint do_blanking 1`). `OUTPUT` default `HDMI-A-1`, `ROTATION` default `90` — README tells the owner to try `270` if the picture is upside down, and to check touch maps correctly (labwc `rc.xml` `<touch mapToOutput="HDMI-A-1"/>`, installed by the script).

`README.md` (Czech-speaking owner reads English fine; write in English, plain): requirements; install steps; first login and setup order (HA → Family → Google → Pages → Photos); **Google Cloud setup**: create project, enable Google Calendar API, OAuth consent screen *External*, add scope `calendar.readonly`, **publish app to "In production"** (explain the 7-day expiry otherwise and that the "Google hasn't verified this app" warning is expected → Advanced → continue), create OAuth client type *Web application* with redirect URI `http://localhost:8080/api/google/callback`; signing in from a phone (paste flow); backup (Photos → Download all, plus copy `~/wallboard-data`); recommend USB SSD; update (`git pull && ~/wallboard/.venv/bin/pip install ./wallboard && sudo systemctl restart wallboard`); troubleshooting (`journalctl -u wallboard -f`); dev workflow with `dev/mock_ha.py` and `WALLBOARD_DEV_CALENDAR`.

- [ ] Verify: `bash -n deploy/install.sh`; `shellcheck deploy/install.sh` if available; `systemd-analyze verify deploy/wallboard.service` (with placeholders filled) if available; full test suites `pytest` and `node --test` pass. On-device check (owner, with the Pi): fresh Pi OS → `install.sh` → reboot → board appears in portrait, touch works, survives `sudo systemctl restart wallboard` and a power cycle. Commit `wallboard: raspberry pi install and docs`.

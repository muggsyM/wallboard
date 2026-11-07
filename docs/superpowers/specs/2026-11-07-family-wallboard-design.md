# Family wallboard — design

Date: 2026-11-07 · Status: draft for approval

A self-hosted, DAKboard-like wall display for one family home: Google calendars,
family photos and Home Assistant, on a portrait monitor driven by a Raspberry Pi.
The Pi runs only this app.

Approved visual references (brainstorm mockups, open in a browser; they are
HTML fragments, so some frame styling is missing):

- `mockups/portrait-v1.html` — Today, Next 7 days, Home pages (portrait). Its Month page is superseded.
- `mockups/month-v2.html` — Month page (approved direction: text in every day).

Names, colours, places and entities in the mockups are placeholders.

---

## 1. Intent

The screen hangs on the wall next to the bathroom, in the private part of the
house. Family members glance at it in passing to see **today's events, the
weather and family pictures**, look further ahead (week, month), and tap it to
**control the house** (lights, robot vacuum, scenes).

It replaces a DAKboard subscription with something local, owned and
extensible: the owner wants to **add new pages easily** over time.

Success: the family uses it daily without thinking about it; it shows correct,
current information; it never needs SSH to keep running; adding a page later is
a small, contained piece of work.

## 2. Goals

1. Full-screen, **portrait** display (monitor rotated 90° to the left) on a Raspberry Pi in Chromium kiosk mode.
2. A set of **pages** that **rotate automatically** and can be changed by touch.
3. Initial pages: **Today**, **Next 7 days**, **Month**, **Home** (Home Assistant).
4. **Google Calendar** via Google login (OAuth). **Each family member has their own personal Gmail account**; the app signs in to each account separately and shows the calendars chosen from each, mapped to a family member with a colour.
5. **Photos uploaded through the app** and stored on the Pi, with a **user-defined order**.
6. **Home Assistant**: show states live, and **control** chosen devices by tapping.
7. **Settings page in the browser** for everything configurable (no file editing needed for normal use).
8. **Czech UI by default, switchable to English.**
9. Architecture where a new page type is one self-contained module plus a registration line.

## 3. Non-goals

- Multiple homes/users, accounts, cloud hosting, or making this installable by strangers.
- Access from outside the home network.
- Google Photos, iCloud, NAS or other photo sources (NAS was considered and dropped; may come back later as an optional source).
- Creating or editing calendar events from the wall.
- Replacing Home Assistant's own dashboard: Home shows a chosen subset only.
- Docker (considered and rejected: dedicated Pi, Chromium must be native anyway).

## 4. Anti-goals (failure even if it "works")

- Looks like an office calendar or a generic admin dashboard. It must look like a calm, good-looking family screen (see mockups: dark glass panels, family photo in the background, large light clock, Outfit font, Material Design Icons).
- Text too small to read from a step or two away.
- The slideshow flips pages away while someone is reading or tapping.
- Any device in Home Assistant can be controlled from the wall. Only devices explicitly chosen in Settings are reachable, enforced on the server.
- Secrets (HA token, Google refresh token) ever reach the display browser.
- Needs regular SSH maintenance; a power cut or crash leaves a black screen.

## 5. Constraints and environment

- **Hardware:** **Raspberry Pi 4**, **touchscreen** monitor rotated 90° left. Exact monitor resolution not known; design for 1080×1920 portrait, with all sizes relative to screen width so other resolutions scale. Pi 4 GPU budget: no live CSS `backdrop-filter` over the full screen and no live blur of photos; blurred/darkened backgrounds are pre-rendered on the server when a photo is uploaded, and glass panels use semi-transparent fills. Current Pi OS 64-bit (Wayland/labwc).
- **OS:** Raspberry Pi OS (current, 64-bit, desktop). Everything — server and kiosk browser — on the Pi.
- **Network:** home LAN only. Display uses `http://localhost`. Phones/computers reach Photos and Settings via `http://<pi-name>.local:<port>`. No HTTPS required (that was only needed for DAKboard's iframes).
- **Home Assistant** runs elsewhere on the LAN; reached with a long-lived access token.
- **Storage:** photos on the Pi's disk (SD card or USB SSD).

## 6. Decided by the owner

| Topic | Decision |
|---|---|
| Display device | Raspberry Pi, everything on the Pi, Pi hosts only this app |
| Orientation | Portrait (monitor rotated 90° left) |
| Navigation | Rotate automatically **and** tap to switch |
| Pages | Today (style "C · Family planner"), Next 7 days, Month, Home (HA); easy to add more |
| Today style | C: header, framed photo, today's timeline with "now" line, next 3 days, quick HA tiles |
| Next 7 days | Its own page |
| Month | Its own page; **every day shows event text**, not just dots |
| Calendar source | Google login (OAuth) |
| Photos | Uploaded through the app, stored on the Pi; owner sets the order |
| HA control | Tap to toggle lights, start the robot vacuum, run scenes, etc. The gas boiler is **out of scope** |
| Google accounts | Each family member has their own personal Gmail account |
| Hardware | Raspberry Pi 4, touchscreen |
| Settings | Browser settings page |
| Language | Czech, switchable to English |
| Packaging | No Docker |

## 7. Design calls made by Claude (owner can override)

- **Stack:** Python 3 server (FastAPI + uvicorn) — matches the existing repo's Python; plain HTML/CSS/JS front end, no build step, no framework. Each page type is an ES module.
- **Runs as** a systemd service in a Python virtualenv; Chromium kiosk autostarts in the desktop session with screen rotation set in the OS display config. An `install.sh` sets up a fresh Pi OS end to end; update = `git pull` + service restart.
- **Location in repo:** new top-level `wallboard/` directory in this repository (reuses the widget's HA formatting/icon logic from `www/dakboard/index.html`). The old DAKboard widget and integration stay untouched.
- **HA connection:** server connects directly to HA's WebSocket API with the long-lived token (`subscribe_entities`/`state_changed`, `call_service`). The existing read-only `dakboard` custom integration is not used (it can't control).
- **Control allowlist:** Settings stores which entities are shown and which actions are allowed for each; the server rejects any service call not on that list. Display ↔ server traffic is over `localhost` only; control endpoints refuse non-loopback clients.
- **No confirm dialogs:** with the boiler out of scope, every allowed action is a single tap. (A per-entity "ask first" flag was considered and dropped as unneeded.)
- **Allowed actions are simple, per domain:** `light`/`switch`/`fan` toggle; `vacuum` start ↔ return to base; `scene`/`script` activate; `cover` open/close. No parameterised calls (setpoints, modes).
- **Tap feedback:** optimistic tile change with spinner; reverts with a short error toast if HA doesn't confirm within ~5 s.
- **Gestures:** swipe left/right = previous/next page; tap on empty area = next page; tap on a tile = act on it. Any touch pauses rotation for 2 min (configurable).
- **Rotation:** per-page duration (default 30 s), pages can be disabled/reordered in Settings.
- **Admin password:** Settings and Photos pages (reachable from the LAN) require a password set at install; they hold the HA token and Google login. The kiosk display itself needs no login.
- **Photo processing on upload:** auto-rotate from EXIF, resize to fit 1080×1920 (keep aspect), re-encode JPEG/WebP ~85 %, strip EXIF location; keep a small thumbnail. HEIC (iPhone) accepted.
- **Photo display:** shown in the user's order, looping; a "shuffle" switch exists. Landscape photos in tall frames get a blurred, darkened copy of themselves behind them rather than black bars. Caption = album name.
- **Albums:** photos belong to an album (name = caption); albums and photos within them are drag-reorderable; an album can be hidden without deleting.
- **Backup:** "Download all photos (zip)" button on the Photos page. Recommend a USB SSD over SD card in the install notes.
- **Language:** all UI strings in `cs` and `en` translation files; dates via `Intl` with the chosen locale; Monday-first weeks and 24-hour time in both. Czech public holidays computed locally (incl. Easter Friday/Monday). Event titles shown as-is.
- **Weather:** taken from Home Assistant's `weather.*` entity (current + daily forecast via `weather.get_forecasts`), chosen in Settings. No separate weather API key.
- **Who's home:** avatars from HA `person.*` entities, mapped to family members in Settings.
- **Offline behaviour:** if HA or Google is unreachable, the last data stays on screen, dimmed, with a small red indicator (same behaviour as the existing widget). Calendar data cached on disk so a reboot without internet still shows events.
- **Event location, no travel time:** the second line under an event is its Google location only. The mockup's "leave 8:30" is placeholder text; no routing/maps API.
- **Multiple Google accounts:** one OAuth client (owned by the owner's Google Cloud project), several stored refresh tokens — one per family member's account. Each person signs in once from Settings. Unverified "In production" apps allow up to 100 users, plenty here.
- **Google token longevity:** the install guide must walk the owner through creating the Google Cloud OAuth client and setting it to "In production" (unverified, accept the warning screen). In "Testing" status Google expires refresh tokens after 7 days, which would silently break the calendar.
- **Screen schedule (night):** optional "screen off/dim between 23:00–6:00" via HA or the Pi's display power; off by default.

## 8. Architecture

```
Raspberry Pi
├─ Chromium (kiosk, portrait) ── http://localhost:8080/  (display)
└─ wallboard server (systemd, Python/FastAPI)
     ├─ /            display shell: page rotator, gestures, shared header
     ├─ /settings    admin UI (password)        ← phone/computer on LAN
     ├─ /photos      upload/reorder UI (password)
     ├─ /ws          WebSocket to the display: pushes calendar, HA states, photo list, config changes
     ├─ /api/...     JSON API for settings, photos, HA actions
     ├─ Google Calendar client (OAuth refresh token, polling + cache)
     ├─ HA client (WebSocket, subscribes to chosen entities, forwards allowed service calls)
     ├─ Photo store (files + metadata, order)
     └─ data/  config.json, secrets (0600), calendar cache, photos/
```

Display page modules share: header component (clock, date, weather, people),
background photo, translation helper, theme tokens, and a data store fed by
`/ws`. A page module declares its name, its settings schema (rendered
generically in Settings) and a `render(container, data)` / `update(data)` pair.
Adding a page = new module file + one registration entry; its settings appear in
Settings automatically.

Settings changes are pushed to the display over `/ws` and applied without a
reload where reasonable (reload otherwise).

## 9. Pages

All pages: portrait 9:16, shared header (large light clock, date, current
weather, who's-home avatars), page dots at the bottom, family photo faintly in
the background. Text sizes as in the mockups (sized relative to screen width).

**Today (C · Family planner)** — `mockups/portrait-v1.html`, screen 1.
Header with H/L and next-rain hint; framed photo (~⅕ height) with album caption;
"Today" timeline with all-day chips, past events faded, red "now" line, event
location as a second line (no "leave by" time); three cards for the next 3 days (weather + top
events); a row of 4 quick-control HA tiles (chosen in Settings).

**Next 7 days** — screen 2. One row per day, today first and highlighted;
each row: weekday, date number, weather icon + H/L; events as chips with a
person-coloured left edge, all-day events solid; past events faded; overflow
"+N more".

**Month** — `mockups/month-v2.html`. Monday-first grid filling the page height,
neighbouring months greyed, today highlighted, weekends tinted, public holidays
labelled. Each day lists events as `time + title` (narrow font, up to 2 lines
each), max 3 per day then "+N more"; all-day events solid; multi-day events as
one continuous bar; weather icon on days with a forecast. Colour legend under
the header.

**Home** — screen 4. Room temperature/humidity cards; tappable tiles in
groups (Lights; Devices & scenes) with on/off/running states, spinner while
pending; status rows (doors, windows,
washer, power). Everything chosen and grouped in Settings.

## 10. Settings (browser)

Sections: General (language, rotation timings, touch pause, night schedule,
admin password) · Google (add one Google account per family member, sign
out/re-sign-in per account with a clear "sign-in expired" warning, pick
calendars from each account, map each calendar to a family member) · Family (members: name, initial, colour, linked HA `person`) · Home
Assistant (URL, token, test connection; weather entity; pick entities, group,
order, label, icon, allowed action) · Pages (enable, order,
duration, per-page options) · Photos link.

## 11. Photos page (browser)

Upload from phone gallery (multiple files, progress), albums, drag to reorder
albums and photos, hide album, delete photo, shuffle switch, download-all zip.
Works well on a phone screen.

## 12. Left to the builder

- Exact module/file layout, API shapes, data file formats.
- Google OAuth mechanics on a LAN-only, plain-HTTP app (client type, redirect
  handling, where the consent is completed). Constraint: one-time sign-in from
  the Settings page, refresh token stored on the Pi, no public hostname.
- Calendar refresh strategy (polling interval, sync tokens), cache format.
- Image library choice (e.g. Pillow + pillow-heif), drag-and-drop implementation.
- Kiosk details (Wayland/X11, rotation and touch-rotation config, hiding cursor, disabling screen blanking, crash restart).
- Tests: unit tests for server logic (allowlist, calendar → day buckets incl. multi-day/all-day/timezones, holidays, photo processing); a mock HA (extend `dev/mock_ha.py`) for UI work.

## 13. Open questions

None blocking. Monitor resolution unknown (handled by relative sizing, §5).

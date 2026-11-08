# Family wallboard

A self-hosted family wall display for a Raspberry Pi 4 portrait touchscreen: a replacement for DAKboard that keeps everything on your own network.

- **Pages:** Today (photo, timeline, next days, quick Home Assistant tiles), Next 7 days, Month and Home. They rotate on their own; swipe or tap to switch.
- **Calendars:** one Google account per family member, cached on the Pi so a reboot without internet still shows events.
- **Photos:** uploaded from a phone to the Pi, in albums you order yourself. iPhone HEIC works.
- **Home Assistant:** live states and tap-to-control, limited to the devices you allow in Settings.
- **Settings and Photos pages** in the browser, protected by an admin password. Czech or English.

**Install and setup:** see [wallboard/deploy/README.md](wallboard/deploy/README.md).

```
wallboard/wallboard/   Python server (FastAPI)
wallboard/web/         display and admin pages (plain HTML/JS, no build step)
wallboard/deploy/      Raspberry Pi installer, systemd service, kiosk autostart, guide
wallboard/tests/       server tests (pytest); wallboard/web/tests/ for the browser code (node --test)
dev/mock_ha.py         fake Home Assistant for development
docs/superpowers/      design, mockups and implementation plan
```

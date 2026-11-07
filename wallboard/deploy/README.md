# Family wallboard on a Raspberry Pi

A self-hosted replacement for DAKboard. A small Python server on the Pi shows the family calendar (Google), Home Assistant data, weather and photos on a portrait touchscreen. You manage it from a phone or laptop in the browser: **Settings** and **Photos**, both protected by an admin password.

Contents: [Requirements](#requirements) · [Install](#install) · [First login and setup](#first-login-and-setup) · [Google Cloud setup](#google-cloud-setup) · [Everyday use](#everyday-use) · [Backup](#backup) · [Update](#update) · [Troubleshooting](#troubleshooting) · [Development](#development)

## Requirements

- Raspberry Pi 4 (2 GB RAM or more).
- **Raspberry Pi OS Bookworm 64-bit, desktop version**, using the **labwc** (Wayland) desktop. This is the default on current images. On an older Bookworm image, switch with `sudo raspi-config` → *Advanced Options* → *Wayland* → *labwc*.
- A touchscreen on HDMI, mounted in portrait (the board is designed for 1080 × 1920).
- **A USB SSD is recommended** instead of the SD card. SD cards wear out and get corrupted after power cuts. Boot from the SSD, or at least keep the data directory on it (see `WALLBOARD_DATA` below).
- Home Assistant on your network, plus a long-lived access token (HA → your profile → *Security* → *Long-lived access tokens*).
- A Google account for the calendars, and about 15 minutes for the [Google Cloud setup](#google-cloud-setup).

## Install

1. Flash Raspberry Pi OS (64-bit, desktop) with Raspberry Pi Imager. In the Imager settings, choose a hostname (e.g. `wallboard`), your user, Wi-Fi and **enable SSH**.
2. Boot the Pi and make sure it logs in to the desktop automatically: `sudo raspi-config` → *System Options* → *Boot / Auto Login* → *Desktop Autologin*. The kiosk browser starts with the desktop session.
3. Over SSH (or in a terminal on the Pi), get the code and run the installer:

   ```sh
   sudo apt install -y git
   git clone <repo-url> ~/wallboard
   cd ~/wallboard
   ./wallboard/deploy/install.sh
   ```

   Run it as your normal user, not with `sudo`; it asks for your sudo password where needed. It asks you to choose the **admin password** (at least 8 characters) the first time.
4. Reboot: `sudo reboot`. After the desktop starts, the board appears full screen.
5. Open Settings from a phone or laptop on the same network: `http://wallboard.local:8080/settings` (use your hostname).

The installer is safe to run again at any time. It:

- installs the packages `python3-venv`, `chromium` (or `chromium-browser` on older images), `wlr-randr`, `libheif1`, `curl`, and `unclutter-xfixes` if available;
- creates the Python environment in `~/wallboard/.venv` and installs the server into it;
- creates the data directory `~/wallboard-data` and sets the admin password if none is set yet;
- installs and starts the `wallboard` systemd service (it restarts automatically if it crashes, and starts at boot);
- adds a marked block to `~/.config/labwc/autostart` that rotates the screen and starts Chromium in kiosk mode once the server answers, restarting the browser if it closes;
- maps the touchscreen to the display in `~/.config/labwc/rc.xml` (`<touch deviceName="" mapToOutput="HDMI-A-1"/>`), so touches line up after rotation. If the file already has a `<touch>` line, it leaves it alone and tells you. Before changing the file, it saves a backup as `rc.xml.wallboard-bak`;
- turns off screen blanking (`raspi-config nonint do_blanking 1`).

The kiosk browser uses its own profile in `~/.config/wallboard-kiosk`, separate from your normal Chromium.

Note: on a fresh Pi, the new `~/.config/labwc/autostart` may replace the default desktop start-up, so the taskbar may no longer show behind the board. That is fine for a wall display.

### Options

Set these in front of the command, e.g. `ROTATION=270 ./wallboard/deploy/install.sh`:

| Variable | Default | Meaning |
|---|---|---|
| `ROTATION` | `90` | Screen rotation. **If the picture is upside down, use `270`.** |
| `OUTPUT` | `HDMI-A-1` | Display output. Run `wlr-randr` in a terminal on the Pi to see the names (the second HDMI port is `HDMI-A-2`). |
| `WALLBOARD_DATA` | `~/wallboard-data` | Data directory, e.g. a folder on the USB SSD. |

After changing `ROTATION` or `OUTPUT`, reboot. If you change `OUTPUT`, also check the `mapToOutput` value in `~/.config/labwc/rc.xml`. **Check that touch lines up:** tap a tile in a corner. If the wrong spot reacts, the touch mapping is wrong, so check `rc.xml`.

## First login and setup

Open `http://<hostname>.local:8080/settings` and log in with the admin password. Set things up in this order, and press **Save** at the top of the page after each part:

1. **Home Assistant**: enter the address (e.g. `http://homeassistant.local:8123`) and the long-lived access token, then press **Test connection**. After that, the other fields suggest entity names as you type. Then pick the weather entity, rooms, tiles and status entities.
2. **Family**: add each member with a name, initial, colour and optionally their `person.…` entity. Event colours on the board come from here.
3. **Google calendars**: do the [Google Cloud setup](#google-cloud-setup) once, enter the **Client ID** and **Client secret**, and save. Then press **Connect Google** for each family member who has a Google calendar, and choose which calendars to show and as whom ("Show as").
4. **Pages**: choose which pages the board shows, in which order, and for how long. Drag to reorder.
5. **Photos**: open `http://<hostname>.local:8080/photos`, create an album and add photos (JPEG, PNG and iPhone HEIC files work, up to 40 MB each).

Changes made in Settings apply to the board straight away, without a restart.

Limits to know:

- **Seconds per page** (rotation) is at least **5 s**.
- **Pause after a touch** is at least **15 s**. After you tap the screen, the board stays on the current page this long.
- **If you change the Home Assistant address to a different server, the saved token is cleared on purpose**, so your token is never sent to the wrong server. Enter the token again.

## Google Cloud setup

The board reads your calendars through your own small Google Cloud "app". You only do this once.

1. Go to <https://console.cloud.google.com/> and create a new project (e.g. "Wallboard").
2. *APIs & Services* → *Library* → search for **Google Calendar API** → **Enable**.
3. Set up the **OAuth consent screen** (newer consoles call it *Google Auth Platform*: *Branding*, *Audience*, *Data access*):
   - User type: **External**.
   - App name and your e-mail address as the support and developer contact. Nothing else is needed.
   - Scopes (*Data access*): **Add or remove scopes** → add `https://www.googleapis.com/auth/calendar.readonly` (read-only calendar access). The board also asks for your e-mail address (`openid`, `email`) to show which account is connected. These need no setup.
4. **Publish the app: set the publishing status to "In production"** (*Audience* → *Publish app*). This is important. While the app is in *Testing*, Google expires the sign-in after **7 days**, and the calendars stop updating every week. You do **not** need to submit the app for verification. It's for your own family only.
5. Create the OAuth client: *Credentials* (or *Clients*) → **Create credentials** → **OAuth client ID**:
   - Application type: **Web application**.
   - **Authorized redirect URI:** `http://localhost:8080/api/google/callback` (exactly this, even though the Pi has another name on your network).
   - Copy the **Client ID** and **Client secret** into Settings → *Google calendars* and save.

When you sign in, Google shows **"Google hasn't verified this app"**. This is expected, because it is your own app. Click **Advanced** → **Go to Wallboard (unsafe)** → allow access to the calendars.

### Signing in from a phone or laptop

Google always sends you back to `http://localhost:8080/api/google/callback?...`. On the Pi itself that works. On a phone or laptop, that page **fails to load** ("can't connect", "site can't be reached"). This is expected:

1. In Settings, press **Connect Google** for the family member, and sign in with Google.
2. When you land on the page that fails to load, **copy the whole address from the browser's address bar** (it starts with `http://localhost:8080/api/google/callback?state=...`).
3. Go back to the Settings tab and paste it into the box **"Paste the address you ended up on"**, then press **Finish sign-in**.

Do it within 10 minutes of pressing *Connect Google*. Otherwise, start again.

If a calendar later shows **"Sign-in expired – sign in again"**, press *Connect Google* for that member again. If this happens every week, the app is still in *Testing*; see step 4 above.

## Everyday use

- **Settings:** `http://<hostname>.local:8080/settings`. **Photos:** `http://<hostname>.local:8080/photos`.
- The board itself (`http://localhost:8080/`) only opens on the Pi. Other devices are refused on purpose, so nobody on the network can operate your lights through it.
- **Change the admin password** in Settings → *Admin password*. Other logged-in devices are logged out.
- **Login lockout:** after 5 wrong passwords, that device is locked out for a while, and each further lockout lasts longer (up to 15 minutes). If there are 30 failed logins within 15 minutes, everyone is locked out for a while. If you have forgotten the password, set a new one over SSH:

  ```sh
  ~/wallboard/.venv/bin/python -m wallboard set-password --data ~/wallboard-data
  sudo systemctl restart wallboard
  ```

- **Editing the config file by hand** (`~/wallboard-data/config.json`) is possible but not needed. The Settings page applies changes live, but hand edits need `sudo systemctl restart wallboard`. If the file is invalid when the service starts, the board still comes up: the bad file is renamed to `config.json.invalid-<date>` and default settings are used, so you can redo the settings in the Settings page or fix the renamed file and copy it back (`journalctl -u wallboard -e` says what was wrong). The same happens with `secrets.json`, but then the admin password, the Home Assistant token and the Google sign-ins are gone: set a new password with `set-password` (above) and enter the rest again in Settings. A file the service cannot read at all (wrong permissions) is never renamed; the board runs on defaults and refuses to save until you fix the permissions and restart. While the service is running, a half-saved or broken edit is ignored and the last good settings stay in use.

## Backup

- **Photos** page → **Download all (zip)** saves all photos and albums.
- For a full backup, copy the whole data directory `~/wallboard-data`, e.g. from your computer:

  ```sh
  scp -r <user>@wallboard.local:wallboard-data ./wallboard-backup
  ```

  To restore, put the directory back in place and run `sudo systemctl restart wallboard`.

**Keep backups private.** `secrets.json` in the data directory contains the Home Assistant token, the Google client secret and the Google sign-in tokens.

## Update

```sh
cd ~/wallboard
git pull && ~/wallboard/.venv/bin/pip install -e ./wallboard && sudo systemctl restart wallboard
```

The board reconnects to the restarted server by itself, notices that the display files changed and reloads itself within a few seconds, so there is no need to restart the browser. (It also reloads itself once a night, at the night-mode start or else around 03:30, to stay fresh.) If the update notes say the install files changed, run `./wallboard/deploy/install.sh` again instead (it keeps your settings, data and password).

## Troubleshooting

- **Server logs:** `journalctl -u wallboard -f` (live) or `journalctl -u wallboard -e` (most recent). Status: `sudo systemctl status wallboard`.
- **Restart the server:** `sudo systemctl restart wallboard`. The browser waits and reconnects by itself.
- **Is the server up?** On the Pi, run `curl http://localhost:8080/healthz`. It should print `{"ok":true}`.
- **Picture upside down:** rerun the installer with `ROTATION=270 ./wallboard/deploy/install.sh` and reboot.
- **Touches land in the wrong place:** the touchscreen is not mapped to the rotated output. Check `~/.config/labwc/rc.xml` for `<touch deviceName="" mapToOutput="HDMI-A-1"/>`, using the output name that `wlr-randr` shows. Then reboot.
- **Black screen or desktop instead of the board:** check that desktop autologin is on, that `~/.config/labwc/autostart` contains the `# >>> wallboard >>>` block, and that the server runs (see above).
- **Screen goes dark after a while:** run `sudo raspi-config nonint do_blanking 1` and reboot. (Night mode in Settings dims the board on purpose.)
- **Calendars or Home Assistant not updating:** the board shows an offline indicator when it loses the server or Home Assistant. Check Settings: *Test connection* for Home Assistant, and the status next to each Google account.

## Development

On a normal computer (Python 3.11+, Node 20+), from the repo root:

```sh
python3 -m venv wallboard/.venv
wallboard/.venv/bin/pip install -e 'wallboard[dev]'
wallboard/.venv/bin/python -m wallboard set-password --data /tmp/wb-data
WALLBOARD_DEV_CALENDAR=dev/sample_calendar.json wallboard/.venv/bin/python -m wallboard serve --data /tmp/wb-data --port 8080
```

- **Fake Home Assistant:** run `python3 dev/mock_ha.py --port 8123 --token dev-token` in a second terminal (needs `pip install websockets`, which the venv already has). In Settings, set the HA address to `http://127.0.0.1:8123` and the token to `dev-token`.
- **Sample calendar:** `WALLBOARD_DEV_CALENDAR=dev/sample_calendar.json` (relative to the directory you start the server from) shows sample events instead of Google. The events belong to the member ids listed in that file (`dad`, `mom`, `tom`, `anna`, `fam`); to see them, add members with those ids to `members` in the data directory's `config.json` by hand and restart the server (Settings generates its own ids).
- **Display shortcuts:** open `http://localhost:8080/#page=home` to start on a given page type (`today`, `week`, `month`, `home`), and add `#norotate` to stop auto-advancing. You can combine them: `#page=week&norotate`.
- **Tests:**

  ```sh
  cd wallboard && .venv/bin/pytest
  cd wallboard/web && node --test
  ```

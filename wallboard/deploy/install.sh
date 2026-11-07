#!/usr/bin/env bash
# Install the family wallboard on a Raspberry Pi 4 (Raspberry Pi OS Bookworm 64-bit desktop, labwc).
#
# Run as the desktop user (not root) from the repo checkout:
#     ./wallboard/deploy/install.sh
# Safe to run again (after an update, or to change OUTPUT / ROTATION).
#
# Optional environment overrides:
#     WALLBOARD_DATA   data directory          (default ~/wallboard-data)
#     OUTPUT           display output name     (default HDMI-A-1; list them with `wlr-randr`)
#     ROTATION         screen transform        (default 90; use 270 if the picture is upside down)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
APP_DIR="$REPO_ROOT/wallboard"
VENV="$HOME/wallboard/.venv"
DATA_DIR="${WALLBOARD_DATA:-$HOME/wallboard-data}"
OUTPUT="${OUTPUT:-HDMI-A-1}"
ROTATION="${ROTATION:-90}"
RUN_USER="$(id -un)"
LABWC_DIR="$HOME/.config/labwc"
AUTOSTART="$LABWC_DIR/autostart"
RC_XML="$LABWC_DIR/rc.xml"
MARK_BEGIN="# >>> wallboard >>>"
MARK_END="# <<< wallboard <<<"

say() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# Escape a value for the replacement side of `sed s|...|...|`.
sed_escape() { printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'; }

# Replace the @...@ placeholders of a template file and print the result.
fill_template() {
    sed -e "s|@USER@|$(sed_escape "$RUN_USER")|g" \
        -e "s|@REPO_DIR@|$(sed_escape "$APP_DIR")|g" \
        -e "s|@VENV@|$(sed_escape "$VENV")|g" \
        -e "s|@DATA_DIR@|$(sed_escape "$DATA_DIR")|g" \
        -e "s|@OUTPUT@|$(sed_escape "$OUTPUT")|g" \
        -e "s|@ROTATION@|$(sed_escape "$ROTATION")|g" \
        "$1"
}

# True if an apt package exists in the package lists.
apt_has() { apt-cache show "$1" >/dev/null 2>&1; }

# --- checks ---------------------------------------------------------------------------------
[ "$(id -u)" -ne 0 ] || die "run this as the desktop user, not as root (it uses sudo where needed)"
[ -f "$APP_DIR/pyproject.toml" ] || die "cannot find $APP_DIR/pyproject.toml - run the script from the repo checkout"
case "$DATA_DIR" in /*) ;; *) die "WALLBOARD_DATA must be an absolute path" ;; esac
for p in "$APP_DIR" "$VENV" "$DATA_DIR"; do
    case "$p" in *[[:space:]]*) die "paths with spaces are not supported: $p" ;; esac
done
[[ "$OUTPUT" =~ ^[A-Za-z0-9._-]+$ ]] || die "OUTPUT looks wrong: $OUTPUT"
case "$ROTATION" in
    normal | 90 | 180 | 270 | flipped | flipped-90 | flipped-180 | flipped-270) ;;
    *) die "ROTATION must be one of: normal 90 180 270 flipped flipped-90 flipped-180 flipped-270" ;;
esac

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

# --- packages -------------------------------------------------------------------------------
say "Installing packages (sudo)"
sudo apt-get update
packages=(python3-venv wlr-randr libheif1 curl)
if apt_has chromium; then
    packages+=(chromium)
elif apt_has chromium-browser; then
    packages+=(chromium-browser)
else
    die "neither the chromium nor the chromium-browser package is available"
fi
if apt_has unclutter-xfixes; then
    packages+=(unclutter-xfixes)
else
    echo "Note: unclutter-xfixes is not available - skipping it (the wallboard hides the cursor itself)."
fi
sudo apt-get install -y "${packages[@]}"

# --- python venv and app --------------------------------------------------------------------
say "Setting up the Python environment in $VENV"
mkdir -p "$(dirname "$VENV")"
if [ ! -x "$VENV/bin/python" ]; then
    python3 -m venv "$VENV"
fi
# Editable install: the server finds its web files next to the code in this checkout.
"$VENV/bin/pip" install --upgrade -e "$APP_DIR"

# --- data dir and admin password ------------------------------------------------------------
say "Data directory: $DATA_DIR"
mkdir -p -m 700 "$DATA_DIR"

has_password() {
    "$VENV/bin/python" - "$DATA_DIR/secrets.json" <<'PY'
import json, sys
try:
    with open(sys.argv[1]) as f:
        data = json.load(f)
except FileNotFoundError:
    sys.exit(1)
sys.exit(0 if data.get("admin_password_hash") else 1)
PY
}

if has_password; then
    echo "Admin password already set - keeping it."
else
    [ -t 0 ] || die "no admin password yet - run the script in a terminal so it can ask for one"
    say "Choose the admin password for the Settings and Photos pages (at least 8 characters)"
    until "$VENV/bin/python" -m wallboard set-password --data "$DATA_DIR"; do
        echo "Please try again."
    done
fi

# --- systemd service ------------------------------------------------------------------------
say "Installing the wallboard service (sudo)"
fill_template "$SCRIPT_DIR/wallboard.service" >"$TMP_DIR/wallboard.service"
sudo install -m 644 "$TMP_DIR/wallboard.service" /etc/systemd/system/wallboard.service
sudo systemctl daemon-reload
sudo systemctl enable wallboard.service
sudo systemctl restart wallboard.service

# --- labwc autostart (kiosk) ----------------------------------------------------------------
say "Installing the kiosk autostart in $AUTOSTART"
mkdir -p "$LABWC_DIR"
fill_template "$SCRIPT_DIR/labwc-autostart" >"$TMP_DIR/block"
if [ -f "$AUTOSTART" ]; then
    if grep -qxF "$MARK_BEGIN" "$AUTOSTART" && ! grep -qxF "$MARK_END" "$AUTOSTART"; then
        die "$AUTOSTART has '$MARK_BEGIN' but no '$MARK_END' - fix the file by hand and rerun"
    fi
    # Keep everything outside our marked block, then append the fresh block.
    awk -v b="$MARK_BEGIN" -v e="$MARK_END" '
        $0 == b { skip = 1; next }
        $0 == e { skip = 0; next }
        !skip
    ' "$AUTOSTART" >"$TMP_DIR/autostart"
    cat "$TMP_DIR/block" >>"$TMP_DIR/autostart"
else
    cp "$TMP_DIR/block" "$TMP_DIR/autostart"
fi
install -m 755 "$TMP_DIR/autostart" "$AUTOSTART"

# --- touch screen follows the rotated output ------------------------------------------------
say "Mapping the touch screen to $OUTPUT in $RC_XML"
TOUCH_LINE="  <touch deviceName=\"\" mapToOutput=\"$OUTPUT\"/>"
if [ ! -f "$RC_XML" ]; then
    if [ -f /etc/xdg/labwc/rc.xml ]; then
        cp /etc/xdg/labwc/rc.xml "$RC_XML"   # start from the system defaults
    else
        printf '<?xml version="1.0"?>\n<labwc_config>\n</labwc_config>\n' >"$RC_XML"
    fi
fi
if grep -q '<touch' "$RC_XML"; then
    echo "Note: $RC_XML already has a <touch> element - leaving it alone."
    echo "      Make sure it says mapToOutput=\"$OUTPUT\"."
else
    root_close=""
    if grep -q '</labwc_config>' "$RC_XML"; then
        root_close='</labwc_config>'
    elif grep -q '</openbox_config>' "$RC_XML"; then
        root_close='</openbox_config>'
    fi
    if [ -n "$root_close" ]; then
        [ -f "$RC_XML.wallboard-bak" ] || cp "$RC_XML" "$RC_XML.wallboard-bak"
        sed -e "s|$root_close|$(sed_escape "$TOUCH_LINE")\n$root_close|" "$RC_XML" >"$TMP_DIR/rc.xml"
        cp "$TMP_DIR/rc.xml" "$RC_XML"
    else
        echo "Note: could not find the closing tag in $RC_XML - add this line inside it by hand:"
        echo "$TOUCH_LINE"
    fi
fi

# --- screen blanking ------------------------------------------------------------------------
if command -v raspi-config >/dev/null 2>&1; then
    say "Disabling screen blanking (sudo)"
    sudo raspi-config nonint do_blanking 1
else
    echo "Note: raspi-config not found - screen blanking left unchanged."
fi

# --- summary --------------------------------------------------------------------------------
cat <<EOF

============================================================
 Wallboard installed.

 Server:    sudo systemctl status wallboard   (logs: journalctl -u wallboard -f)
 Settings:  http://$(hostname).local:8080/settings   (from a phone or laptop on the same network)
 Photos:    http://$(hostname).local:8080/photos

 Next: reboot to start the kiosk display:   sudo reboot

 Picture upside down?  Rerun with:  ROTATION=270 $0
 Wrong screen output?  Run 'wlr-randr' to list outputs, then rerun with OUTPUT=<name>.
============================================================
EOF

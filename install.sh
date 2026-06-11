#!/usr/bin/env bash
# VButton installer.
#
# Default:   build the standalone .app, install to ~/Applications, add to Login Items.
# Dev mode:  ./install.sh --dev   — venv + deps + model only, no .app build.
#
# Permissions are NOT granted by this script (macOS doesn't allow it); you'll
# be prompted by the system on first use. See README for the exact panes.
set -euo pipefail

cd "$(dirname "$0")"
HERE="$(pwd)"
VENV="$HERE/.venv"
PY="$VENV/bin/python"
APP_SRC="$HERE/dist/VButton.app"
APP_DEST_DIR="$HOME/Applications"
APP_DEST="$APP_DEST_DIR/VButton.app"
BUNDLE_ID="com.ydan.vbutton.app"

DEV_MODE=0
if [ "${1:-}" = "--dev" ]; then
    DEV_MODE=1
fi

echo "[1/6] checking brew + portaudio"
if ! command -v brew >/dev/null; then
    echo "homebrew is required: https://brew.sh" >&2
    exit 1
fi
brew list portaudio >/dev/null 2>&1 || brew install portaudio

echo "[2/6] creating venv at $VENV"
if [ ! -x "$PY" ]; then
    python3 -m venv "$VENV"
fi
"$PY" -m pip install --upgrade pip wheel >/dev/null

echo "[3/6] installing python deps"
"$PY" -m pip install -r "$HERE/requirements.txt"

echo "[4/6] downloading + warming up whisper model (one-time, ~1-2 GB)"
"$PY" "$HERE/vbutton.py" warmup

if [ "$DEV_MODE" = "1" ]; then
    echo
    echo "[5/6] dev mode — skipping .app build"
    echo "[6/6] done."
    echo
    echo "Run from terminal:"
    echo "  $PY $HERE/vbutton.py run"
    echo
    echo "macOS will prompt for permissions on first hotkey press / first mic access."
    echo "In dev mode the grants attach to .venv/bin/python — they break on brew upgrade python."
    exit 0
fi

echo "[5/6] building standalone .app"
"$PY" -m pip install py2app >/dev/null
rm -rf "$HERE/build" "$HERE/dist"
"$PY" "$HERE/setup.py" py2app
"$HERE/tools/postbuild_mlx.sh"   # copies mlx + re-signs the bundle

if [ ! -d "$APP_SRC" ]; then
    echo "build failed: $APP_SRC not found" >&2
    exit 1
fi

# Quit any running copy from a previous install
pkill -f "VButton.app/Contents/MacOS/VButton" 2>/dev/null || true
sleep 1

# Wipe stale TCC entries — re-signing changed the CDHash, so old grants
# would either silently fail to apply or attach to the wrong binary.
for service in Accessibility ListenEvent AppleEvents Microphone; do
    tccutil reset "$service" "$BUNDLE_ID" >/dev/null 2>&1 || true
done

mkdir -p "$APP_DEST_DIR"
rm -rf "$APP_DEST"
cp -R "$APP_SRC" "$APP_DEST"

# Add to Login Items so the .app auto-starts at login. Idempotent: try to
# remove any existing entry first.
osascript -e 'tell application "System Events" to delete login item "VButton"' 2>/dev/null || true
osascript -e "tell application \"System Events\" to make login item at end with properties {path:\"$APP_DEST\", hidden:false}" >/dev/null

echo "[6/6] done."
echo
echo "Installed: $APP_DEST"
echo "Login Item: added (will auto-launch at next login)"
echo
echo "Grant permissions in System Settings -> Privacy & Security:"
echo "  - Microphone:       add $APP_DEST"
echo "  - Input Monitoring: add $APP_DEST"
echo "  - Accessibility:    add $APP_DEST"
echo "  - Automation:       prompted on first paste (VButton -> System Events)"
echo
echo "Launch now:"
echo "  open \"$APP_DEST\""

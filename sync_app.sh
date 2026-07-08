#!/usr/bin/env bash
# Sync your latest vbutton.py edits into the INSTALLED app without a full
# py2app rebuild — and without resetting macOS permissions.
#
# Why this exists: the installed bundle freezes the code as a compiled
# vbutton.pyc inside Contents/Resources/lib/pythonXYZ.zip, and the bundle's
# bootstrap drops Resources/ from sys.path. So `import vbutton` loads that
# frozen .pyc, NOT the loose Resources/vbutton.py. Editing the source alone
# does nothing until the frozen .pyc is refreshed.
#
# This script recompiles vbutton.py with the bundle's OWN python (so the
# bytecode magic matches), replaces the frozen vbutton.pyc in the zip, keeps
# the loose source copy in sync for tidiness, then restarts the app.
#
# A full rebuild (./install.sh) re-signs the bundle, which changes its CDHash
# and wipes TCC grants (Microphone/Accessibility/Input Monitoring). This swap
# leaves the main executable untouched, so those permissions survive.
set -euo pipefail

cd "$(dirname "$0")"
SRC="$(pwd)/vbutton.py"
APP="$HOME/Applications/VButton.app"

[ -f "$SRC" ] || { echo "error: $SRC not found" >&2; exit 1; }
[ -d "$APP" ] || { echo "error: installed app not found at $APP — run ./install.sh first" >&2; exit 1; }

RES="$APP/Contents/Resources"
BPY="$APP/Contents/MacOS/python"
[ -x "$BPY" ] || { echo "error: bundle python not found at $BPY" >&2; exit 1; }

# Find the frozen stdlib zip (pythonXYZ.zip) that holds vbutton.pyc.
ZIP="$(find "$RES/lib" -maxdepth 1 -name 'python*.zip' | head -1)"
[ -n "$ZIP" ] || { echo "error: could not find python*.zip in $RES/lib" >&2; exit 1; }

echo "[1/4] compiling vbutton.py with bundle python ($("$BPY" -c 'import sys;print(".".join(map(str,sys.version_info[:3])))'))"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cp "$SRC" "$TMP/vbutton.py"
"$BPY" -c "import py_compile; py_compile.compile(r'$TMP/vbutton.py', cfile=r'$TMP/vbutton.pyc', doraise=True)"

echo "[2/4] injecting fresh vbutton.pyc into $(basename "$ZIP")"
( cd "$TMP" && zip -q "$ZIP" vbutton.pyc )
# Keep the loose source copy consistent (not imported, but avoids confusion).
cp "$SRC" "$RES/vbutton.py"

echo "[3/4] stopping any running VButton"
pkill -9 -f "VButton.app" 2>/dev/null || true
sleep 1

echo "[4/4] relaunching"
open "$APP"
sleep 2
if pgrep -f "VButton.app/Contents/MacOS/VButton" >/dev/null; then
    echo "done — VButton is running your latest code (look for the menu bar icon)."
else
    echo "warning: VButton did not appear to start; launch it manually from ~/Applications." >&2
fi

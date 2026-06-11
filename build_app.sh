#!/usr/bin/env bash
# DEPRECATED — this script builds a thin shell-script wrapper .app that
# launches vbutton.py via the local venv. It uses the same bundle ID
# (com.ydan.vbutton.app) as the real py2app build, which causes macOS TCC
# (Accessibility, Input Monitoring, Automation) to associate grants with
# whichever .app it saw last — extremely confusing.
#
# Use ./build_app_py2app.sh instead. It produces a self-contained bundle
# you can install to ~/Applications and grant permissions to once.
#
# Continuing only because you ran the script explicitly...
set -euo pipefail

cat <<'WARN' >&2
WARNING: build_app.sh is deprecated. The .app it produces will share a
bundle ID with any py2app build you also have installed, which will break
your TCC permissions in non-obvious ways. Prefer:
  ./build_app_py2app.sh
  cp -R dist/VButton.app ~/Applications/
Continuing in 3 seconds...
WARN
sleep 3

cd "$(dirname "$0")"
HERE="$(pwd)"
APP="$HERE/VButton.app"
APP_BIN="$APP/Contents/MacOS"
APP_RES="$APP/Contents/Resources"
PY="$HERE/.venv/bin/python"
SCRIPT="$HERE/vbutton.py"

if [ ! -x "$PY" ]; then
    echo "venv missing at $PY — run ./install.sh first" >&2
    exit 1
fi

echo "Building $APP"
rm -rf "$APP"
mkdir -p "$APP_BIN" "$APP_RES"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>          <string>VButton</string>
    <key>CFBundleDisplayName</key>   <string>VButton</string>
    <key>CFBundleIdentifier</key>    <string>com.ydan.vbutton.app</string>
    <key>CFBundleVersion</key>       <string>1.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleExecutable</key>    <string>VButton</string>
    <key>CFBundleIconFile</key>      <string>VButton</string>
    <key>CFBundlePackageType</key>   <string>APPL</string>
    <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
    <key>LSUIElement</key>           <true/>
    <key>LSMinimumSystemVersion</key><string>13.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSMicrophoneUsageDescription</key><string>VButton records audio so it can transcribe what you say into text.</string>
    <key>NSAppleEventsUsageDescription</key><string>VButton sends Cmd+V to paste transcribed text into the focused app.</string>
</dict>
</plist>
PLIST

cat > "$APP_BIN/VButton" <<LAUNCHER
#!/usr/bin/env bash
# If the daemon is already running, signal it to open its menu bar dropdown.
# Otherwise start it via launchd so logs and lifecycle stay consistent.
LABEL="com.ydan.vbutton"
UID_NUM=\$(id -u)

PIDS=\$(pgrep -f "vbutton.py run" || true)
if [ -n "\$PIDS" ]; then
    for pid in \$PIDS; do
        kill -USR1 "\$pid" 2>/dev/null || true
    done
    exit 0
fi

if launchctl print "gui/\$UID_NUM/\$LABEL" >/dev/null 2>&1; then
    launchctl kickstart "gui/\$UID_NUM/\$LABEL" >/dev/null 2>&1 && exit 0
fi

exec "$PY" "$SCRIPT" run
LAUNCHER
chmod +x "$APP_BIN/VButton"

# Register with Launch Services so Finder/Dock know it.
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
    -f "$APP" 2>/dev/null || true

echo "Done. $APP"
echo "  Drag to /Applications, Dock, or Desktop."
echo "  Double-click to launch (does nothing if already running)."

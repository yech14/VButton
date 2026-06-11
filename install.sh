#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
HERE="$(pwd)"
VENV="$HERE/.venv"
PY="$VENV/bin/python"
LOG="$HOME/Library/Logs/vbutton.log"
AGENT_DIR="$HOME/Library/LaunchAgents"
AGENT_PLIST="$AGENT_DIR/com.ydan.vbutton.plist"
LABEL="com.ydan.vbutton"

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

echo "[5/6] writing LaunchAgent plist -> $AGENT_PLIST"
mkdir -p "$AGENT_DIR" "$(dirname "$LOG")"
sed \
    -e "s|__VENV_PY__|$PY|g" \
    -e "s|__VBUTTON_PY__|$HERE/vbutton.py|g" \
    -e "s|__WORKDIR__|$HERE|g" \
    -e "s|__LOG__|$LOG|g" \
    "$HERE/com.ydan.vbutton.plist.tmpl" > "$AGENT_PLIST"

DOMAIN="gui/$(id -u)"
if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    launchctl bootout "$DOMAIN/$LABEL" || true
fi
launchctl bootstrap "$DOMAIN" "$AGENT_PLIST"
launchctl enable "$DOMAIN/$LABEL"
launchctl kickstart -k "$DOMAIN/$LABEL"

echo "[6/6] done."
echo
echo "macOS will prompt for permissions on first hotkey press / first mic access."
echo "Pre-grant these now to avoid silent failures:"
echo "  - Microphone:       System Settings > Privacy & Security > Microphone       -> add $PY"
echo "  - Accessibility:    System Settings > Privacy & Security > Accessibility    -> add $PY"
echo "  - Input Monitoring: System Settings > Privacy & Security > Input Monitoring -> add $PY"
echo
echo "Logs: $LOG"
echo "Try:  hold Right-Option and speak."
echo "Test: $PY $HERE/vbutton.py once"

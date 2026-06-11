#!/usr/bin/env bash
# Build the standalone VButton.app bundle using py2app.
# Output: dist/VButton.app  (drag to /Applications)
set -euo pipefail

cd "$(dirname "$0")"
HERE="$(pwd)"
PY="$HERE/.venv/bin/python"

if [ ! -x "$PY" ]; then
    echo "venv missing — run ./install.sh first" >&2
    exit 1
fi

if ! "$PY" -c "import py2app" 2>/dev/null; then
    echo "[1/3] installing py2app"
    "$PY" -m pip install py2app
fi

echo "[2/3] py2app build"
rm -rf build dist
"$PY" setup.py py2app

echo "[3/3] post-build (copy mlx, fix stdlib zip, re-sign bundle)"
"$HERE/tools/postbuild_mlx.sh"

echo
echo "Built: $HERE/dist/VButton.app"
echo "  Drag to /Applications, then launch from Spotlight/Finder."
echo "  Permissions to grant on first launch: Microphone, Accessibility, Input Monitoring."

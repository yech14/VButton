#!/usr/bin/env bash
# Copy the mlx + mlx_whisper packages into the built .app bundle.
# py2app skips `mlx` because it's a namespace package (no __init__.py).
set -euo pipefail

cd "$(dirname "$0")/.."
HERE="$(pwd)"

APP="$HERE/dist/VButton.app"
PY="$HERE/.venv/bin/python"

if [ ! -d "$APP" ]; then
    echo "build first: python setup.py py2app" >&2
    exit 1
fi
if [ ! -x "$PY" ]; then
    echo "venv missing at $PY — run ./install.sh first" >&2
    exit 1
fi

# Detect Python minor version from the venv so this works on 3.10+ regardless
# of which version the user has installed (e.g., 3.13 on stock Homebrew,
# 3.14 on the latest, etc.). py2app names the bundled stdlib zip after the
# version too: lib/python3.14/ + lib/python314.zip, lib/python3.13/ + lib/python313.zip.
PYVER=$("$PY" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYVER_FLAT=$(echo "$PYVER" | tr -d '.')
VENV_SP="$HERE/.venv/lib/python$PYVER/site-packages"
BUNDLE_SP="$APP/Contents/Resources/lib/python$PYVER"
echo "detected Python $PYVER (bundle layout: lib/python$PYVER/, lib/python${PYVER_FLAT}.zip)"

mkdir -p "$BUNDLE_SP"
for pkg in mlx mlx_whisper; do
    src="$VENV_SP/$pkg"
    dst="$BUNDLE_SP/$pkg"
    if [ ! -d "$src" ]; then
        echo "skipping $pkg (not installed in venv)" >&2
        continue
    fi
    echo "copying $pkg -> $dst"
    rm -rf "$dst"
    cp -R "$src" "$dst"
done

# Remove lib-dynload/mlx/ — duplicate of mlx/core that we just copied.
DYNLOAD_MLX="$BUNDLE_SP/lib-dynload/mlx"
if [ -d "$DYNLOAD_MLX" ]; then
    echo "removing lib-dynload/mlx/ (use full mlx package from site-packages)"
    rm -rf "$DYNLOAD_MLX"
fi

# py2app stuffs stub mlx/__init__.pyc + mlx/core.pyc into pythonXY.zip
# that try to load `lib-dynload/mlx/core.so`. These shadow the real package
# we copied and break mlx._reprlib_fix etc. Strip them from the zip so
# Python falls back to the real package at lib/pythonX.Y/mlx/.
PYZIP="$APP/Contents/Resources/lib/python${PYVER_FLAT}.zip"
if [ -f "$PYZIP" ]; then
    echo "stripping mlx/* and mlx_whisper/* stubs from python${PYVER_FLAT}.zip"
    zip -q -d "$PYZIP" "mlx/*" "mlx_whisper/*" 2>/dev/null || true
fi

# The mlx copy + zip edits above invalidated the seal py2app stamped on the
# bundle. macOS's strict event delivery (Notes, iTerm, Mail) silently drops
# synthetic Cmd+V from bundles with a broken seal — re-sign so paste works.
echo "re-signing bundle (postbuild modifications invalidated the seal)"
codesign --force --deep --sign - "$APP"
if codesign --verify --deep --strict "$APP" 2>/dev/null; then
    echo "signature OK"
else
    echo "WARNING: codesign verify failed — paste may fail in Notes/iTerm/Mail" >&2
fi

echo "postbuild_mlx done."

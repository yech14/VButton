#!/usr/bin/env bash
# Copy the mlx + mlx_whisper packages into the built .app bundle.
# py2app skips `mlx` because it's a namespace package (no __init__.py).
set -euo pipefail

cd "$(dirname "$0")/.."
HERE="$(pwd)"

APP="$HERE/dist/VButton.app"
VENV_SP="$HERE/.venv/lib/python3.14/site-packages"
BUNDLE_SP="$APP/Contents/Resources/lib/python3.14"

if [ ! -d "$APP" ]; then
    echo "build first: python setup.py py2app" >&2
    exit 1
fi

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
DYNLOAD_MLX="$APP/Contents/Resources/lib/python3.14/lib-dynload/mlx"
if [ -d "$DYNLOAD_MLX" ]; then
    echo "removing lib-dynload/mlx/ (use full mlx package from site-packages)"
    rm -rf "$DYNLOAD_MLX"
fi

# py2app stuffs stub mlx/__init__.pyc + mlx/core.pyc into python314.zip
# that try to load `lib-dynload/mlx/core.so`. These shadow the real package
# we copied and break mlx._reprlib_fix etc. Strip them from the zip so
# Python falls back to the real package at lib/python3.14/mlx/.
PYZIP="$APP/Contents/Resources/lib/python314.zip"
if [ -f "$PYZIP" ]; then
    echo "stripping mlx/* and mlx_whisper/* stubs from python314.zip"
    zip -q -d "$PYZIP" "mlx/*" "mlx_whisper/*" 2>/dev/null || true
fi

echo "postbuild_mlx done."

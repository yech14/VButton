"""py2app build config for VButton.

Build a standalone .app bundle:

    .venv/bin/pip install py2app
    .venv/bin/python setup.py py2app

Output: dist/VButton.app  (drag to /Applications)

The Whisper model is NOT bundled. It downloads to ~/.cache/huggingface on
first run (same as the dev install).
"""
import sys
sys.setrecursionlimit(10000)

from setuptools import setup

APP = ["app_main.py"]

DATA_FILES = [
    ("", ["vbutton.py"]),
    ("assets", [
        "assets/menubar_idle.png",
        "assets/menubar_idle@2x.png",
        "assets/menubar_idle@3x.png",
        "assets/menubar_busy.png",
        "assets/menubar_busy@2x.png",
        "assets/menubar_busy@3x.png",
    ]),
]

OPTIONS = {
    "argv_emulation": False,
    "iconfile": "VButton.icns",
    "plist": {
        "CFBundleName": "VButton",
        "CFBundleDisplayName": "VButton",
        "CFBundleIdentifier": "com.ydan.vbutton.app",
        "CFBundleVersion": "1.0",
        "CFBundleShortVersionString": "1.0",
        "LSUIElement": True,
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription":
            "VButton records audio so it can transcribe what you say into text.",
        "NSAppleEventsUsageDescription":
            "VButton sends Cmd+V to paste transcribed text into the focused app.",
    },
    # NOTE: `mlx` and `mlx_whisper` are NOT listed here — `mlx` is a namespace
    # package (no __init__.py) which py2app's modulegraph can't traverse.
    # They are copied into the bundle by tools/postbuild_mlx.sh after py2app.
    "packages": [
        "tokenizers",
        "huggingface_hub",
        "sounddevice",
        "numpy",
        "pynput",
        "rumps",
    ],
    "includes": [
        "objc",
        "Foundation",
        "AppKit",
        "Quartz",
    ],
    "excludes": [
        "tkinter",
        "matplotlib",
        "PIL",
        "pytest",
        "IPython",
        # MLX backend is used at runtime — drop the faster-whisper code path
        # and its heavy transitive deps to shrink the bundle ~660 MB.
        "torch",
        "torchgen",
        "sympy",
        "onnxruntime",
        "av",
        "faster_whisper",
        "ctranslate2",
    ],
}

setup(
    app=APP,
    name="VButton",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)

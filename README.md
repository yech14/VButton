# VButton

Push-to-talk dictation for macOS. Hold a hotkey (default: **Right Option**), speak, release — your words are transcribed by Whisper and pasted at the cursor. Works in any app, in Hebrew and English (auto-detected), with optional grammar fixing via Gemini.

Runs as a menu-bar app. Local transcription — audio never leaves your Mac.

---

## Requirements

- macOS 13 or newer (Apple Silicon recommended — uses MLX for fast on-device inference)
- [Homebrew](https://brew.sh)
- Python 3.10+
- ~2 GB free disk space for the Whisper model (downloaded on first run)

---

## Install

```bash
git clone https://github.com/<your-user>/VButton.git
cd VButton
./install.sh
```

`install.sh` will:

1. Install `portaudio` via Homebrew (needed for the microphone)
2. Create a Python virtual environment in `.venv/`
3. Install Python dependencies from `requirements.txt`
4. Download and warm up the Whisper model (~1–2 GB, one-time, cached in `~/.cache/huggingface`)
5. Register a LaunchAgent so VButton starts at login and stays running

After install, macOS will prompt for permissions on first use. To avoid silent failures, pre-grant these now in **System Settings → Privacy & Security**:

- **Microphone** → add `.venv/bin/python` (or your terminal)
- **Accessibility** → add `.venv/bin/python` (needed to paste at the cursor)
- **Input Monitoring** → add `.venv/bin/python` (needed for the global hotkey)

Logs live at `~/Library/Logs/vbutton.log`.

---

## Usage

Hold **Right Option**, speak, release. The transcript is pasted where your cursor is.

Click the menu-bar icon to change settings:

- **Hotkey** — Right/Left Option, Right Command, Right Control, Right Shift, F18–F20
- **Language** — Auto (Hebrew + English), Hebrew only, English only, Auto (all languages)
- **Grammar fix** — off, English, Hebrew, both (requires Gemini API key)
- **Bubble timeout** — how long the grammar-fix suggestion popover stays open
- **Auto-switch** — automatically switch your keyboard layout to match the transcribed language

Quick CLI tests:

```bash
.venv/bin/python vbutton.py once     # one-shot record-and-transcribe in the terminal
.venv/bin/python vbutton.py warmup   # re-download / re-cache the model
```

---

## Optional: build a standalone .app

If you want a Finder/Dock icon you can double-click, there are two options.

### Lightweight wrapper (depends on this folder)

```bash
./build_app.sh
```

Creates `./VButton.app` — a thin launcher that runs the script from the venv you already have. Tiny, but the source folder must stay where it is.

### Standalone bundle (self-contained, larger)

```bash
./build_app_py2app.sh
```

Creates `./dist/VButton.app` — a fully self-contained ~500 MB bundle you can move to `/Applications` or share. The Whisper model is still downloaded on first launch (not bundled — that would be ~3 GB).

Drag the resulting `.app` into `/Applications`.

---

## Configuration

All settings are stored in `~/Library/Application Support/VButton/config.json` and can be set from the menu. You can also override at runtime with environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `VBUTTON_HOTKEY` | `alt_r` | Hotkey name (any `pynput.keyboard.Key` attribute) |
| `VBUTTON_SILENT` | unset | Set `1` to suppress start/stop beeps |
| `VBUTTON_BACKEND` | `mlx` | `mlx` (Apple Silicon) or `faster-whisper` (Intel/fallback) |
| `VBUTTON_MODEL` | `mlx-community/whisper-large-v3-mlx` | Whisper model name |
| `VBUTTON_COMPUTE` | `int8` | For faster-whisper: `int8`, `int8_float32`, `float32` |
| `VBUTTON_LANGUAGE` | `auto_heb_en` | `he`, `en`, `auto_heb_en`, `auto_all` |
| `VBUTTON_GRAMMAR_FIX` | `en` | `off`, `en`, `he`, `both` |
| `VBUTTON_GEMINI_KEY` | unset | Gemini API key (also settable via menu) |
| `VBUTTON_POPOVER_TIMEOUT` | `5` | Grammar-fix popover lifetime (seconds, 0 = never) |

### Switching backends / models

MLX (Apple Silicon, default) is fastest. To switch to faster-whisper:

```bash
echo "VBUTTON_BACKEND=faster-whisper" >> ~/.zshrc
echo "VBUTTON_MODEL=large-v3-turbo" >> ~/.zshrc
launchctl kickstart -k "gui/$(id -u)/com.ydan.vbutton"
```

Smaller models (`small`, `medium`, `large-v3-turbo`) trade accuracy for speed and disk space.

---

## Grammar fix (optional)

VButton can run transcripts through Gemini to fix grammar and phrasing. Get a free API key at <https://aistudio.google.com/apikey>, then click the menu-bar icon → **Set Gemini API key…** and paste it in.

When enabled, a small popover appears under the menu-bar icon after each transcript with an "Improved" version. Click **Replace text** to swap the pasted text for the fixed one, or **Copy** to copy the fix without replacing.

---

## Troubleshooting

**Nothing happens when I hold the hotkey.**
Check Input Monitoring permission is granted to the Python interpreter at `.venv/bin/python`. Then restart:

```bash
launchctl kickstart -k "gui/$(id -u)/com.ydan.vbutton"
tail -f ~/Library/Logs/vbutton.log
```

**Mic is silent / "very low peak rms" warning.**
Microphone permission isn't granted, or another app has exclusive control. Re-check System Settings → Microphone.

**Text doesn't paste at the cursor.**
Accessibility permission missing. Add `.venv/bin/python` (or `VButton.app` if you built the bundle) to System Settings → Accessibility.

**See what's happening:**

```bash
tail -f ~/Library/Logs/vbutton.log
```

---

## Uninstall

```bash
# Stop and remove the LaunchAgent
launchctl bootout "gui/$(id -u)/com.ydan.vbutton" 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.ydan.vbutton.plist

# Remove config, logs, and the cached Whisper model
rm -rf ~/Library/Application\ Support/VButton
rm -f ~/Library/Logs/vbutton.log
rm -rf ~/.cache/huggingface/hub/models--mlx-community--whisper-large-v3-mlx

# Remove the project folder (and any built .app)
rm -rf /path/to/VButton
rm -rf /Applications/VButton.app    # if you installed the bundled .app
```

---

## Project layout

```
vbutton.py                  main application (menu bar app + transcription)
app_main.py                 entry point used by py2app
install.sh                  one-command dev install + LaunchAgent setup
build_app.sh                build thin .app wrapper
build_app_py2app.sh         build standalone .app bundle
setup.py                    py2app build configuration
requirements.txt            Python dependencies
com.ydan.vbutton.plist.tmpl LaunchAgent template
assets/                     menu-bar icons (idle / busy, @1x/@2x/@3x)
tools/                      icon generators and post-build helpers
VButton.icns                .app bundle icon
```

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

## Install (recommended: standalone .app)

```bash
git clone https://github.com/<your-user>/VButton.git
cd VButton
./install.sh           # one-time: brew deps, venv, py2app, model warmup, install to ~/Applications
```

`install.sh` will:

1. Install `portaudio` via Homebrew
2. Create a Python virtual environment in `.venv/`
3. Install Python deps from `requirements.txt` (including `py2app`)
4. Download and warm up the Whisper model (~1–2 GB, one-time, cached in `~/.cache/huggingface`)
5. Build the standalone bundle into `dist/VButton.app`
6. Re-sign the bundle (py2app's seal is invalidated by the mlx postbuild — without re-signing, paste silently fails in Notes/iTerm/Mail)
7. Move it to `~/Applications/VButton.app`
8. Add it to **Login Items** so it auto-launches when you log in

After the install completes, you'll need to grant macOS permissions — see the next section.

> **One install path only.** Don't also run `./build_app.sh` or write a launchd agent that runs `vbutton.py` directly — that creates a second instance with the same bundle ID, which produces duplicate paste events and confuses TCC.

---

## Permissions (grant once, before first use)

All permissions are granted to the **VButton.app** entry (not to "Python"). Open **System Settings → Privacy & Security** and add `/Users/<you>/Applications/VButton.app` to:

| Pane | Why it's needed |
|---|---|
| **Microphone** | record your voice |
| **Input Monitoring** | listen for the global hotkey |
| **Accessibility** | general event delivery |
| **Automation** → *VButton → System Events* | paste via System Events (prompted on first paste) |

You can pre-grant the first three; the **Automation** prompt will appear automatically the first time you hold the hotkey and try to paste — click *Allow*.

If you ever rebuild the .app or upgrade Python, the bundle's code hash changes and TCC silently invalidates the grants. Symptoms: hotkey stops responding, or paste makes a "funk" sound. Fix: remove the VButton entry from each pane and re-add it.

When launched via Login Items, stdout goes to the macOS unified log (use `Console.app` and filter for "VButton"). To get a regular file, see the "See what's happening" tip in the Troubleshooting section below — it shows how to relaunch with `> /tmp/vbutton_app.log 2>&1` redirection.

---

## Usage

Hold **Right Option**, speak, release. The transcript is pasted where your cursor is.

Click the menu-bar icon to change settings:

- **Hotkey** — Right/Left Option, Right Command, Right Control, Right Shift, F18–F20
- **Language** — Auto (Hebrew + English), Hebrew only, English only, Auto (all languages)
- **Grammar fix** — off, English, Hebrew, both (requires Gemini API key)
- **Bubble timeout** — how long the grammar-fix suggestion popover stays open
- **Auto-switch** — automatically switch your keyboard layout to match the transcribed language

---

## Configuration

All settings are stored in `~/Library/Application Support/VButton/config.json` and can be set from the menu. When running from the terminal (dev mode), you can also override with environment variables:

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

Environment variables only apply when you launch `vbutton.py` directly from a terminal (dev mode). The `.app` reads from `config.json`.

---

## Grammar fix (optional)

VButton can run transcripts through Gemini to fix grammar and phrasing. Get a free API key at <https://aistudio.google.com/apikey>, then click the menu-bar icon → **Set Gemini API key…** and paste it in.

When enabled, a small popover appears under the menu-bar icon after each transcript with an "Improved" version. Click **Replace text** to swap the pasted text for the fixed one, or **Copy** to copy the fix without replacing.

---

## Dev mode (run from terminal without building the .app)

For development or quick testing you can run `vbutton.py` directly without building a bundle:

```bash
./install.sh --dev          # skips the .app build; just sets up venv + deps + model
.venv/bin/python vbutton.py run       # starts the menu-bar app from terminal
.venv/bin/python vbutton.py once      # one-shot record-and-transcribe
.venv/bin/python vbutton.py warmup    # re-download / re-cache the model
```

When running this way, macOS treats `.venv/bin/python` (a symlink chain to the actual Python framework binary) as the app posting events. You'll need to grant Microphone / Input Monitoring / Accessibility / Automation to **Python** (the entry that appears as a Python rocket-ship icon), not to VButton. Note that a `brew upgrade python` will invalidate those grants. Dev mode and the installed .app **share the same bundle ID** in places, so don't run both at once — quit one before starting the other.

---

## Updating the installed .app after editing `vbutton.py`

When you change `vbutton.py`, the installed `~/Applications/VButton.app` does **not** pick up the edit automatically — and copying the source into the bundle doesn't help either. py2app freezes the code as a compiled `vbutton.pyc` **inside** `Contents/Resources/lib/pythonXY.zip`, and the bundle's bootstrap removes `Resources/` from `sys.path`. So `import vbutton` loads that frozen `.pyc` from the zip, never the loose `Resources/vbutton.py`. Until the frozen `.pyc` is refreshed, the app keeps running the old code.

For ordinary code edits, use the fast path:

```bash
./sync_app.sh
```

`sync_app.sh` will:

1. Compile your current `vbutton.py` with the bundle's **own** Python (so the bytecode magic matches the bundle exactly)
2. Replace the frozen `vbutton.pyc` inside `pythonXY.zip` — the module the app actually imports
3. Keep the loose `Resources/vbutton.py` in sync (cosmetic; not imported)
4. Quit and relaunch the app

The key advantage: it never touches the main executable's signature, so its **CDHash is unchanged and your TCC permissions survive** (no need to re-grant Microphone / Input Monitoring / Accessibility / Automation). A full `./install.sh` rebuild re-signs the bundle, which changes the hash and wipes those grants.

> Use `./sync_app.sh` for **code** edits. If you add, remove, or upgrade Python **dependencies** (anything in `requirements.txt`), the new packages aren't in `vbutton.py`, so re-run `./install.sh` for a full rebuild (and re-grant permissions afterward).

---

## Troubleshooting

**Hotkey doesn't trigger anything.**
Input Monitoring permission is missing or attached to a stale binary hash. Open **System Settings → Privacy & Security → Input Monitoring**, remove the VButton entry, re-add `~/Applications/VButton.app`, then quit and relaunch VButton (permissions are read at startup).

**Recording works but paste does nothing / plays the "funk" reject sound.**
Either:
- Automation permission missing → System Settings → Privacy & Security → Automation → toggle "VButton → System Events" ON.
- The .app's signature seal is broken (only possible if you rebuilt without `tools/postbuild_mlx.sh`'s re-sign step). Re-sign with `codesign --force --deep --sign - ~/Applications/VButton.app`, then reset TCC: `tccutil reset Accessibility com.ydan.vbutton.app; tccutil reset ListenEvent com.ydan.vbutton.app; tccutil reset AppleEvents com.ydan.vbutton.app`, then re-grant.

**Mic is silent / "very low peak rms" warning.**
Microphone permission isn't granted, or another app has exclusive control. Re-check System Settings → Microphone.

**Two paste events / Hebrew text appearing when language is English.**
You have two VButton instances running. Check with:
```bash
ps -ef | grep -iE "vbutton|VButton" | grep -v grep
launchctl list | grep vbutton
```
Kill any stale ones. Make sure you don't have both a launchd agent (`~/Library/LaunchAgents/com.ydan.vbutton.plist`) AND the .app in Login Items — pick one.

**See what's happening:**
The .app launched from Login Items writes logs to its stdout, which by default is discarded. To see logs, launch it manually with redirection:
```bash
pkill -f "VButton.app/Contents/MacOS/VButton"
/Users/$USER/Applications/VButton.app/Contents/MacOS/VButton > /tmp/vbutton_app.log 2>&1 &
tail -f /tmp/vbutton_app.log
```

---

## Uninstall

```bash
# Remove the .app from Login Items
osascript -e 'tell application "System Events" to delete login item "VButton"' 2>/dev/null

# Quit any running instance
pkill -f "VButton.app/Contents/MacOS/VButton" 2>/dev/null

# Remove TCC grants
tccutil reset Accessibility com.ydan.vbutton.app
tccutil reset ListenEvent com.ydan.vbutton.app
tccutil reset AppleEvents com.ydan.vbutton.app
tccutil reset Microphone com.ydan.vbutton.app

# Remove any leftover launchd agent (only if you ever ran the legacy install)
launchctl bootout "gui/$(id -u)/com.ydan.vbutton" 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.ydan.vbutton.plist

# Remove the .app, config, logs, and the cached Whisper model
rm -rf ~/Applications/VButton.app
rm -rf ~/Library/Application\ Support/VButton
rm -f /tmp/vbutton_app.log ~/Library/Logs/vbutton.log
rm -rf ~/.cache/huggingface/hub/models--mlx-community--whisper-large-v3-mlx

# Remove the project folder
rm -rf /path/to/VButton
```

---

## Project layout

```
vbutton.py                  main application (menu bar app + transcription)
app_main.py                 entry point used by py2app
install.sh                  one-command install (.app build + permissions setup)
sync_app.sh                 fast-update the installed .app after a code edit (no rebuild, keeps permissions)
build_app_py2app.sh         build standalone .app bundle (called by install.sh)
build_app.sh                DEPRECATED thin-wrapper builder — don't use
setup.py                    py2app build configuration
requirements.txt            Python dependencies
com.ydan.vbutton.plist.tmpl legacy LaunchAgent template (not used by the .app path)
assets/                     menu-bar icons (idle / busy, @1x/@2x/@3x)
tools/                      icon generators and post-build helpers
  postbuild_mlx.sh          copies mlx packages into the bundle + re-signs
VButton.icns                .app bundle icon
```

---

## Why these specific quirks exist

A few decisions in the code are not obvious — here's why:

- **Paste goes through `osascript` / System Events, not `CGEventPost`.** macOS silently drops synthetic `Cmd+V` from adhoc-signed apps when delivered to strict Cocoa apps (Notes, iTerm, Mail). System Events is Apple-signed, so events originating from it are trusted. Cost is ~80ms per paste — fine for voice dictation.
- **The .app is re-signed after the mlx postbuild.** py2app's signature seal is broken the moment we modify the stdlib zip (`pythonXY.zip`) or copy mlx packages into `Contents/Resources/lib/`. macOS's event delivery checks the seal; a broken seal = silently dropped events.
- **The hotkey listener is `pynput`-based** instead of using the macOS hotkey API. This was a tradeoff: cross-platform-ish code + global hold-detection at the cost of needing Input Monitoring instead of just Accessibility.
- **Clipboard is restored after 300ms** rather than left set to the transcription. This means power-users won't lose what they had on the clipboard, but if the paste target is slow to read the clipboard (rare), the restore can race.

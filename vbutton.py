#!/usr/bin/env python3
import json
import os
import queue
import subprocess
import sys
import threading
import time

CONFIG_PATH = os.path.expanduser("~/Library/Application Support/VButton/config.json")
HOTKEY_OPTIONS = [
    ("Right Option", "alt_r"),
    ("Left Option", "alt_l"),
    ("Right Command", "cmd_r"),
    ("Right Control", "ctrl_r"),
    ("Right Shift", "shift_r"),
    ("F18", "f18"),
    ("F19", "f19"),
    ("F20", "f20"),
]

LANGUAGE_OPTIONS = [
    ("Auto", "auto_heb_en"),
    ("Hebrew only", "he"),
    ("English only", "en"),
    ("Auto (all languages)", "auto_all"),
]
ALLOWED_LANGS = {"he", "en"}

GRAMMAR_FIX_OPTIONS = [
    ("Off", "off"),
    ("English only", "en"),
    ("Hebrew only", "he"),
    ("Both", "both"),
]
POPOVER_TIMEOUT_OPTIONS = [
    ("2 seconds", 2),
    ("5 seconds", 5),
    ("Never", 0),
]
POPOVER_TIMEOUT_MAX = 600  # cap user-entered custom values at 10 minutes
# Bubble background tint. 0.0 = default macOS popover (translucent vibrancy);
# 1.0 = fully opaque. Edit just this number to taste.
POPOVER_BG_OPACITY = 0.3
GEMINI_MODEL = "gemini-2.5-flash-lite"
GEMINI_ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
GRAMMAR_TIMEOUT = 10
GRAMMAR_MIN_CHARS = 8
GRAMMAR_PROMPTS = {
    "en": "Fix English grammar and phrasing. Keep meaning identical. Reply with only the corrected text, no quotes, no explanations.",
    "he": "תקן את הדקדוק והניסוח בעברית. שמור על המשמעות. החזר רק את הטקסט המתוקן, ללא מרכאות וללא הסברים.",
}


def _load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


_CFG = _load_config()
HOTKEY_NAME = os.environ.get("VBUTTON_HOTKEY") or _CFG.get("hotkey", "alt_r")
SILENT = os.environ.get("VBUTTON_SILENT") == "1"
try:
    SOUND_VOLUME = float(os.environ.get("VBUTTON_SOUND_VOLUME") or _CFG.get("sound_volume", 1.0))
except (TypeError, ValueError):
    SOUND_VOLUME = 1.0
SOUND_VOLUME = max(0.0, min(1.0, SOUND_VOLUME))
BACKEND = os.environ.get("VBUTTON_BACKEND", _CFG.get("backend", "mlx"))
MODEL = os.environ.get("VBUTTON_MODEL") or _CFG.get(
    "model",
    "mlx-community/whisper-large-v3-mlx" if BACKEND == "mlx" else "large-v3-turbo",
)
COMPUTE_TYPE = os.environ.get("VBUTTON_COMPUTE", "int8")
LANGUAGE = os.environ.get("VBUTTON_LANGUAGE") or _CFG.get("language", "auto_heb_en")
GRAMMAR_FIX_MODE = os.environ.get("VBUTTON_GRAMMAR_FIX") or _CFG.get("grammar_fix_mode", "en")
GEMINI_KEY = os.environ.get("VBUTTON_GEMINI_KEY") or _CFG.get("gemini_api_key", "")
try:
    POPOVER_TIMEOUT = int(os.environ.get("VBUTTON_POPOVER_TIMEOUT") or _CFG.get("popover_timeout", 5))
except (TypeError, ValueError):
    POPOVER_TIMEOUT = 5
TEMPERATURE_FALLBACK = (0.0, 0.2, 0.4, 0.6, 0.8)
COMPRESSION_RATIO_THRESHOLD = 2.2
LOGPROB_THRESHOLD = -1.0

MATCH_LAYOUT_DEFAULT = bool(_CFG.get("match_layout", True))
PASTE_AT_ORIGIN_DEFAULT = bool(_CFG.get("paste_at_origin", True))
HISTORY_SIZE_MAX = 50
try:
    HISTORY_SIZE = int(_CFG.get("history_size", 5))
except (TypeError, ValueError):
    HISTORY_SIZE = 5
HISTORY_SIZE = max(1, min(HISTORY_SIZE_MAX, HISTORY_SIZE))
LAYOUT_MATCHERS = {
    "he": ("hebrew",),
    "en": ("abc", ".us", "british", "english", "australian", "canadian", "irish"),
}


def _tis_load():
    try:
        import objc
        from Foundation import NSBundle
        bundle = NSBundle.bundleWithPath_("/System/Library/Frameworks/Carbon.framework")
        if bundle is None:
            return None
        ns = {}
        objc.loadBundleFunctions(bundle, ns, [
            ("TISCopyCurrentKeyboardInputSource", b"@"),
            ("TISCreateInputSourceList", b"@@B"),
            ("TISGetInputSourceProperty", b"@@@"),
            ("TISSelectInputSource", b"i@"),
        ])
        objc.loadBundleVariables(bundle, ns, [
            ("kTISPropertyInputSourceID", b"@"),
        ])
        return ns
    except Exception as e:
        print(f"[vbutton] cannot load Carbon TIS: {e}", flush=True)
        return None


_TIS = None


def _tis():
    global _TIS
    if _TIS is None:
        _TIS = _tis_load() or {}
    return _TIS


def _current_layout_id():
    api = _tis()
    if not api:
        return None
    src = api["TISCopyCurrentKeyboardInputSource"]()
    if not src:
        return None
    sid = api["TISGetInputSourceProperty"](src, api["kTISPropertyInputSourceID"])
    return str(sid) if sid else None


def _all_layouts():
    api = _tis()
    if not api:
        return []
    srcs = api["TISCreateInputSourceList"](None, False)
    out = []
    if not srcs:
        return out
    key = api["kTISPropertyInputSourceID"]
    for src in srcs:
        sid = api["TISGetInputSourceProperty"](src, key)
        if sid:
            out.append((str(sid), src))
    return out


_VK_DELETE = 0x33  # kVK_Delete (Backspace)


def _find_layout_obj(sid_substring):
    for sid, obj in _all_layouts():
        if sid_substring in sid:
            return obj
    return None


def _send_cmd_v():
    # Cocoa apps (Notes, iTerm, Mail, …) silently drop synthetic
    # CGEventPost(kCGHIDEventTap, …) Cmd+V from adhoc-signed bundles — you
    # get a "funk" reject sound instead of a paste. Routing through Apple's
    # System Events (which is Apple-signed) makes the event trusted and
    # accepted everywhere. Costs ~80ms per paste; requires Automation
    # permission ("VButton wants to control System Events") on first use.
    # key code 9 is the physical V key (layout-independent). `keystroke "v"`
    # looks up the character "v" in the active layout — on Hebrew/Russian/etc.
    # that misfires (Cmd+A "select all" was the observed failure), since those
    # layouts have no "v" character.
    subprocess.run(
        ["/usr/bin/osascript", "-e",
         'tell application "System Events" to key code 9 using command down'],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _send_cmd_c():
    # key code 8 is the physical C key (layout-independent), same rationale as
    # _send_cmd_v above.
    subprocess.run(
        ["/usr/bin/osascript", "-e",
         'tell application "System Events" to key code 8 using command down'],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _select_left_n(n):
    """Shift+Left N times — extends selection N chars backwards from cursor."""
    if n <= 0:
        return
    script = (
        'tell application "System Events"\n'
        f'  repeat {n} times\n'
        '    key code 123 using shift down\n'
        '  end repeat\n'
        'end tell'
    )
    subprocess.run(
        ["/usr/bin/osascript", "-e", script],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _press_right_arrow():
    """Collapse a selection by moving the cursor right one step."""
    subprocess.run(
        ["/usr/bin/osascript", "-e",
         'tell application "System Events" to key code 124'],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _send_backspaces(n, per_key_delay=0.003):
    """Post N backspace key events at the cursor. Works in text fields and Terminal alike."""
    if n <= 0:
        return
    from Quartz import CGEventCreateKeyboardEvent, CGEventPost, kCGHIDEventTap
    for _ in range(n):
        down = CGEventCreateKeyboardEvent(None, _VK_DELETE, True)
        CGEventPost(kCGHIDEventTap, down)
        up = CGEventCreateKeyboardEvent(None, _VK_DELETE, False)
        CGEventPost(kCGHIDEventTap, up)
        if per_key_delay:
            time.sleep(per_key_delay)


def _snapshot_pasteboard(pb):
    """Capture every type's data for every item currently on the pasteboard."""
    items = pb.pasteboardItems() or []
    snapshot = []
    for item in items:
        data_map = {}
        for t in list(item.types() or []):
            data = item.dataForType_(t)
            if data is not None:
                data_map[str(t)] = data
        if data_map:
            snapshot.append(data_map)
    return snapshot


def _restore_pasteboard(pb, snapshot):
    from AppKit import NSPasteboardItem

    pb.clearContents()
    if not snapshot:
        return
    new_items = []
    for data_map in snapshot:
        item = NSPasteboardItem.alloc().init()
        for t, data in data_map.items():
            item.setData_forType_(data, t)
        new_items.append(item)
    if new_items:
        pb.writeObjects_(new_items)


def _paste_text(text, _keyboard_unused):
    """Deliver Unicode `text` at the cursor via clipboard + Cmd+V. Works regardless of active keyboard layout. Restores the full prior clipboard after Cmd+V is processed."""
    from AppKit import NSPasteboard

    pb = NSPasteboard.generalPasteboard()
    NSPasteboardTypeString = "public.utf8-plain-text"
    snapshot = _snapshot_pasteboard(pb)
    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)
    expected_count = pb.changeCount()
    _send_cmd_v()

    def _restore():
        time.sleep(0.3)
        try:
            # Don't clobber if the user (or another app) copied something new during the delay
            if pb.changeCount() != expected_count:
                return
            _restore_pasteboard(pb, snapshot)
        except Exception as e:
            print(f"[vbutton] clipboard restore failed: {e}", file=sys.stderr, flush=True)

    threading.Thread(target=_restore, daemon=True).start()


def _on_main_sync(fn):
    try:
        from Foundation import NSBlockOperation, NSOperationQueue
        op = NSBlockOperation.blockOperationWithBlock_(fn)
        NSOperationQueue.mainQueue().addOperations_waitUntilFinished_([op], True)
    except Exception as e:
        print(f"[vbutton] main-thread dispatch failed: {e}", file=sys.stderr, flush=True)
        fn()


NSApplicationActivateIgnoringOtherApps = 1 << 1


def _capture_frontmost_app():
    """The app that was frontmost when recording started, so we can paste back
    into it even if the user switched windows during transcription. Returns None
    (paste wherever focus is, i.e. old behavior) if VButton itself is frontmost
    or the lookup fails."""
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None or app.processIdentifier() == os.getpid():
            return None
        return app
    except Exception as e:
        print(f"[vbutton] capture frontmost app failed: {e}", file=sys.stderr, flush=True)
        return None


def _activate_app(app):
    """Bring a captured NSRunningApplication back to the front before pasting.
    Call on the main thread."""
    if app is None:
        return
    try:
        app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
    except Exception as e:
        print(f"[vbutton] activate origin app failed: {e}", file=sys.stderr, flush=True)


def _select_layout_handle(src_obj):
    api = _tis()
    if not api:
        return False
    return api["TISSelectInputSource"](src_obj) == 0


def _layout_matches(sid, needles):
    s = sid.lower()
    return any(n in s for n in needles)


def _switch_layout_for_lang(lang):
    needles = LAYOUT_MATCHERS.get(lang)
    if not needles:
        return False
    current = _current_layout_id()
    if current and _layout_matches(current, needles):
        return True
    keyboard_layouts = [(sid, obj) for sid, obj in _all_layouts() if "keylayout." in sid]
    for sid, obj in keyboard_layouts:
        if _layout_matches(sid, needles):
            ok = _select_layout_handle(obj)
            if ok:
                time.sleep(0.05)
            return ok
    return False


SAMPLE_RATE = 16000
MAX_SECONDS = 120
DEBOUNCE_MS = 200
MIN_AUDIO_SECONDS = 0.4
SILENCE_PEAK_THRESHOLD = 0.01

HALLUCINATION_PHRASES = {
    "thank you.", "thank you", "thanks for watching.", "thanks for watching",
    "thank you for watching.", "thank you for watching", "i'll see you next time.",
    "i'll see you next time", "subscribe to my channel.", "subscribe to my channel",
    "please subscribe.", "please subscribe", "bye.", "bye", "bye!", "you", ".",
    "..", "...", "[music]", "[applause]", "(music)", "(applause)", "okay.",
    "תודה.", "תודה", "תודה רבה.", "תודה רבה", "להתראות.", "להתראות",
    "תודה לכם על הצפייה.", "תודה לכם על הצפייה", "תודה רבה לכם.",
    "תודה רבה לכולם.", "ביי.", "ביי", "בסדר.",
}


def _is_hallucination(text):
    t = text.strip().lower()
    if len(t) < 3:
        return True
    return t in HALLUCINATION_PHRASES


HELP = """vbutton - push-to-talk Hebrew/English dictation

Usage:
  vbutton run          start the daemon (hold the hotkey to dictate)
  vbutton once         one-shot CLI test (Enter to start, Enter to stop)
  vbutton warmup       download + load the model, exit (use after install)
  vbutton --help       this message

Env:
  VBUTTON_HOTKEY=alt_r|alt|cmd_r|f18 ...   default: alt_r (Right Option)
  VBUTTON_SILENT=1                         suppress start/stop beeps
  VBUTTON_MODEL=large-v3-turbo             faster-whisper model name
  VBUTTON_COMPUTE=int8                     int8 | int8_float32 | float32
  VBUTTON_LANGUAGE=                        force lang ("he", "en"); empty = auto

macOS permissions needed (System Settings -> Privacy & Security):
  - Microphone:       grant to your terminal (or Python)
  - Accessibility:    grant to Python (for typing at cursor)
  - Input Monitoring: grant to Python (for global hotkey)
"""


def _play(name):
    if SILENT or SOUND_VOLUME <= 0.0:
        return
    subprocess.Popen(
        ["afplay", "-v", f"{SOUND_VOLUME:.3f}", f"/System/Library/Sounds/{name}.aiff"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _load_model():
    if BACKEND == "mlx":
        import mlx_whisper  # noqa: F401  (just ensures it's importable)
        return MODEL  # mlx-whisper caches the loaded model internally by path
    from faster_whisper import WhisperModel
    return WhisperModel(MODEL, device="cpu", compute_type=COMPUTE_TYPE, cpu_threads=os.cpu_count() or 8)


def _warmup(model):
    import numpy as np
    _transcribe(model, np.zeros(SAMPLE_RATE, dtype="float32"))


def _resolve_hotkey():
    from pynput.keyboard import Key

    name = HOTKEY_NAME.strip().lower()
    if not hasattr(Key, name):
        raise SystemExit(f"VBUTTON_HOTKEY={HOTKEY_NAME!r} is not a valid pynput Key name")
    return getattr(Key, name)


def _mlx_run(model, audio, language):
    import mlx_whisper
    kwargs = dict(
        path_or_hf_repo=model,
        condition_on_previous_text=False,
        temperature=TEMPERATURE_FALLBACK,
        compression_ratio_threshold=COMPRESSION_RATIO_THRESHOLD,
        logprob_threshold=LOGPROB_THRESHOLD,
        no_speech_threshold=0.6,
        word_timestamps=False,
    )
    if language:
        kwargs["language"] = language
    r = mlx_whisper.transcribe(audio, **kwargs)
    text = (r.get("text") or "").strip()
    lang = r.get("language", "")
    segs = r.get("segments") or []
    scores = [s.get("avg_logprob", -10.0) for s in segs if "avg_logprob" in s]
    score = sum(scores) / len(scores) if scores else -10.0
    return text, lang, score


def _fw_run(model, audio, language):
    kwargs = dict(
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        condition_on_previous_text=False,
        no_speech_threshold=0.6,
        temperature=TEMPERATURE_FALLBACK,
        compression_ratio_threshold=COMPRESSION_RATIO_THRESHOLD,
        log_prob_threshold=LOGPROB_THRESHOLD,
    )
    if language:
        kwargs["language"] = language
    segments, info = model.transcribe(audio, **kwargs)
    segs = list(segments)
    parts = [s.text for s in segs if s.no_speech_prob <= 0.85]
    scores = [s.avg_logprob for s in segs if hasattr(s, "avg_logprob")]
    score = sum(scores) / len(scores) if scores else -10.0
    return "".join(parts).strip(), info.language, score


def _run_once(model, audio, language):
    return (_mlx_run if BACKEND == "mlx" else _fw_run)(model, audio, language)


def _transcribe(model, audio, *, prev_text=""):
    mode = LANGUAGE
    if mode in ("he", "en"):
        text, lang, _ = _run_once(model, audio, mode)
        return text, lang
    if mode == "auto_all":
        text, lang, _ = _run_once(model, audio, None)
        return text, lang
    # auto_heb_en: try auto first; if it picks anything other than he/en, retry both and keep the best
    text, lang, score = _run_once(model, audio, None)
    if lang in ALLOWED_LANGS:
        return text, lang
    print(f"[vbutton] auto picked '{lang}', retrying with he/en", flush=True)
    candidates = [(text, lang, score)]
    for forced in ("he", "en"):
        try:
            candidates.append(_run_once(model, audio, forced))
        except Exception as e:
            print(f"[vbutton] retry({forced}) failed: {e}", flush=True)
    candidates = [c for c in candidates if c[1] in ALLOWED_LANGS] or candidates
    best = max(candidates, key=lambda c: c[2])
    return best[0], best[1]


def _fix_grammar(text, lang, api_key, timeout=GRAMMAR_TIMEOUT):
    import json as _json
    import urllib.error
    import urllib.request

    if not api_key or not text or lang not in GRAMMAR_PROMPTS:
        return None
    body = {
        "contents": [{"parts": [{"text": f"{GRAMMAR_PROMPTS[lang]}\n\n{text}"}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 400},
    }
    req = urllib.request.Request(
        GEMINI_ENDPOINT,
        data=_json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"[vbutton] grammar fix request failed: {e}", file=sys.stderr, flush=True)
        return None
    except Exception as e:
        print(f"[vbutton] grammar fix error: {e}", file=sys.stderr, flush=True)
        return None
    try:
        parts = data["candidates"][0]["content"]["parts"]
        out = "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, TypeError):
        print(f"[vbutton] grammar fix: unexpected response shape: {data!r}", file=sys.stderr, flush=True)
        return None
    # Strip a single layer of surrounding quotes if the model added them
    for q in ('"', "'", "`", "“", "”"):
        if len(out) >= 2 and out.startswith(q) and out.endswith(q):
            out = out[1:-1].strip()
            break
    return out or None


def _diff_spans(original, corrected):
    """Word-level diff. Returns (orig_spans, new_spans), each a list of (text, kind).
    kind in {'equal','change'}: 'change' marks tokens deleted from original or
    inserted in corrected (including the both-sides parts of replacements).
    """
    import difflib
    import re

    def tok(s):
        return re.findall(r"\S+|\s+", s)

    a = tok(original)
    b = tok(corrected)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    orig_spans, new_spans = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        a_seg = "".join(a[i1:i2])
        b_seg = "".join(b[j1:j2])
        if tag == "equal":
            if a_seg:
                orig_spans.append((a_seg, "equal"))
            if b_seg:
                new_spans.append((b_seg, "equal"))
        elif tag == "delete":
            if a_seg:
                orig_spans.append((a_seg, "change"))
        elif tag == "insert":
            if b_seg:
                new_spans.append((b_seg, "change"))
        elif tag == "replace":
            if a_seg:
                orig_spans.append((a_seg, "change"))
            if b_seg:
                new_spans.append((b_seg, "change"))
    return orig_spans, new_spans


_POPOVER_OBJC_CLASSES = None


def _load_popover_objc_classes():
    global _POPOVER_OBJC_CLASSES
    if _POPOVER_OBJC_CLASSES is not None:
        return _POPOVER_OBJC_CLASSES
    import objc
    from AppKit import NSButton, NSColor, NSTrackingArea, NSView
    from Foundation import NSObject

    _HOVER_TRACK_OPTS = 0x01 | 0x80 | 0x200  # MouseEnteredAndExited | ActiveAlways | InVisibleRect

    class _VButtonHoverIconButton(NSButton):
        """Icon button whose symbol turns blue while the mouse is over it."""

        def _install_tracking(self):
            for ta in list(self.trackingAreas()):
                self.removeTrackingArea_(ta)
            ta = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(), _HOVER_TRACK_OPTS, self, None,
            )
            self.addTrackingArea_(ta)

        def updateTrackingAreas(self):
            objc.super(_VButtonHoverIconButton, self).updateTrackingAreas()
            self._install_tracking()

        def mouseEntered_(self, _event):
            self.setContentTintColor_(NSColor.systemBlueColor())

        def mouseExited_(self, _event):
            self.setContentTintColor_(None)

    class _VButtonFlippedView(NSView):
        def isFlipped(self):
            return True

        def resetCursorRects(self):
            rects = getattr(self, "_hand_rects", None)
            if not rects:
                return
            from AppKit import NSCursor
            hand = NSCursor.pointingHandCursor()
            for r in rects:
                self.addCursorRect_cursor_(r, hand)

        def mouseEntered_(self, _event):
            cb = getattr(self, "_on_enter", None)
            if cb is not None:
                cb()

        def mouseExited_(self, _event):
            cb = getattr(self, "_on_exit", None)
            if cb is not None:
                cb()

    class _VButtonPopoverHandler(NSObject):
        def initWithPaste_copy_popover_(self, paste, copy, popover_ref):
            self = objc.super(_VButtonPopoverHandler, self).init()
            if self is None:
                return None
            self._paste = paste
            self._copy = copy
            self._popover_ref = popover_ref
            self._copy_original = None
            return self

        def doPaste_(self, _sender):
            pop = self._popover_ref[0]
            if pop is not None:
                pop.close()
            self._paste()

        def doCopy_(self, _sender):
            pop = self._popover_ref[0]
            if pop is not None:
                pop.close()
            self._copy()

        def doCopyOriginal_(self, _sender):
            pop = self._popover_ref[0]
            if pop is not None:
                pop.close()
            cb = self._copy_original
            if cb is not None:
                cb()

        def doClose_(self, _sender):
            pop = self._popover_ref[0]
            if pop is not None:
                pop.close()

    _POPOVER_OBJC_CLASSES = (_VButtonFlippedView, _VButtonPopoverHandler, _VButtonHoverIconButton)
    return _POPOVER_OBJC_CLASSES


_SETTINGS_WINDOW_CLASS = None


def _load_settings_window_class():
    global _SETTINGS_WINDOW_CLASS
    if _SETTINGS_WINDOW_CLASS is not None:
        return _SETTINGS_WINDOW_CLASS
    import objc
    from AppKit import NSApplication
    from Foundation import NSObject

    class _SettingsWindowCtrl(NSObject):
        def initWithApp_window_state_(self, app, window, state):
            self = objc.super(_SettingsWindowCtrl, self).init()
            if self is None:
                return None
            self._app = app
            self._window = window
            self._state = state
            return self

        def hotkeyChanged_(self, sender):
            codes = self._state["hotkey_codes"]
            idx = int(sender.indexOfSelectedItem())
            if 0 <= idx < len(codes):
                self._app.set_hotkey(codes[idx])

        def languageChanged_(self, sender):
            codes = self._state["language_codes"]
            idx = int(sender.indexOfSelectedItem())
            if 0 <= idx < len(codes):
                self._app.set_language(codes[idx])

        def grammarChanged_(self, sender):
            codes = self._state["grammar_codes"]
            idx = int(sender.indexOfSelectedItem())
            if 0 <= idx < len(codes):
                self._app.set_grammar_fix_mode(codes[idx])

        def timeoutCommitted_(self, sender):
            raw = (sender.stringValue() or "").strip()
            try:
                seconds = int(raw)
            except ValueError:
                sender.setStringValue_(str(self._app.popover_timeout))
                return
            if seconds < 0:
                seconds = 0
            if seconds > POPOVER_TIMEOUT_MAX:
                seconds = POPOVER_TIMEOUT_MAX
            sender.setStringValue_(str(seconds))
            self._app.set_popover_timeout(seconds)

        def autoSwitchChanged_(self, sender):
            self._app.set_match_layout(sender.state() == 1)

        def pasteAtOriginChanged_(self, sender):
            self._app.set_paste_at_origin(sender.state() == 1)

        def soundVolumeChanged_(self, sender):
            self._app.set_sound_volume(sender.doubleValue())
            # Play a sample at the new level so the user hears the scale.
            self._app.play_test_sound()

        def historyCommitted_(self, sender):
            raw = (sender.stringValue() or "").strip()
            try:
                n = int(raw)
            except ValueError:
                sender.setStringValue_(str(self._app.history_size))
                return
            n = max(1, min(HISTORY_SIZE_MAX, n))
            sender.setStringValue_(str(n))
            self._app.set_history_size(n)

        def toggleShowKey_(self, sender):
            secure = self._state["secure"]
            plain = self._state["plain"]
            show = sender.state() == 1
            if show:
                plain.setStringValue_(secure.stringValue())
                secure.setHidden_(True)
                plain.setHidden_(False)
                self._window.makeFirstResponder_(plain)
            else:
                secure.setStringValue_(plain.stringValue())
                plain.setHidden_(True)
                secure.setHidden_(False)
                self._window.makeFirstResponder_(secure)

        def _commit_pending(self):
            timeout_field = self._state.get("timeout_field")
            if timeout_field is not None:
                raw = (timeout_field.stringValue() or "").strip()
                try:
                    seconds = int(raw)
                    if seconds < 0:
                        seconds = 0
                    if seconds > POPOVER_TIMEOUT_MAX:
                        seconds = POPOVER_TIMEOUT_MAX
                    if seconds != self._app.popover_timeout:
                        self._app.set_popover_timeout(seconds)
                except ValueError:
                    pass

            history_field = self._state.get("history_field")
            if history_field is not None:
                raw = (history_field.stringValue() or "").strip()
                try:
                    n = max(1, min(HISTORY_SIZE_MAX, int(raw)))
                    if n != self._app.history_size:
                        self._app.set_history_size(n)
                except ValueError:
                    pass

            secure = self._state.get("secure")
            plain = self._state.get("plain")
            if secure is not None and plain is not None:
                visible = plain if secure.isHidden() else secure
                self._app.set_gemini_key(visible.stringValue().strip())

        def doDone_(self, _sender):
            self._commit_pending()
            NSApplication.sharedApplication().stopModal()
            self._window.orderOut_(None)

        def windowShouldClose_(self, _sender):
            self._commit_pending()
            NSApplication.sharedApplication().stopModal()
            self._window.orderOut_(None)
            return False

    _SETTINGS_WINDOW_CLASS = _SettingsWindowCtrl
    return _SETTINGS_WINDOW_CLASS


def _show_grammar_popover(
    anchor_button, original, corrected, on_paste, on_copy, retain_box,
    *, auto_close_seconds=5, on_copy_original=None,
):
    from AppKit import (
        NSApplication,
        NSBezelStyleRounded,
        NSButton,
        NSColor,
        NSFont,
        NSFontAttributeName,
        NSForegroundColorAttributeName,
        NSImage,
        NSPopover,
        NSTextField,
        NSTextView,
        NSTimer,
        NSTrackingArea,
        NSViewController,
    )
    from Foundation import NSMakeRange, NSMakeRect, NSMakeSize, NSMutableAttributedString

    NSPopoverBehaviorTransient = 1
    NSPopoverBehaviorSemitransient = 2
    NSRectEdgeMinY = 3
    NSLineBreakByWordWrapping = 0
    NSImageOnly = 2
    NSFocusRingTypeNone = 1
    NSBezelStyleShadowlessSquare = 6
    NSTrackingMouseEnteredAndExited = 0x01
    NSTrackingActiveAlways = 0x80
    NSTrackingInVisibleRect = 0x200

    FlippedView, Handler, HoverIconButton = _load_popover_objc_classes()

    width = 380
    pad = 12
    inner_width = width - 2 * pad
    gap_small = 4
    gap = 10
    btn_h = 26
    icon_size = 16

    def _label(text, font, color, max_width):
        lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, max_width, 20))
        lbl.setStringValue_(text)
        lbl.setEditable_(False)
        lbl.setSelectable_(True)
        lbl.setBordered_(False)
        lbl.setDrawsBackground_(False)
        lbl.setFont_(font)
        lbl.setTextColor_(color)
        lbl.cell().setWraps_(True)
        lbl.cell().setLineBreakMode_(NSLineBreakByWordWrapping)
        lbl.setPreferredMaxLayoutWidth_(max_width)
        size = lbl.sizeThatFits_(NSMakeSize(max_width, 10000))
        lbl.setFrame_(NSMakeRect(0, 0, max_width, size.height))
        return lbl

    def _diff_label(spans, font, base_color, change_color, max_width):
        text = "".join(s for s, _ in spans)
        attr = NSMutableAttributedString.alloc().initWithString_(text)
        full = NSMakeRange(0, len(text))
        attr.addAttribute_value_range_(NSFontAttributeName, font, full)
        attr.addAttribute_value_range_(NSForegroundColorAttributeName, base_color, full)
        offset = 0
        for seg, kind in spans:
            n = len(seg)
            if kind == "change" and n > 0:
                attr.addAttribute_value_range_(
                    NSForegroundColorAttributeName, change_color, NSMakeRange(offset, n)
                )
            offset += n
        tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, max_width, 10000))
        tv.setEditable_(False)
        tv.setSelectable_(True)
        tv.setDrawsBackground_(False)
        tv.setTextContainerInset_(NSMakeSize(0, 0))
        tc = tv.textContainer()
        tc.setLineFragmentPadding_(0)
        tc.setContainerSize_(NSMakeSize(max_width, 1.0e7))
        tc.setWidthTracksTextView_(False)
        tv.textStorage().setAttributedString_(attr)
        lm = tv.layoutManager()
        lm.ensureLayoutForTextContainer_(tc)
        used = lm.usedRectForTextContainer_(tc)
        tv.setFrame_(NSMakeRect(0, 0, max_width, used.size.height))
        return tv

    font_section = NSFont.systemFontOfSize_(11)
    font_body = NSFont.systemFontOfSize_(12)
    font_improved = NSFont.boldSystemFontOfSize_(13)

    orig_spans, new_spans = _diff_spans(original, corrected)
    print(
        f"[vbutton] popover diff: orig_changes={sum(1 for _,k in orig_spans if k=='change')} "
        f"new_changes={sum(1 for _,k in new_spans if k=='change')}",
        flush=True,
    )

    # Darker red/green for the diff highlights — system colors were too pale
    # to spot at a glance.
    dark_red = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.70, 0.10, 0.10, 1.0)
    dark_green = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.10, 0.45, 0.15, 1.0)

    title_orig = _label("Original:", font_section, NSColor.secondaryLabelColor(), inner_width)
    body_orig = _diff_label(
        orig_spans, font_body,
        NSColor.labelColor(), dark_red, inner_width,
    )
    title_impr = _label("Improved:", font_section, NSColor.labelColor(), inner_width)
    body_impr = _diff_label(
        new_spans, font_improved,
        NSColor.labelColor(), dark_green, inner_width,
    )

    total_h = (
        pad
        + title_orig.frame().size.height + gap_small + body_orig.frame().size.height
        + gap + title_impr.frame().size.height + gap_small + body_impr.frame().size.height
        + gap + btn_h + pad
    )

    content = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, width, total_h))
    if POPOVER_BG_OPACITY > 0:
        content.setWantsLayer_(True)
        content.layer().setBackgroundColor_(
            NSColor.colorWithCalibratedWhite_alpha_(0.98, POPOVER_BG_OPACITY).CGColor()
        )

    y = pad
    for lbl in (title_orig, body_orig, title_impr, body_impr):
        h = lbl.frame().size.height
        lbl.setFrame_(NSMakeRect(pad, y, inner_width, h))
        content.addSubview_(lbl)
        if lbl is title_orig or lbl is title_impr:
            y += h + gap_small
        elif lbl is body_orig:
            y += h + gap
        else:
            y += h + gap

    popover_ref = [None]
    handler = Handler.alloc().initWithPaste_copy_popover_(on_paste, on_copy, popover_ref)
    handler._copy_original = on_copy_original

    def _icon_button(symbol, fallback, tooltip, action, frame, *, key=None):
        btn = HoverIconButton.alloc().initWithFrame_(frame)
        img = None
        try:
            img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, tooltip)
        except Exception:
            img = None
        if img is not None:
            btn.setImage_(img)
            btn.setImagePosition_(NSImageOnly)
        else:
            btn.setTitle_(fallback)
        # Icon-only. No border — the symbol itself turns blue on hover
        # (see _VButtonHoverIconButton) and dims on press.
        btn.setBordered_(False)
        btn.setFocusRingType_(NSFocusRingTypeNone)
        btn.setToolTip_(tooltip)
        btn.setTarget_(handler)
        btn.setAction_(action)
        if key:
            btn.setKeyEquivalent_(key)
        return btn

    def _icon_y_for(label):
        frame = label.frame()
        return frame.origin.y + (frame.size.height - icon_size) / 2

    icon_gap = 6

    # Top-right icons on the "Original:" row: close (X) at the far right, and
    # copy-original just to its left.
    icon_y = _icon_y_for(title_orig)
    close_btn = _icon_button(
        "xmark", "✕", "Close", "doClose:",
        NSMakeRect(width - pad - icon_size, icon_y, icon_size, icon_size),
    )
    content.addSubview_(close_btn)
    hand_rects = [close_btn.frame()]
    if on_copy_original is not None:
        copy_orig_btn = _icon_button(
            "doc.on.doc", "⧉", "Copy original to clipboard", "doCopyOriginal:",
            NSMakeRect(width - pad - 2 * icon_size - icon_gap, icon_y, icon_size, icon_size),
        )
        content.addSubview_(copy_orig_btn)
        hand_rects.append(copy_orig_btn.frame())
    content._hand_rects = hand_rects

    # Bottom action buttons — unicode glyph + text in the title (no NSImage)
    def _text_button(title, frame, action, *, key=None):
        btn = NSButton.alloc().initWithFrame_(frame)
        btn.setTitle_(title)
        btn.setBezelStyle_(NSBezelStyleRounded)
        btn.setTarget_(handler)
        btn.setAction_(action)
        if key:
            btn.setKeyEquivalent_(key)
        return btn

    btn_w = (inner_width - 8) / 2
    y_btn = total_h - pad - btn_h
    copy_btn = _text_button(
        "⧉  Copy",
        NSMakeRect(pad, y_btn, btn_w, btn_h),
        "doCopy:",
    )
    paste_btn = _text_button(
        "↻  Replace text",
        NSMakeRect(pad + btn_w + 8, y_btn, btn_w, btn_h),
        "doPaste:",
        key="\r",
    )
    content.addSubview_(copy_btn)
    content.addSubview_(paste_btn)

    vc = NSViewController.alloc().init()
    vc.setView_(content)

    pop = NSPopover.alloc().init()
    pop.setContentViewController_(vc)
    # Semitransient (not Transient) — Transient auto-closes when the app
    # deactivates, which fires the instant we hand focus back to the user's
    # previous app below.
    pop.setBehavior_(NSPopoverBehaviorSemitransient)
    pop.setContentSize_(NSMakeSize(width, total_h))
    popover_ref[0] = pop

    # Auto-close timer with hover pause.
    timer_box = {"timer": None}

    def _close_now():
        try:
            pop.close()
        except Exception:
            pass

    def _start_timer():
        if not auto_close_seconds or auto_close_seconds <= 0:
            return
        t = timer_box.get("timer")
        if t is not None:
            t.invalidate()
        timer_box["timer"] = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            float(auto_close_seconds), False, lambda _t: _close_now()
        )

    def _stop_timer():
        t = timer_box.get("timer")
        if t is not None:
            t.invalidate()
            timer_box["timer"] = None

    content._on_enter = _stop_timer
    content._on_exit = _start_timer

    tracking = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
        content.bounds(),
        NSTrackingMouseEnteredAndExited | NSTrackingActiveAlways | NSTrackingInVisibleRect,
        content,
        None,
    )
    content.addTrackingArea_(tracking)

    # Keep strong refs in the caller's box so nothing gets GC'd while the popover is on screen
    retain_box["popover"] = pop
    retain_box["handler"] = handler
    retain_box["vc"] = vc
    retain_box["content"] = content
    retain_box["tracking"] = tracking
    retain_box["timer_box"] = timer_box

    pop.showRelativeToRect_ofView_preferredEdge_(
        anchor_button.bounds(), anchor_button, NSRectEdgeMinY
    )
    try:
        win = content.window()
        if win is not None:
            win.invalidateCursorRectsForView_(content)
    except Exception:
        pass
    # Hand focus back to whatever the user was typing in. NSPopover otherwise
    # makes VButton the foreground app, which swallows keystrokes.
    NSApplication.sharedApplication().deactivate()
    _start_timer()
    return pop


class Recorder:
    def __init__(self):
        self.stop_event = threading.Event()
        self.q = queue.Queue()
        self._stream = None
        self._start_time = None
        self._meter = None

    def start(self, meter=None):
        import sounddevice as sd

        self.stop_event.clear()
        self.q = queue.Queue()
        self._start_time = time.time()
        self._meter = meter

        def cb(indata, frames, t, status):
            if status:
                print(f"\n[vbutton] mic status: {status}", file=sys.stderr, flush=True)
            arr = indata.copy().reshape(-1).astype("float32")
            self.q.put(arr)
            if self._meter is not None:
                self._meter(arr)
            if time.time() - self._start_time > MAX_SECONDS:
                self.stop_event.set()

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=int(SAMPLE_RATE * 0.1),
            callback=cb,
        )
        self._stream.start()

    def stop(self):
        import numpy as np

        self.stop_event.set()
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                print(f"[vbutton] stream close: {e}", file=sys.stderr, flush=True)
            self._stream = None
        chunks = []
        while True:
            try:
                chunks.append(self.q.get_nowait())
            except queue.Empty:
                break
        if not chunks:
            return np.zeros(0, dtype="float32")
        return np.concatenate(chunks)


def cmd_run():
    import rumps
    from pynput.keyboard import Controller, Key, Listener

    print(f"[vbutton] loading model {MODEL} (compute={COMPUTE_TYPE})...", flush=True)
    model = _load_model()
    _warmup(model)
    print(f"[vbutton] ready. Hold {HOTKEY_NAME} or click the menu bar icon.", flush=True)

    keyboard = Controller()
    rec = Recorder()
    lock = threading.Lock()

    try:
        import AppKit
        AppKit.NSApplication.sharedApplication().setActivationPolicy_(1)
    except Exception as e:
        print(f"[vbutton] early activation policy failed: {e}", file=sys.stderr, flush=True)

    STATE_IDLE = "idle"
    STATE_REC = "recording"
    STATE_BUSY = "transcribing"

    ICONS = {STATE_IDLE: "VBtn", STATE_REC: "VBtn●", STATE_BUSY: "VBtn…"}
    HOTKEY_LABELS_MAP = {key_name: label for label, key_name in HOTKEY_OPTIONS}
    LANGUAGE_LABELS_MAP = {code: label for label, code in LANGUAGE_OPTIONS}
    GRAMMAR_FIX_LABELS_MAP = {code: label for label, code in GRAMMAR_FIX_OPTIONS}
    POPOVER_TIMEOUT_LABELS_MAP = {seconds: label for label, seconds in POPOVER_TIMEOUT_OPTIONS}

    def _make_symbol_image(name, point_size=14.0, weight=-0.4, box=16.0):
        # weight uses NSFontWeight floats: -0.8 ultralight .. -0.4 light .. 0.0 regular .. 0.4 bold
        # box is the final pixel size (square) we force the image into, so it always fits the menu bar
        try:
            from AppKit import NSImage, NSImageSymbolConfiguration
            from Foundation import NSMakeSize
            img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
            if img is None:
                return None
            config = NSImageSymbolConfiguration.configurationWithPointSize_weight_(point_size, weight)
            img2 = img.imageWithSymbolConfiguration_(config) if config else img
            img2.setSize_(NSMakeSize(box, box))
            img2.setTemplate_(True)
            return img2
        except Exception as e:
            print(f"[vbutton] cannot load SF Symbol {name}: {e}", flush=True)
            return None

    def _make_asset_image(base_name, box=18.0):
        """Load `<base_name>.png`, `<base_name>@2x.png`, `<base_name>@3x.png`
        as separate NSBitmapImageRep instances on a single NSImage with
        logical size `box` pt. macOS will pick the rep matching the display's
        backing scale with no resampling, so the icon stays crisp on Retina.
        """
        try:
            from AppKit import NSImage, NSBitmapImageRep
            from Foundation import NSMakeSize
            # When bundled by py2app, __file__ points inside python314.zip.
            # py2app sets RESOURCEPATH to Contents/Resources/.
            here = os.environ.get("RESOURCEPATH") or os.path.dirname(os.path.abspath(__file__))
            img = NSImage.alloc().init()
            loaded = 0
            for suffix in ("", "@2x", "@3x"):
                path = os.path.join(here, "assets", f"{base_name}{suffix}.png")
                if not os.path.isfile(path):
                    continue
                rep = NSBitmapImageRep.imageRepWithContentsOfFile_(path)
                if rep is None:
                    continue
                # Force the rep's point size to `box` pt — its pixel count
                # stays at the file's resolution. e.g. a 36px file with
                # box=18 → 2x rep, picked automatically on a Retina display.
                rep.setSize_(NSMakeSize(box, box))
                img.addRepresentation_(rep)
                loaded += 1
            if loaded == 0:
                print(f"[vbutton] no menu bar assets found for {base_name}", flush=True)
                return None
            img.setSize_(NSMakeSize(box, box))
            img.setTemplate_(True)
            return img
        except Exception as e:
            print(f"[vbutton] cannot load asset {base_name}: {e}", flush=True)
            return None

    def _make_rec_dynamic_image(box=18.0):
        """V (label-color tinted) + red recording dot, with a thin silhouette gap.

        Drawn at request time via NSImage drawingHandler so the V follows the
        menu bar's text color through light/dark appearance changes; the dot
        stays system red regardless. V geometry and halo thickness match
        menubar_idle / menubar_busy so the menu bar icon doesn't jump size.
        """
        try:
            from AppKit import NSImage, NSColor, NSBezierPath, NSGraphicsContext
            from Foundation import NSMakeSize, NSMakeRect, NSMakePoint

            NSCompositingOperationDestinationOut = 8

            def _handler(rect):
                s = rect.size.width

                def p(fx, fy):
                    return NSMakePoint(fx * s, fy * s)

                # V — same fractions as menubar_idle / menubar_busy.
                # (flipped=True below: y grows downward, so these match the
                # PIL fractions used by make_busy_icon.py.)
                v = NSBezierPath.bezierPath()
                v.moveToPoint_(p(0.13, 0.17))
                v.lineToPoint_(p(0.50, 0.89))
                v.lineToPoint_(p(0.87, 0.17))
                v.lineToPoint_(p(0.67, 0.17))
                v.lineToPoint_(p(0.50, 0.50))
                v.lineToPoint_(p(0.33, 0.17))
                v.closePath()
                NSColor.labelColor().set()
                v.fill()

                dot_cx = 0.76 * s
                dot_cy = 0.66 * s
                dot_r = 0.155 * s
                halo_pad = 0.035 * s

                # Carve a circular gap around the dot so a thin silhouette
                # outline separates it from the V's right rib.
                ctx = NSGraphicsContext.currentContext()
                ctx.saveGraphicsState()
                ctx.setCompositingOperation_(NSCompositingOperationDestinationOut)
                NSColor.blackColor().set()
                halo_rect = NSMakeRect(
                    dot_cx - dot_r - halo_pad,
                    dot_cy - dot_r - halo_pad,
                    (dot_r + halo_pad) * 2,
                    (dot_r + halo_pad) * 2,
                )
                NSBezierPath.bezierPathWithOvalInRect_(halo_rect).fill()
                ctx.restoreGraphicsState()

                NSColor.systemRedColor().set()
                dot_rect = NSMakeRect(
                    dot_cx - dot_r, dot_cy - dot_r, dot_r * 2, dot_r * 2,
                )
                NSBezierPath.bezierPathWithOvalInRect_(dot_rect).fill()
                return True

            img = NSImage.imageWithSize_flipped_drawingHandler_(
                NSMakeSize(box, box), True, _handler,
            )
            return img
        except Exception as e:
            print(f"[vbutton] cannot build rec image: {e}", flush=True)
            return None

    SYMBOLS = {
        STATE_IDLE: _make_asset_image("menubar_idle") or _make_symbol_image("mic"),
        STATE_REC: _make_rec_dynamic_image() or _make_symbol_image("mic.fill"),
        STATE_BUSY: _make_asset_image("menubar_busy") or _make_symbol_image("waveform"),
    }

    class VButtonApp(rumps.App):
        HOTKEY_LABELS = HOTKEY_LABELS_MAP
        LANGUAGE_LABELS = LANGUAGE_LABELS_MAP
        GRAMMAR_FIX_LABELS = GRAMMAR_FIX_LABELS_MAP
        POPOVER_TIMEOUT_LABELS = POPOVER_TIMEOUT_LABELS_MAP

        def __init__(self):
            super().__init__(ICONS[STATE_IDLE], quit_button=None)
            self.state = STATE_IDLE
            self.press_time = 0.0
            self.prev_text = ""
            self.last_text = ""
            self.hotkey_name = HOTKEY_NAME
            self.hotkey = getattr(Key, HOTKEY_NAME, Key.alt_r)
            self.gemini_key = GEMINI_KEY
            self._popover_box = {}
            self.last_original = ""
            self.last_corrected = ""
            self._origin_app = None
            self.last_origin_app = None
            self.paste_at_origin = PASTE_AT_ORIGIN_DEFAULT
            self._transcribe_gen = 0
            self.sound_volume = SOUND_VOLUME
            self.history_size = HISTORY_SIZE
            self.history = [h for h in _CFG.get("history", []) if isinstance(h, str)][: self.history_size]

            self.toggle_item = rumps.MenuItem("Start Recording", callback=self.on_toggle)
            self.last_item = rumps.MenuItem("Last: (none)", callback=self.on_copy_last)

            self.hotkey_items = {}
            self.hotkey_menu = rumps.MenuItem(self._hotkey_menu_title())
            for label, key_name in HOTKEY_OPTIONS:
                item = rumps.MenuItem(label, callback=self._make_hotkey_setter(key_name))
                if key_name == self.hotkey_name:
                    item.state = 1
                self.hotkey_menu.add(item)
                self.hotkey_items[key_name] = item

            self.language_code = LANGUAGE if LANGUAGE in self.LANGUAGE_LABELS else "auto_heb_en"
            self.language_items = {}
            self.language_menu = rumps.MenuItem(self._language_menu_title())
            for label, code in LANGUAGE_OPTIONS:
                item = rumps.MenuItem(label, callback=self._make_language_setter(code))
                if code == self.language_code:
                    item.state = 1
                self.language_menu.add(item)
                self.language_items[code] = item

            self.grammar_fix_mode = GRAMMAR_FIX_MODE if GRAMMAR_FIX_MODE in self.GRAMMAR_FIX_LABELS else "en"
            self.grammar_fix_items = {}
            self.grammar_fix_menu = rumps.MenuItem(self._grammar_fix_menu_title())
            for label, code in GRAMMAR_FIX_OPTIONS:
                item = rumps.MenuItem(label, callback=self._make_grammar_fix_setter(code))
                if code == self.grammar_fix_mode:
                    item.state = 1
                self.grammar_fix_menu.add(item)
                self.grammar_fix_items[code] = item

            # Bubble timeout is configured in Settings only (no menu-bar submenu).
            self.popover_timeout = POPOVER_TIMEOUT if POPOVER_TIMEOUT >= 0 else 5

            self.history_menu = rumps.MenuItem("History")
            self._rebuild_history_menu()

            self.match_layout = MATCH_LAYOUT_DEFAULT
            self.match_layout_item = rumps.MenuItem("Auto-switch", callback=self.on_toggle_match_layout)
            self.match_layout_item.state = 1 if self.match_layout else 0

            self.settings_item = rumps.MenuItem("Settings…", callback=self.on_open_settings)
            self.quit_item = rumps.MenuItem("Quit", callback=self.on_quit)
            self.menu = [
                self.toggle_item,
                None,
                self.hotkey_menu,
                self.language_menu,
                self.grammar_fix_menu,
                self.match_layout_item,
                None,
                self.history_menu,
                self.last_item,
                None,
                self.settings_item,
                self.quit_item,
            ]

        def _hotkey_label(self, key_name=None):
            kn = key_name or self.hotkey_name
            return self.HOTKEY_LABELS.get(kn, kn)

        def _hotkey_menu_title(self):
            return "Hotkey"

        def _make_hotkey_setter(self, key_name):
            def setter(_sender):
                self.set_hotkey(key_name)
            return setter

        def set_hotkey(self, key_name):
            if not hasattr(Key, key_name):
                print(f"[vbutton] unknown key: {key_name}", flush=True)
                return
            for kn, item in self.hotkey_items.items():
                item.state = 1 if kn == key_name else 0
            self.hotkey_name = key_name
            self.hotkey = getattr(Key, key_name)
            self.hotkey_menu.title = self._hotkey_menu_title()
            cfg = _load_config()
            cfg["hotkey"] = key_name
            _save_config(cfg)
            print(f"[vbutton] hotkey changed to {key_name}", flush=True)

        def _language_label(self, code=None):
            c = code or self.language_code
            return self.LANGUAGE_LABELS.get(c, c)

        def _language_menu_title(self):
            return f"Language: {self._language_label()}"

        def _make_language_setter(self, code):
            def setter(_sender):
                self.set_language(code)
            return setter

        def set_language(self, code):
            global LANGUAGE
            if code not in self.LANGUAGE_LABELS:
                print(f"[vbutton] unknown language code: {code}", flush=True)
                return
            for c, item in self.language_items.items():
                item.state = 1 if c == code else 0
            self.language_code = code
            LANGUAGE = code
            self.language_menu.title = self._language_menu_title()
            cfg = _load_config()
            cfg["language"] = code
            _save_config(cfg)
            print(f"[vbutton] language mode changed to {code}", flush=True)

        def _grammar_fix_menu_title(self):
            return f"Grammar fix: {self.GRAMMAR_FIX_LABELS.get(getattr(self, 'grammar_fix_mode', 'off'), 'Off')}"

        def _make_grammar_fix_setter(self, code):
            def setter(_sender):
                self.set_grammar_fix_mode(code)
            return setter

        def set_grammar_fix_mode(self, code):
            if code not in self.GRAMMAR_FIX_LABELS:
                print(f"[vbutton] unknown grammar fix mode: {code}", flush=True)
                return
            for c, item in self.grammar_fix_items.items():
                item.state = 1 if c == code else 0
            self.grammar_fix_mode = code
            self.grammar_fix_menu.title = self._grammar_fix_menu_title()
            cfg = _load_config()
            cfg["grammar_fix_mode"] = code
            _save_config(cfg)
            print(f"[vbutton] grammar fix mode changed to {code}", flush=True)

        def set_popover_timeout(self, seconds):
            if seconds < 0:
                print(f"[vbutton] invalid popover timeout: {seconds}", flush=True)
                return
            self.popover_timeout = seconds
            cfg = _load_config()
            cfg["popover_timeout"] = seconds
            _save_config(cfg)
            print(f"[vbutton] popover timeout set to {seconds}s", flush=True)

        # ── History ──────────────────────────────────────────────────────
        def _history_preview(self, text):
            t = " ".join(text.split())
            return t if len(t) <= 40 else t[:39] + "…"

        def _rebuild_history_menu(self):
            # A rumps MenuItem has no submenu NSMenu until its first .add(), so
            # .clear() would dereference None on the very first build.
            if self.history_menu._menu is not None:
                self.history_menu.clear()
            if not self.history:
                self.history_menu.add(rumps.MenuItem("(empty)"))
                return
            for idx, entry in enumerate(self.history):
                self.history_menu.add(
                    rumps.MenuItem(
                        self._history_preview(entry),
                        callback=self._make_history_copier(idx),
                    )
                )

        def _make_history_copier(self, idx):
            def copier(_sender):
                if 0 <= idx < len(self.history):
                    self._copy_text(self.history[idx])
            return copier

        def _copy_text(self, text):
            try:
                subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
                rumps.notification("VButton", "Copied to clipboard", text[:80])
            except Exception as e:
                print(f"[vbutton] history copy failed: {e}", file=sys.stderr, flush=True)

        def _add_history(self, text):
            text = text.strip()
            if not text:
                return
            # Move an existing identical entry to the front rather than duplicating.
            self.history = [h for h in self.history if h != text]
            self.history.insert(0, text)
            del self.history[self.history_size:]
            cfg = _load_config()
            cfg["history"] = self.history
            _save_config(cfg)
            _on_main_sync(self._rebuild_history_menu)

        def set_history_size(self, n):
            try:
                n = int(n)
            except (TypeError, ValueError):
                return
            n = max(1, min(HISTORY_SIZE_MAX, n))
            self.history_size = n
            del self.history[n:]
            self._rebuild_history_menu()
            cfg = _load_config()
            cfg["history_size"] = n
            cfg["history"] = self.history
            _save_config(cfg)
            print(f"[vbutton] history size set to {n}", flush=True)

        def on_open_settings(self, _sender):
            import objc
            from AppKit import (
                NSApplication,
                NSBackingStoreBuffered,
                NSBezelStyleRounded,
                NSButton,
                NSColor,
                NSFont,
                NSImage,
                NSPopUpButton,
                NSSecureTextField,
                NSSlider,
                NSTabView,
                NSTabViewItem,
                NSTextField,
                NSView,
                NSWindow,
            )
            from Foundation import NSData, NSMakeRect, NSMakeSize

            NSWindowStyleMaskTitled = 1
            NSWindowStyleMaskClosable = 2
            NSSwitchButton = 3
            NSViewWidthSizable = 2
            NSViewHeightSizable = 16
            NSViewMinYMargin = 8

            here = os.environ.get("RESOURCEPATH") or os.path.dirname(os.path.abspath(__file__))
            icon_path = os.path.join(here, "VButton.icns")
            if os.path.isfile(icon_path):
                data = NSData.dataWithContentsOfFile_(icon_path)
                if data is not None:
                    icon = NSImage.alloc().initWithData_(data)
                    if icon is not None:
                        NSApplication.sharedApplication().setApplicationIconImage_(icon)

            W, H = 560, 400
            PAD = 20
            style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
            window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, W, H), style, NSBackingStoreBuffered, False,
            )
            window.setTitle_("VButton Settings")
            window.center()
            content = window.contentView()

            # Done button (bottom right)
            btn_w, btn_h = 96, 30
            btn_y = PAD
            done = NSButton.alloc().initWithFrame_(
                NSMakeRect(W - PAD - btn_w, btn_y, btn_w, btn_h)
            )
            done.setBezelStyle_(NSBezelStyleRounded)
            done.setTitle_("Done")
            done.setKeyEquivalent_("\r")
            content.addSubview_(done)

            # Tab view above the Done button
            tab_y_pos = btn_y + btn_h + 14
            tab_w = W - 2 * PAD
            tab_h = H - tab_y_pos - PAD
            tab_view = NSTabView.alloc().initWithFrame_(
                NSMakeRect(PAD, tab_y_pos, tab_w, tab_h)
            )
            content.addSubview_(tab_view)

            # Approximate content size inside a tab (Cocoa will fit/stretch)
            inner_w = tab_w - 12
            inner_h = tab_h - 35
            PAD_TAB = 16
            label_w = 110
            field_x = PAD_TAB + label_w + 8
            row_y_step = 38

            state = {}
            Ctrl = _load_settings_window_class()
            ctrl = Ctrl.alloc().initWithApp_window_state_(self, window, state)

            def _label(text, x, y_from_top, w, *, bold=False, secondary=False, height=18):
                y = inner_h - y_from_top - height
                l = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, height))
                l.setStringValue_(text)
                l.setBezeled_(False)
                l.setDrawsBackground_(False)
                l.setEditable_(False)
                l.setSelectable_(False)
                if bold:
                    l.setFont_(NSFont.boldSystemFontOfSize_(13))
                elif secondary:
                    l.setFont_(NSFont.systemFontOfSize_(11))
                    l.setTextColor_(NSColor.secondaryLabelColor())
                else:
                    l.setFont_(NSFont.systemFontOfSize_(13))
                l.setAutoresizingMask_(NSViewMinYMargin)
                return l

            def _popup(x, y_from_top, w, options, current_value, action_sel):
                h = 26
                y = inner_h - y_from_top - h
                pop = NSPopUpButton.alloc().initWithFrame_pullsDown_(
                    NSMakeRect(x, y, w, h), False,
                )
                codes = []
                for label, code in options:
                    pop.addItemWithTitle_(label)
                    codes.append(code)
                if current_value in codes:
                    pop.selectItemAtIndex_(codes.index(current_value))
                pop.setTarget_(ctrl)
                pop.setAction_(objc.selector(None, action_sel))
                pop.setAutoresizingMask_(NSViewMinYMargin)
                return pop, codes

            # ── General tab ──────────────────────────────────────────────
            general_view = NSView.alloc().initWithFrame_(
                NSMakeRect(0, 0, inner_w, inner_h)
            )
            general_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

            y_top = PAD_TAB
            general_view.addSubview_(_label("Hotkey:", PAD_TAB, y_top + 4, label_w))
            hotkey_pop, hotkey_codes = _popup(
                field_x, y_top, 220, HOTKEY_OPTIONS, self.hotkey_name, b"hotkeyChanged:",
            )
            general_view.addSubview_(hotkey_pop)
            state["hotkey_codes"] = hotkey_codes

            y_top += row_y_step
            general_view.addSubview_(_label("Language:", PAD_TAB, y_top + 4, label_w))
            language_pop, language_codes = _popup(
                field_x, y_top, 220, LANGUAGE_OPTIONS, self.language_code, b"languageChanged:",
            )
            general_view.addSubview_(language_pop)
            state["language_codes"] = language_codes

            y_top += row_y_step
            y = inner_h - y_top - 22
            auto_check = NSButton.alloc().initWithFrame_(
                NSMakeRect(PAD_TAB, y, inner_w - 2 * PAD_TAB, 22)
            )
            auto_check.setButtonType_(NSSwitchButton)
            auto_check.setTitle_("Auto-switch keyboard layout to match dictation language")
            auto_check.setState_(1 if self.match_layout else 0)
            auto_check.setTarget_(ctrl)
            auto_check.setAction_(objc.selector(None, b"autoSwitchChanged:"))
            auto_check.setAutoresizingMask_(NSViewMinYMargin)
            general_view.addSubview_(auto_check)

            y_top += 30
            y = inner_h - y_top - 22
            origin_check = NSButton.alloc().initWithFrame_(
                NSMakeRect(PAD_TAB, y, inner_w - 2 * PAD_TAB, 22)
            )
            origin_check.setButtonType_(NSSwitchButton)
            origin_check.setTitle_("Paste where recording started (not where focus is now)")
            origin_check.setState_(1 if self.paste_at_origin else 0)
            origin_check.setTarget_(ctrl)
            origin_check.setAction_(objc.selector(None, b"pasteAtOriginChanged:"))
            origin_check.setAutoresizingMask_(NSViewMinYMargin)
            general_view.addSubview_(origin_check)

            y_top += 34
            general_view.addSubview_(_label("History size:", PAD_TAB, y_top + 4, label_w))
            hist_y = inner_h - y_top - 24
            history_field = NSTextField.alloc().initWithFrame_(
                NSMakeRect(field_x, hist_y, 64, 24)
            )
            history_field.setStringValue_(str(self.history_size))
            history_field.setFont_(NSFont.systemFontOfSize_(13))
            history_field.setTarget_(ctrl)
            history_field.setAction_(objc.selector(None, b"historyCommitted:"))
            history_field.setAutoresizingMask_(NSViewMinYMargin)
            general_view.addSubview_(history_field)
            general_view.addSubview_(
                _label(f"entries  (1–{HISTORY_SIZE_MAX})", field_x + 72, y_top + 6, 220, secondary=True)
            )
            state["history_field"] = history_field

            y_top += 34
            general_view.addSubview_(_label("Sound volume:", PAD_TAB, y_top + 4, label_w))
            vol_y = inner_h - y_top - 24
            volume_slider = NSSlider.alloc().initWithFrame_(
                NSMakeRect(field_x, vol_y, 200, 24)
            )
            volume_slider.setMinValue_(0.0)
            volume_slider.setMaxValue_(1.0)
            volume_slider.setDoubleValue_(self.sound_volume)
            volume_slider.setContinuous_(False)  # fire on release, then play a sample
            volume_slider.setTarget_(ctrl)
            volume_slider.setAction_(objc.selector(None, b"soundVolumeChanged:"))
            volume_slider.setAutoresizingMask_(NSViewMinYMargin)
            general_view.addSubview_(volume_slider)
            general_view.addSubview_(
                _label("(0 = silent)", field_x + 208, y_top + 6, 120, secondary=True)
            )

            # ── Grammar tab ──────────────────────────────────────────────
            grammar_view = NSView.alloc().initWithFrame_(
                NSMakeRect(0, 0, inner_w, inner_h)
            )
            grammar_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

            y_top = PAD_TAB
            grammar_view.addSubview_(_label("Grammar fix:", PAD_TAB, y_top + 4, label_w))
            grammar_pop, grammar_codes = _popup(
                field_x, y_top, 220, GRAMMAR_FIX_OPTIONS, self.grammar_fix_mode, b"grammarChanged:",
            )
            grammar_view.addSubview_(grammar_pop)
            state["grammar_codes"] = grammar_codes

            y_top += row_y_step
            grammar_view.addSubview_(_label("Bubble timeout:", PAD_TAB, y_top + 4, label_w))
            timeout_y = inner_h - y_top - 24
            timeout_field = NSTextField.alloc().initWithFrame_(
                NSMakeRect(field_x, timeout_y, 64, 24)
            )
            timeout_field.setStringValue_(str(self.popover_timeout))
            timeout_field.setFont_(NSFont.systemFontOfSize_(13))
            timeout_field.setTarget_(ctrl)
            timeout_field.setAction_(objc.selector(None, b"timeoutCommitted:"))
            timeout_field.setAutoresizingMask_(NSViewMinYMargin)
            grammar_view.addSubview_(timeout_field)
            grammar_view.addSubview_(
                _label("seconds  (0 = never close)", field_x + 72, y_top + 6, 220, secondary=True)
            )
            state["timeout_field"] = timeout_field

            # ── API Key tab ──────────────────────────────────────────────
            apikey_view = NSView.alloc().initWithFrame_(
                NSMakeRect(0, 0, inner_w, inner_h)
            )
            apikey_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

            y_top = PAD_TAB
            apikey_view.addSubview_(_label(
                "Gemini API Key", PAD_TAB, y_top, inner_w - 2 * PAD_TAB,
                bold=True, height=20,
            ))
            y_top += 26
            apikey_view.addSubview_(_label(
                "Paste your key from aistudio.google.com/apikey",
                PAD_TAB, y_top, inner_w - 2 * PAD_TAB, secondary=True,
            ))

            y_top += 28
            field_y = inner_h - y_top - 26
            existing = self.gemini_key or ""

            secure = NSSecureTextField.alloc().initWithFrame_(
                NSMakeRect(PAD_TAB, field_y, inner_w - 2 * PAD_TAB, 26)
            )
            secure.setStringValue_(existing)
            secure.setPlaceholderString_("AIza…")
            secure.setFont_(NSFont.systemFontOfSize_(13))
            secure.setAutoresizingMask_(NSViewMinYMargin | NSViewWidthSizable)
            apikey_view.addSubview_(secure)

            plain = NSTextField.alloc().initWithFrame_(
                NSMakeRect(PAD_TAB, field_y, inner_w - 2 * PAD_TAB, 26)
            )
            plain.setStringValue_(existing)
            plain.setPlaceholderString_("AIza…")
            plain.setFont_(NSFont.systemFontOfSize_(13))
            plain.setHidden_(True)
            plain.setAutoresizingMask_(NSViewMinYMargin | NSViewWidthSizable)
            apikey_view.addSubview_(plain)

            y_top += 36
            show_y = inner_h - y_top - 20
            show_check = NSButton.alloc().initWithFrame_(
                NSMakeRect(PAD_TAB, show_y, 140, 20)
            )
            show_check.setButtonType_(NSSwitchButton)
            show_check.setTitle_("Show key")
            show_check.setState_(0)
            show_check.setTarget_(ctrl)
            show_check.setAction_(objc.selector(None, b"toggleShowKey:"))
            show_check.setAutoresizingMask_(NSViewMinYMargin)
            apikey_view.addSubview_(show_check)

            state["secure"] = secure
            state["plain"] = plain

            for v, title in [
                (general_view, "General"),
                (grammar_view, "Grammar"),
                (apikey_view, "API Key"),
            ]:
                item = NSTabViewItem.alloc().initWithIdentifier_(title)
                item.setLabel_(title)
                item.setView_(v)
                tab_view.addTabViewItem_(item)

            done.setTarget_(ctrl)
            done.setAction_(objc.selector(None, b"doDone:"))

            window.setDelegate_(ctrl)
            window.makeFirstResponder_(hotkey_pop)
            window.makeKeyAndOrderFront_(None)
            NSApplication.sharedApplication().runModalForWindow_(window)

        def _should_run_grammar_fix(self, lang):
            mode = self.grammar_fix_mode
            if mode == "off":
                return False
            if mode == "both":
                return lang in ("en", "he")
            return mode == lang

        def _run_grammar_fix(self, original, lang, origin_app=None):
            if not original or len(original) < GRAMMAR_MIN_CHARS:
                return
            if not self.gemini_key:
                print("[vbutton] grammar fix skipped: no Gemini API key set (use 'Set Gemini API key…')", flush=True)
                return

            def _worker():
                t0 = time.time()
                corrected = _fix_grammar(original, lang, self.gemini_key)
                dt = time.time() - t0
                if not corrected:
                    print(f"[vbutton] grammar fix produced no output ({dt:.2f}s)", flush=True)
                    return
                if corrected.strip() == original.strip():
                    print(f"[vbutton] grammar fix: no changes ({dt:.2f}s)", flush=True)
                    return
                print(f"[vbutton] grammar fix ({dt:.2f}s): {corrected}", flush=True)
                self.last_original = original
                self.last_corrected = corrected
                self.last_origin_app = origin_app
                _on_main_sync(lambda: self._present_grammar_popover(original, corrected, origin_app))

            threading.Thread(target=_worker, daemon=True).start()

        def _present_grammar_popover(self, original, corrected, origin_app=None):
            if origin_app is None:
                origin_app = self.last_origin_app
            try:
                btn = self._nsapp.nsstatusitem.button()
            except Exception:
                btn = None
            if btn is None:
                return

            old = self._popover_box.get("popover")
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass

            def _do_paste():
                # Verify before replace: select the last N chars, copy them,
                # and only replace if they match what transcription pasted.
                # Prevents deleting the user's other content if the cursor
                # moved or they kept typing after the transcription.
                expected = original + " "
                n_chars = len(expected)

                def _bg():
                    time.sleep(0.15)  # let popover close + focus return
                    if origin_app is not None and self.paste_at_origin:
                        _on_main_sync(lambda: _activate_app(origin_app))
                        time.sleep(0.12)  # replace lands in the origin app
                    try:
                        from AppKit import NSPasteboard

                        pb = NSPasteboard.generalPasteboard()
                        saved_snapshot = _snapshot_pasteboard(pb)
                        change_before_copy = pb.changeCount()

                        _select_left_n(n_chars)
                        time.sleep(0.05)
                        _send_cmd_c()

                        # Wait briefly for Cmd+C to update the pasteboard.
                        deadline = time.time() + 0.3
                        while pb.changeCount() == change_before_copy and time.time() < deadline:
                            time.sleep(0.01)

                        sel_text = pb.stringForType_("public.utf8-plain-text") or ""

                        if sel_text == expected or sel_text.strip() == original.strip():
                            # Safe: restore user's clipboard first so _paste_text
                            # snapshots the right thing, then replace selection.
                            _restore_pasteboard(pb, saved_snapshot)
                            time.sleep(0.03)
                            _on_main_sync(lambda: _paste_text(corrected + " ", None))
                        else:
                            # Mismatch — don't destroy the selection. Collapse it
                            # and stash corrected text on the clipboard.
                            _press_right_arrow()
                            pb.clearContents()
                            pb.setString_forType_(corrected, "public.utf8-plain-text")
                            print(
                                f"[vbutton] paste-improved aborted: selection "
                                f"didn't match original (got {sel_text!r}); "
                                f"corrected text placed on clipboard.",
                                file=sys.stderr, flush=True,
                            )
                            try:
                                rumps.notification(
                                    title="VButton",
                                    subtitle="Couldn't verify original text",
                                    message="Improved text copied to clipboard — paste manually.",
                                )
                            except Exception:
                                pass
                    except Exception as e:
                        print(f"[vbutton] paste-improved failed: {e}", file=sys.stderr, flush=True)

                threading.Thread(target=_bg, daemon=True).start()

            def _do_copy():
                try:
                    subprocess.run(["pbcopy"], input=corrected.encode("utf-8"), check=True)
                except Exception as e:
                    print(f"[vbutton] copy-improved failed: {e}", file=sys.stderr, flush=True)

            def _do_copy_original():
                try:
                    subprocess.run(["pbcopy"], input=original.encode("utf-8"), check=True)
                except Exception as e:
                    print(f"[vbutton] copy-original failed: {e}", file=sys.stderr, flush=True)

            _show_grammar_popover(
                btn, original, corrected, _do_paste, _do_copy, self._popover_box,
                auto_close_seconds=self.popover_timeout,
                on_copy_original=_do_copy_original,
            )

        def set_state(self, s):
            self.state = s
            img = SYMBOLS.get(s)
            try:
                btn = self._nsapp.nsstatusitem.button()
            except Exception:
                btn = None
            if img is not None and btn is not None:
                btn.setImage_(img)
                btn.setTitle_("")
            else:
                self.title = ICONS[s]
            self.toggle_item.title = {STATE_IDLE: "Start Recording", STATE_REC: "Stop & Transcribe", STATE_BUSY: "✕  Cancel Transcribing"}[s]

        def start_recording(self):
            # Remember where focus is now; paste lands here even if the user
            # switches windows while transcription runs.
            self._origin_app = _capture_frontmost_app()
            with lock:
                if self.state != STATE_IDLE:
                    return False
                self.set_state(STATE_REC)
            _play("Tink")
            try:
                rec.start()
                return True
            except Exception as e:
                print(f"[vbutton] mic start failed: {e}", file=sys.stderr, flush=True)
                with lock:
                    self.set_state(STATE_IDLE)
                return False

        def stop_and_transcribe(self, *, min_hold_ms=0):
            with lock:
                if self.state != STATE_REC:
                    return
                self.set_state(STATE_BUSY)
                self._transcribe_gen += 1
                gen = self._transcribe_gen
            audio = rec.stop()
            _play("Pop")
            threading.Thread(target=self._do_transcribe, args=(audio, min_hold_ms, gen), daemon=True).start()

        def cancel_transcribe(self):
            with lock:
                if self.state != STATE_BUSY:
                    return
                # Bump the generation so the in-flight worker discards its result
                # (and won't reset state) when it finishes.
                self._transcribe_gen += 1
                self.set_state(STATE_IDLE)
            print("[vbutton] transcription cancelled", flush=True)
            _play("Funk")

        def _do_transcribe(self, audio, min_hold_ms, gen):
            import numpy as np
            try:
                dur = len(audio) / SAMPLE_RATE
                if min_hold_ms and dur * 1000 < min_hold_ms:
                    print(f"[vbutton] too short ({dur:.2f}s), skipped", flush=True)
                    return
                if dur < MIN_AUDIO_SECONDS:
                    print(f"[vbutton] too short ({dur:.2f}s), skipped", flush=True)
                    return
                peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
                if peak < SILENCE_PEAK_THRESHOLD:
                    print(f"[vbutton] silent clip ({dur:.2f}s, peak={peak:.4f}), skipped", flush=True)
                    return
                t0 = time.time()
                text, lang = _transcribe(model, audio, prev_text=self.prev_text)
                dt = time.time() - t0
                if gen != self._transcribe_gen:
                    print(f"[vbutton] transcription discarded (cancelled, {dt:.2f}s)", flush=True)
                    return
                if not text:
                    print(f"[vbutton] no speech detected ({dur:.2f}s clip, {dt:.2f}s, lang={lang})", flush=True)
                    return
                if _is_hallucination(text):
                    print(f"[vbutton] hallucination filter dropped: {text!r} ({dur:.2f}s, peak={peak:.4f}, lang={lang})", flush=True)
                    return
                print(f"[vbutton] {dur:.2f}s -> {dt:.2f}s, lang={lang}, peak={peak:.4f}: {text}", flush=True)
                self.prev_text = (self.prev_text + " " + text)[-300:]
                self.last_text = text
                preview = text if len(text) <= 25 else text[:22] + "..."
                self.last_item.title = f"Last: {preview}"
                self._add_history(text)
                do_switch = self.match_layout and lang in LAYOUT_MATCHERS
                result = {"switched": None, "paste_err": None}

                def _on_main():
                    try:
                        _paste_text(text + " ", keyboard)
                    except Exception as e:
                        result["paste_err"] = str(e)
                    if do_switch:
                        try:
                            result["switched"] = _switch_layout_for_lang(lang)
                        except Exception as e:
                            print(f"[vbutton] layout switch failed: {e}", file=sys.stderr, flush=True)

                if gen != self._transcribe_gen:
                    print("[vbutton] paste skipped (cancelled)", flush=True)
                    return
                origin_app = self._origin_app
                self.last_origin_app = origin_app
                if origin_app is not None and self.paste_at_origin:
                    _on_main_sync(lambda: _activate_app(origin_app))
                    time.sleep(0.12)  # let the origin app come to the front
                _on_main_sync(_on_main)
                if result["switched"] is False:
                    print(f"[vbutton] no installed layout matches lang={lang}", flush=True)
                if result["paste_err"]:
                    print(f"[vbutton] paste failed: {result['paste_err']}", file=sys.stderr, flush=True)
                if self._should_run_grammar_fix(lang):
                    self._run_grammar_fix(text, lang, origin_app)
            except Exception as e:
                print(f"[vbutton] transcribe failed: {e}", file=sys.stderr, flush=True)
            finally:
                with lock:
                    if gen == self._transcribe_gen:
                        self.set_state(STATE_IDLE)

        def on_toggle(self, _sender):
            if self.state == STATE_IDLE:
                self.start_recording()
            elif self.state == STATE_REC:
                self.stop_and_transcribe()
            elif self.state == STATE_BUSY:
                self.cancel_transcribe()

        def set_match_layout(self, value):
            self.match_layout = bool(value)
            self.match_layout_item.state = 1 if self.match_layout else 0
            cfg = _load_config()
            cfg["match_layout"] = self.match_layout
            _save_config(cfg)
            installed = [sid for sid, _ in _all_layouts()]
            print(f"[vbutton] match_layout={self.match_layout}; installed layouts: {installed}", flush=True)

        def on_toggle_match_layout(self, _sender):
            self.set_match_layout(not self.match_layout)

        def set_paste_at_origin(self, value):
            self.paste_at_origin = bool(value)
            cfg = _load_config()
            cfg["paste_at_origin"] = self.paste_at_origin
            _save_config(cfg)
            print(f"[vbutton] paste_at_origin={self.paste_at_origin}", flush=True)

        def set_sound_volume(self, v):
            global SOUND_VOLUME
            try:
                v = float(v)
            except (TypeError, ValueError):
                return
            v = max(0.0, min(1.0, v))
            SOUND_VOLUME = v
            self.sound_volume = v
            cfg = _load_config()
            cfg["sound_volume"] = round(v, 3)
            _save_config(cfg)
            print(f"[vbutton] sound volume set to {v:.2f}", flush=True)

        def play_test_sound(self):
            _play("Tink")

        def set_gemini_key(self, key):
            if key == self.gemini_key:
                return
            self.gemini_key = key
            cfg = _load_config()
            if key:
                cfg["gemini_api_key"] = key
            else:
                cfg.pop("gemini_api_key", None)
            _save_config(cfg)
            print(f"[vbutton] gemini api key {'set' if key else 'cleared'}", flush=True)

        def on_copy_last(self, _sender):
            if self.last_original and self.last_corrected:
                self._present_grammar_popover(self.last_original, self.last_corrected)
                return
            if not self.last_text:
                return
            try:
                subprocess.run(["pbcopy"], input=self.last_text.encode("utf-8"), check=True)
                rumps.notification("VButton", "Copied to clipboard", self.last_text[:80])
            except Exception as e:
                print(f"[vbutton] copy failed: {e}", file=sys.stderr, flush=True)

        def on_quit(self, _sender):
            rumps.quit_application()

    app = VButtonApp()

    def on_press(key):
        if key != app.hotkey or app.state != STATE_IDLE:
            return
        app.press_time = time.time()
        app.start_recording()

    def on_release(key):
        if key != app.hotkey or app.state != STATE_REC:
            return
        held_ms = (time.time() - app.press_time) * 1000
        if held_ms < DEBOUNCE_MS:
            with lock:
                rec.stop()
                app.set_state(STATE_IDLE)
            return
        app.stop_and_transcribe()

    listener = Listener(on_press=on_press, on_release=on_release)
    listener.daemon = True
    listener.start()

    import signal as _signal
    _open_menu_flag = threading.Event()
    _icon_applied = threading.Event()

    def _on_sigusr1(_signum, _frame):
        _open_menu_flag.set()

    _signal.signal(_signal.SIGUSR1, _on_sigusr1)

    def _poll_open_menu(_timer):
        if not _icon_applied.is_set():
            try:
                if app._nsapp is not None and app._nsapp.nsstatusitem is not None:
                    app.set_state(STATE_IDLE)
                    _icon_applied.set()
            except Exception as e:
                print(f"[vbutton] initial icon apply failed: {e}", flush=True)
                _icon_applied.set()
        if _open_menu_flag.is_set():
            _open_menu_flag.clear()
            try:
                btn = app._nsapp.nsstatusitem.button()
                if btn:
                    btn.performClick_(None)
            except Exception as e:
                print(f"[vbutton] open-menu failed: {e}", file=sys.stderr, flush=True)

    rumps.Timer(_poll_open_menu, 0.3).start()

    app.run()


def cmd_once():
    import numpy as np
    import sounddevice as sd

    print(f"[vbutton] loading model {MODEL} (compute={COMPUTE_TYPE})...", flush=True)
    model = _load_model()
    _warmup(model)
    try:
        dev = sd.query_devices(kind="input")
        print(f"[vbutton] mic: {dev['name']}  (default samplerate {dev['default_samplerate']:.0f})", flush=True)
    except Exception as e:
        print(f"[vbutton] mic query failed: {e}", flush=True)
    print("[vbutton] ready. Press Enter to start, Enter again to stop.", flush=True)
    input()

    peak = [0.0]

    def meter(arr):
        rms = float(np.sqrt(np.mean(arr * arr)))
        if rms > peak[0]:
            peak[0] = rms
        bars = min(40, int(rms * 400))
        print(f"\r[mic rms {rms:.4f}] {'#' * bars:<40}", end="", flush=True)

    rec = Recorder()
    _play("Tink")
    rec.start(meter=meter)
    try:
        input()
    except KeyboardInterrupt:
        pass
    audio = rec.stop()
    _play("Pop")
    print()
    dur = len(audio) / SAMPLE_RATE
    print(f"[vbutton] captured {dur:.2f}s, peak rms {peak[0]:.4f}", flush=True)
    if dur < MIN_AUDIO_SECONDS:
        print("[vbutton] too short to transcribe", flush=True)
        return
    if peak[0] < 0.005:
        print("[vbutton] WARNING: very low peak rms; mic may be silent", flush=True)
    t0 = time.time()
    text, lang = _transcribe(model, audio)
    dt = time.time() - t0
    print(f"[vbutton] transcribed in {dt:.2f}s, lang={lang}", flush=True)
    print(f"---\n{text}\n---", flush=True)


def cmd_warmup():
    print(f"[vbutton] downloading + loading {MODEL}...", flush=True)
    model = _load_model()
    _warmup(model)
    print("[vbutton] warmup ok.", flush=True)


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(HELP)
        return
    cmds = {"run": cmd_run, "once": cmd_once, "warmup": cmd_warmup}
    fn = cmds.get(sys.argv[1])
    if not fn:
        print(HELP)
        sys.exit(2)
    fn()


if __name__ == "__main__":
    main()

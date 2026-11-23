#!/usr/bin/env python3
"""
Tap-to-toggle + Esc-discard behaviour of DictationController, driven with
fakes only (no microphone, model, keyboard, sound or clipboard):

  - a quick tap latches the recording, the next press stops + transcribes once
  - a hold of 0.35 s or more stops on release
  - Esc discards (held or latched), Esc while idle does nothing
  - tap_to_toggle=False is the old hold-only behaviour
  - a press while transcribing is ignored
  - the hotkey state machine's on_escape fires per Esc press, held or not

Run from saghi-mac/:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=. SAGHI_DATA_DIR=$(mktemp -d) \
        SAGHI_NO_PERMISSION_PROMPT=1 <venv>/bin/python dev/test_tap_toggle.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

assert os.environ.get("SAGHI_DATA_DIR"), "Run with SAGHI_DATA_DIR set to a scratch directory (never the real data dir)"

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from saghi import paths  # noqa: E402
from saghi.engine import TranscribeResult  # noqa: E402
from saghi.hotkey import HotkeyStateMachine  # noqa: E402
from saghi.settings import SettingsManager  # noqa: E402
from saghi.ui import dictation as dictation_module  # noqa: E402
from saghi.ui import sounds  # noqa: E402
from saghi.ui.dictation import TAP_MAX_S, DictationController  # noqa: E402
from saghi.ui.engine_status import EngineStatusBridge  # noqa: E402

_checks = 0


def check(condition: bool, message: str) -> None:
    global _checks
    assert condition, f"FAILED: {message}"
    _checks += 1
    print(f"ok: {message}")


class _FakeRecorder:
    def __init__(self) -> None:
        self.start_calls: list = []
        self.stop_calls = 0
        self.cancel_calls = 0

    def start(self, device=None) -> None:
        self.start_calls.append(device)

    def stop(self) -> np.ndarray:
        self.stop_calls += 1
        return np.full(16000, 0.01, dtype=np.float32)  # 1 s, not silent

    def cancel(self) -> None:
        self.cancel_calls += 1

    def level(self):
        return np.zeros(0, dtype=np.float32), 0.0


class _FakeEngine:
    def __init__(self) -> None:
        self.is_loaded = True
        self.device = "cpu"
        self.stack_path = ""
        self.calls = 0

    def transcribe_array(self, audio, sr, language="ar", cleanup_level="light"):
        self.calls += 1
        return TranscribeResult(raw_text="x", text="y", language=language, duration_s=1.0, inference_s=0.0)


class _FakeHotkey:
    combo = "ctrl+cmd"

    def set_combo(self, combo): ...
    def start(self): ...
    def stop(self): ...


app = QApplication.instance() or QApplication(sys.argv)
paths.ensure_dirs()

# Keep the test off the clipboard, the history database and the speakers.
played: list = []
dictation_module.sounds.play = lambda name: played.append(name)
dictation_module.paste.paste_text = lambda text, autopaste=False: type("R", (), {"pasted": False})()
dictation_module.history.add_entry = lambda **kw: None

engine = _FakeEngine()
engine_status = EngineStatusBridge(engine, poll_ms=3_600_000)
settings_manager = SettingsManager()
settings_manager.update(tap_to_toggle=True, sound_feedback=True, autopaste=False, save_recordings=False,
                        openrouter_enabled=False)

controller = DictationController(engine, engine_status, settings_manager)
controller._hotkey = _FakeHotkey()
discards: list = []
_orig_discard = controller.indicator.show_discarded
controller.indicator.show_discarded = lambda: (discards.append(1), _orig_discard())


def fresh() -> _FakeRecorder:
    rec = _FakeRecorder()
    controller.recorder = rec
    played.clear()
    discards.clear()
    engine.calls = 0
    return rec


def press(held_s: float = 0.0) -> None:
    """Hotkey press; pretend it began `held_s` ago when the release comes."""
    controller._on_hold_started()


def release(held_s: float) -> None:
    controller._press_time = time.monotonic() - held_s
    controller._on_hold_ended()


def wait_idle() -> None:
    deadline = time.time() + 5
    while controller._busy and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


# ---- 1. tap latches; second press stops and transcribes once ----------------
print("--- tap latches, second press transcribes once ---")
rec = fresh()
press()
check(rec.start_calls == [None] and controller.indicator.state == "recording", "press starts recording")
check(played == [sounds.START], "start sound played")
release(0.1)
check(controller._latched and controller._recording_active, "a quick tap keeps recording (latched)")
check(rec.stop_calls == 0 and engine.calls == 0, "nothing stopped or transcribed yet")
press()
check(rec.stop_calls == 1, "second press stops the recorder")
release(0.05)  # the release of the second press
wait_idle()
check(engine.calls == 1, "exactly one transcription")
check(rec.stop_calls == 1, "second press's release is ignored (no second stop)")
check(played == [sounds.START, sounds.STOP], f"start then stop sounds (got {played})")
check(controller.indicator.state == "hidden" and not controller._busy, "island closes after the paste")

# ---- 2. hold >= 0.35 s stops on release -------------------------------------
print("--- hold stops on release ---")
rec = fresh()
press()
release(TAP_MAX_S + 0.05)
check(not controller._latched and rec.stop_calls == 1, "a hold stops on release")
wait_idle()
check(engine.calls == 1, "and transcribes once")
check(played == [sounds.START, sounds.STOP], "start then stop sounds")

# ---- 3. Esc while latched discards ------------------------------------------
print("--- Esc while latched ---")
rec = fresh()
press()
release(0.1)
controller._on_cancelled()  # what on_escape is wired to
check(rec.cancel_calls == 1 and rec.stop_calls == 0, "recorder cancelled, not stopped")
check(discards == [1], "show_discarded called")
check(played == [sounds.START, sounds.DISCARD], f"discard sound played (got {played})")
check(engine.calls == 0 and not controller._busy and not controller._latched, "no transcription, controller idle")
controller._on_cancelled()
check(discards == [1] and rec.cancel_calls == 1, "a second Esc does nothing")
wait_idle()

# ---- 4. Esc while held discards ---------------------------------------------
print("--- Esc while held ---")
rec = fresh()
press()
controller._on_cancelled()
check(rec.cancel_calls == 1 and discards == [1], "held recording discarded")
release(0.1)  # release after Esc must not latch or stop anything
check(not controller._latched and rec.stop_calls == 0 and engine.calls == 0, "release after Esc does nothing")
check(played == [sounds.START, sounds.DISCARD], "discard sound")

# ---- 5. Esc when idle -------------------------------------------------------
print("--- Esc when idle ---")
rec = fresh()
controller._on_cancelled()
controller._escape_raw.emit()
app.processEvents()
check(discards == [] and played == [] and rec.cancel_calls == 0, "nothing happens")

# ---- 6. tap_to_toggle off: hold-only ----------------------------------------
print("--- tap_to_toggle False ---")
settings_manager.update(tap_to_toggle=False)
rec = fresh()
press()
release(0.1)
check(not controller._latched and rec.stop_calls == 1, "even a quick release stops (hold-only)")
wait_idle()
settings_manager.update(tap_to_toggle=True)

# ---- 7. press while busy is ignored ------------------------------------------
print("--- press while busy ---")
rec = fresh()
controller._busy = True  # as if a transcription is running
press()
check(rec.start_calls == [] and not controller._latched, "press ignored while busy")
release(0.1)
check(not controller._latched and rec.stop_calls == 0, "its release is ignored too")
controller._busy = False

# ---- 8. slow mic open: tap latches, Esc cancels ------------------------------
print("--- slow mic open ---")


class _SlowRecorder(_FakeRecorder):
    def start(self, device=None) -> None:
        time.sleep(dictation_module.MIC_OPEN_WAIT_S + 0.2)
        super().start(device)


rec = _SlowRecorder()
controller.recorder = rec
played.clear()
discards.clear()
press()
release(0.1)
check(controller._latched and controller._open_token is not None, "tap during a slow open latches")
controller._on_cancelled()
check(discards == [1] and played == [sounds.DISCARD], "Esc during a slow open discards")
deadline = time.time() + 5
while controller._busy and time.time() < deadline:
    app.processEvents()
    time.sleep(0.01)
check(rec.cancel_calls == 1 and engine.calls == 0 and not controller._busy, "mic closed again when it answers, nothing transcribed")

# ---- 9. hotkey state machine: on_escape --------------------------------------
print("--- HotkeyStateMachine.on_escape ---")
from pynput.keyboard import Key  # noqa: E402

events: list = []
sm = HotkeyStateMachine(
    combo="ctrl+cmd",
    on_hold_started=lambda: events.append("start"),
    on_hold_ended=lambda: events.append("end"),
    on_cancelled=lambda: events.append("cancelled"),
    on_escape=lambda: events.append("esc"),
)
sm.handle_press(Key.esc)
sm.handle_press(Key.esc)  # key-repeat
check(events == ["esc"], "Esc with no combo fires on_escape once (repeat swallowed)")
sm.handle_release(Key.esc)
sm.handle_press(Key.esc)
check(events == ["esc", "esc"], "a new Esc press fires again")
sm.handle_release(Key.esc)
events.clear()
sm.handle_press(Key.ctrl)
sm.handle_press(Key.cmd)
sm.handle_press(Key.esc)
check(events == ["start", "cancelled", "esc"], f"Esc while holding fires cancelled and on_escape (got {events})")
sm.handle_release(Key.esc)
sm.handle_release(Key.ctrl)
sm.handle_release(Key.cmd)
check(events == ["start", "cancelled", "esc"], "release after the cancel is swallowed")

print(f"\nALL PASSED ({_checks} checks)")

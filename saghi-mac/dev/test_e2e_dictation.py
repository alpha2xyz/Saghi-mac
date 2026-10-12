#!/usr/bin/env python3
"""
Real end-to-end dictation on a Mac. Nothing is faked:

    hotkey state machine (Ctrl+Cmd held)  ->  DictationController
    -> real Recorder on a real microphone  ->  real model (loads on first use,
    like the app)  ->  text in history and on the clipboard

The Mac's own Arabic voice (`say -v Majed`) speaks a sentence through the
speakers while the hotkey is held, and the microphone hears it. This is the
path that broke in v0.2.0 ("no text, pill stops at 0:03, flat waveform"):
the recording came back as pure silence from the chosen mic, so no other
test could see it -- they all hand the controller ready-made audio.

The only shortcut: key presses go straight into the hotkey state machine
instead of through pynput's OS-level listener, which needs Input
Monitoring and a human at the keyboard (dev/test_hotkey_logic.py covers
the state machine; dev/probe_devices.py the listener).

Needs: macOS, the Majed voice (System Settings -> Accessibility -> Spoken
Content), Microphone permission for the app running this (Terminal /
Claude), SAGHI_MODEL_DIR, and a voice the mic can hear clearly: speakers
at 50% or more and a quiet room. On a MacBook the speaker-to-mic path is
weak: at 6% volume with people talking nearby the model hears "music" or
"noise", which says nothing about Saghi. So the test measures the room
first and SKIPS (exit 0, "SKIPPED (ROOM/VOLUME)") when that is the
problem, so a red result always means a Saghi fault. Also skips when
macOS, the model or the voice is missing; FAILS when the mic sends silence. Autopaste stays off; the clipboard is restored.

    SAGHI_E2E_MIC   name of the mic to record from (default: system default)

Run from saghi-mac/ (the model loads for real: ~10 s on an M5):
    SAGHI_DATA_DIR=$(mktemp -d) SAGHI_MODEL_DIR=~/Applications/Saghi.app/Contents/Resources/model \\
        PYTHONPATH=. <app venv>/bin/python3.12 dev/test_e2e_dictation.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if sys.platform != "darwin":
    print("SKIPPED: macOS only (real microphone, speakers and `say`).")
    sys.exit(0)

_model_env = os.environ.get("SAGHI_MODEL_DIR")
if not _model_env or not (Path(_model_env) / "config.json").is_file():
    print("SKIPPED: set SAGHI_MODEL_DIR to the model folder to run this real end-to-end test.")
    sys.exit(0)

if "Majed" not in subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout:
    print("SKIPPED: the Arabic voice 'Majed' is not installed.")
    sys.exit(0)

assert os.environ.get("SAGHI_DATA_DIR"), "Run with SAGHI_DATA_DIR set to a scratch directory (never the real data dir)"

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pynput import keyboard  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from saghi import history  # noqa: E402
from saghi.engine import SaghiEngine  # noqa: E402
from saghi.settings import SettingsManager  # noqa: E402
from saghi.ui.dictation import DictationController  # noqa: E402
from saghi.ui.engine_status import EngineStatusBridge  # noqa: E402

SENTENCE = "مرحبا، هذا اختبار لتطبيق صاغي على الماك"
EXPECTED_WORDS = ("اختبار", "صاغي")  # at least one must come back
TIMEOUT_S = 300.0
MIN_VOLUME = 50  # percent of the Mac's output volume
MAX_ROOM_RMS = 0.01  # a quiet room is ~0.002; people talking nearby ~0.03

_checks = 0


def check(condition: bool, message: str) -> None:
    global _checks
    assert condition, f"FAILED: {message}"
    _checks += 1
    print(f"ok: {message}")


def strip_marks(text: str) -> str:
    """Drop Arabic diacritics and tatweel so «مرحبًا» matches «مرحبا»."""
    return re.sub(r"[ً-ْـ]", "", text)


def pump(app: QApplication, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def skip_env(reason: str) -> None:
    print(f"SKIPPED (ROOM/VOLUME): {reason}")
    sys.exit(0)


def osascript(expr: str) -> str:
    return subprocess.run(["osascript", "-e", expr], capture_output=True, text=True).stdout.strip()


check(osascript("output muted of (get volume settings)") != "true", "speakers are not muted (the mic has to hear the voice)")
volume = int(osascript("output volume of (get volume settings)") or 0)
if volume < MIN_VOLUME:
    skip_env(f"speakers at {volume}% -- raise them to {MIN_VOLUME}% or more and run again")

work = Path(tempfile.mkdtemp(prefix="saghi-e2e-"))
speech = work / "speech.aiff"
subprocess.run(["say", "-v", "Majed", "-o", str(speech), SENTENCE], check=True)
check(speech.is_file() and speech.stat().st_size > 10_000, "the Mac voice recorded the Arabic test sentence")

clipboard_before = subprocess.run(["pbpaste"], capture_output=True).stdout

app = QApplication.instance() or QApplication(sys.argv)
engine = SaghiEngine(Path(_model_env))
engine_status = EngineStatusBridge(engine)
settings_manager = SettingsManager()
settings_manager.update(
    hotkey="ctrl+cmd",
    microphone=os.environ.get("SAGHI_E2E_MIC") or None,
    language="ar",
    cleanup_level="light",
    autopaste=False,
    sound_feedback=True,  # as shipped: the start "Tink" plays while recording
    openrouter_enabled=False,
)
controller = DictationController(engine, engine_status, settings_manager)
hotkey = controller._hotkey._state  # the real HotkeyStateMachine the app uses

# Room check, with the same Recorder and mic the dictation will use.
from saghi.recorder import Recorder  # noqa: E402

probe = Recorder()
probe.start(device=settings_manager.current.microphone)
time.sleep(1.5)
room = probe.stop()
room_rms = float((room.astype("float64") ** 2).mean() ** 0.5) if room.size else 0.0
check(room.size > 0 and float(abs(room).max()) > 1e-6, "the microphone sends sound (not bit-exact silence)")
if room_rms > MAX_ROOM_RMS:
    controller.stop()
    skip_env(f"background sound is rms {room_rms:.4f} (limit {MAX_ROOM_RMS}) -- run it in a quiet room")

try:
    print(f"--- Hold Ctrl+Cmd, speak through the speakers (mic: {settings_manager.current.microphone or 'system default'}) ---")
    hotkey.handle_press(keyboard.Key.ctrl_l)
    hotkey.handle_press(keyboard.Key.cmd)
    pump(app, 0.1)
    check(controller.status == "recording", "holding Ctrl+Cmd starts a recording")

    player = subprocess.Popen(["afplay", str(speech)])
    peak_seen = 0.0
    while player.poll() is None:
        _samples, rms = controller.recorder.level()
        peak_seen = max(peak_seen, rms)
        pump(app, 0.05)
    pump(app, 0.4)  # let the last word reach the buffer
    check(peak_seen > 0.001, f"the live waveform moved while the voice played (max rms {peak_seen:.4f})")

    hotkey.handle_release(keyboard.Key.cmd)
    hotkey.handle_release(keyboard.Key.ctrl_l)
    pump(app, 0.1)
    check(controller.status in ("processing", "ready"), f"releasing the keys sends the audio to the model (status: {controller.status})")

    deadline = time.monotonic() + TIMEOUT_S
    while (controller._busy or controller._worker is not None) and time.monotonic() < deadline:
        pump(app, 0.1)
    check(not controller._busy, "the dictation finished before the timeout")
    check(controller.status == "ready", f"the pill is back to ready, not an error (status: {controller.status!r} {controller.status_message!r})")

    rows = history.search(limit=1)
    check(len(rows) == 1, "the dictation was saved to history")
    text = rows[0]["text"]
    print(f"    transcript: {text}")
    check(any(w in strip_marks(text) for w in EXPECTED_WORDS), f"the transcript contains {' or '.join(EXPECTED_WORDS)}")

    clip = subprocess.run(["pbpaste"], capture_output=True).stdout.decode("utf-8", "replace")
    check(clip.strip() == text.strip(), "the text was put on the clipboard, ready to paste")
finally:
    controller.stop()
    subprocess.run(["pbcopy"], input=clipboard_before)
    shutil.rmtree(work, ignore_errors=True)

print(f"\nALL PASSED ({_checks} checks)")

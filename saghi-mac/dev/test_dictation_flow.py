#!/usr/bin/env python3
"""
Phase 5 test: THE ONE real end-to-end inference run budgeted for the live
dictation flow (model load ~70-110s + inference, measured on a slow CPU
-- budget several minutes; far faster on Apple Silicon).

Drives saghi.ui.dictation.DictationController exactly like a real
press-and-hold dictation would, except:
  - the hotkey-thread signals are invoked directly (`controller._on_hold_
    started()` / `_on_hold_ended()` / `_on_cancelled()`) instead of a real
    pynput.Listener -- this test is about the controller's OWN flow logic,
    not hotkey detection (already covered by dev/test_hotkey_logic.py) or
    TCC-dependent listener delivery (covered by dev/probe_devices.py).
  - `controller.recorder` is swapped for a `_FakeRecorder` that returns a
    fixed, real ndarray read from <model dir>/examples/sample1.wav
    -- so the ONE real inference call in this file is
    engine.transcribe_array() on genuine, known audio, not silence or
    synthetic noise.

Order matters here, deliberately: every ZERO-inference guard-rail check
(short recording discarded, bit-exact-silence treated as a mic error,
hold_started ignored while busy, Esc-cancel) runs FIRST and cheap, so a bug
in any of them is caught before spending the one real-inference budget on
the happy path -- same principle as the other tests' zero-inference checks.

SAFETY NOTE: `settings.autopaste` is left FALSE for this whole test.
paste.paste_text()'s clipboard-set path is real and gets exercised for
real (see dev/test_paste_unit.py for the CGEventPost / Cmd+V path, which
already spent its own, deliberate probe of that call once); this file does
NOT additionally synthesize a real system-wide Cmd+V keystroke into
whatever app happens to be frontmost on the real desktop -- there is no
reason for an automated, unattended test to do that, and autopaste itself
is exercised at the unit level in test_paste_unit.py already.

Reuses the known-correct sample1.wav transcript seen in
earlier benchmark runs ("ففي الحالة دي المسألة دي يعني more safe") only as
a LOOSE substring/non-empty sanity check, not exact equality -- per an
advisor review during this phase's design: the dictation flow calls
`engine.transcribe_array()` (the in-memory array path), a different call
shape from the `engine.transcribe()` file-path call that produced that
exact benchmark string, and Phase 3's own notes already documented
capitalization drift ("More Safe" vs "more safe") between the two paths on
segment-level calls. An exact-equality assert here would risk burning the
entire inference budget on a spurious re-run over a capitalization
difference.

Run (generous timeout -- this genuinely takes minutes):
    QT_QPA_PLATFORM=offscreen SAGHI_DATA_DIR=<scratch dir> \
        PYTHONPATH=. <venv>/bin/python dev/test_dictation_flow.py
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
import soundfile as sf  # noqa: E402
from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from saghi import history, paste, paths  # noqa: E402
from saghi.engine import SaghiEngine  # noqa: E402
from saghi.settings import SettingsManager  # noqa: E402
from saghi.ui.dictation import DictationController  # noqa: E402
from saghi.ui.engine_status import EngineStatusBridge  # noqa: E402

_checks = 0


def check(condition: bool, message: str) -> None:
    global _checks
    assert condition, f"FAILED: {message}"
    _checks += 1
    print(f"ok: {message}")


class _FakeRecorder:
    """
    Duck-types saghi.recorder.Recorder's public surface
    (start/stop/cancel/level) without touching a real microphone --
    `DictationController.recorder` is a plain public attribute (see
    dictation.py) specifically so a test can swap it out like this.
    """

    def __init__(self, audio: np.ndarray) -> None:
        self._audio = audio
        self.start_calls: list = []
        self.stop_called = False
        self.cancel_called = False

    def start(self, device=None) -> None:
        self.start_calls.append(device)

    def stop(self) -> np.ndarray:
        self.stop_called = True
        return self._audio

    def cancel(self) -> None:
        self.cancel_called = True

    def level(self):
        return np.zeros(0, dtype=np.float32), 0.0


_model_env = os.environ.get("SAGHI_MODEL_DIR")
if not _model_env or not (Path(_model_env) / "examples" / "sample1.wav").is_file():
    print("SKIPPED: set SAGHI_MODEL_DIR to a model folder that contains examples/sample1.wav to run this real-inference test.")
    sys.exit(0)
MODEL_DIR = Path(_model_env)
SAMPLE_WAV = MODEL_DIR / "examples" / "sample1.wav"
TIMEOUT_MS = 600_000  # 10 minutes -- generous, matching dev/test_gui_filejob.py's precedent

assert SAMPLE_WAV.is_file(), f"Sample WAV not found: {SAMPLE_WAV}"

app = QApplication.instance() or QApplication(sys.argv)

engine = SaghiEngine(MODEL_DIR)
engine_status = EngineStatusBridge(engine)
settings_manager = SettingsManager()
settings_manager.update(
    language="ar",
    cleanup_level="light",
    autopaste=False,  # see module docstring's SAFETY NOTE
    save_recordings=True,
    waveform_style="bars",
    waveform_color="#4A90D9",
    microphone=None,
)

controller = DictationController(engine, engine_status, settings_manager)
# Deliberately never call controller.start() -- that would start a REAL
# pynput.keyboard.Listener, which this test doesn't need (hotkey detection
# itself is dev/test_hotkey_logic.py's job) and which would just be dead
# weight sitting on a background thread for the rest of this test.

check(engine.is_loaded is False, "engine not loaded before any guard-rail check runs")

# ============================================================================
# ZERO-INFERENCE GUARD RAILS FIRST (see module docstring for why this order)
# ============================================================================

print("--- Guard rail 1: recording shorter than MIN_RECORDING_S is discarded silently ---")

hist_count_before = len(history.search(limit=200))
short_audio = np.zeros(int(16000 * 0.1), dtype=np.float32)  # 0.1s, well under the 0.4s floor
fake_short = _FakeRecorder(short_audio)
controller.recorder = fake_short

controller._on_hold_started()
check(fake_short.start_calls == [None], "recorder.start(device=None) called once")
check(controller.indicator.state == "recording", "indicator shows recording while held")

controller._on_hold_ended()
check(fake_short.stop_called is True, "recorder.stop() was called")
check(controller.indicator.state == "hidden", "indicator hides immediately for a too-short recording")
check(controller._busy is False, "controller._busy resets to False after a discarded short recording")
check(controller._worker is None, "no transcription worker was created for a too-short recording")
check(len(history.search(limit=200)) == hist_count_before, "no history row added for a discarded short recording")
check(engine.is_loaded is False, "engine still not loaded (short-recording guard never reaches the engine)")

print("\n--- Guard rail 2: bit-exact-silent recording is treated as a mic-permission error ---")

silent_audio = np.zeros(16000, dtype=np.float32)  # 1.0s, well above the floor, but exact zeros
fake_silent = _FakeRecorder(silent_audio)
controller.recorder = fake_silent

controller._on_hold_started()
controller._on_hold_ended()
check(controller.indicator.state == "error", "indicator shows an error state for bit-exact silence")
check(controller._busy is False, "controller._busy resets to False after the silence guard fires")
check(controller._worker is None, "no transcription worker was created for a silent recording")
check(len(history.search(limit=200)) == hist_count_before, "no history row added for a silent recording")
check(engine.is_loaded is False, "engine still not loaded (silence guard never reaches the engine)")

controller.indicator.hide_indicator()  # clear the error state's auto-hide timer before the next check

print("\n--- Guard rail 3: hold_started is ignored while a dictation is already busy ---")

fake_busy = _FakeRecorder(np.ones(16000, dtype=np.float32))
controller.recorder = fake_busy
controller._busy = True  # simulate "already mid-flow" without actually running one
controller._on_hold_started()
check(fake_busy.start_calls == [], "recorder.start() is never called when hold_started arrives while busy")
controller._busy = False  # reset for the next check

print("\n--- Guard rail 4: Esc-cancel stops and discards the recording ---")

fake_cancel = _FakeRecorder(np.ones(16000, dtype=np.float32) * 0.01)
controller.recorder = fake_cancel

controller._on_hold_started()
check(controller.indicator.state == "recording", "indicator shows recording before cancel")
controller._on_cancelled()
check(fake_cancel.cancel_called is True, "recorder.cancel() was called")
check(fake_cancel.stop_called is False, "recorder.stop() was NOT called on cancel (cancel(), not stop())")
check(controller.indicator.state == "hidden", "indicator hides immediately on cancel")
check(controller._busy is False, "controller._busy resets to False after cancel")
check(len(history.search(limit=200)) == hist_count_before, "no history row added for a cancelled recording")
check(engine.is_loaded is False, "engine still not loaded after four zero-inference guard rails")

print("\n--- Guard rail 5: hotkey signals cross from a real non-Qt thread to the main-thread slots ---")

# Every check above drove the controller's private slots
# (_on_hold_started/_on_hold_ended/_on_cancelled) directly, on the main
# thread -- that never exercises the actual seam the real HotkeyListener
# relies on: on_hold_started=self._hold_started_raw.emit is a bound
# Signal.emit passed as a plain callable and invoked from pynput's own
# listener thread (see hotkey.py's module docstring). This check emits
# those same private signals from a REAL background threading.Thread (not
# a QThread, matching what pynput actually uses) and confirms Qt's queued-
# connection delivery lands the call on the main thread correctly.
import threading  # noqa: E402

fake_thread = _FakeRecorder(np.ones(16000, dtype=np.float32) * 0.01)
controller.recorder = fake_thread

t = threading.Thread(target=controller._hold_started_raw.emit)
t.start()
t.join()

# Queued cross-thread signal delivery only happens once the receiving
# thread's Qt event loop actually spins -- poll app.processEvents() for a
# bounded window rather than assuming one call is enough.
deadline = time.time() + 3.0
while not fake_thread.start_calls and time.time() < deadline:
    app.processEvents()

check(
    fake_thread.start_calls == [None],
    "hold_started emitted from a REAL background thread reaches _on_hold_started on the main thread via Qt's queued connection",
)
check(controller.indicator.state == "recording", "indicator correctly shows recording after the cross-thread hold_started")

t2 = threading.Thread(target=controller._cancelled_raw.emit)
t2.start()
t2.join()

deadline = time.time() + 3.0
while controller.indicator.state != "hidden" and time.time() < deadline:
    app.processEvents()

check(fake_thread.cancel_called is True, "cancelled emitted from a real background thread reaches _on_cancelled on the main thread too")
check(controller.indicator.state == "hidden", "indicator hides after the cross-thread cancel")
check(controller._busy is False, "controller._busy resets to False after the cross-thread cancel")
check(engine.is_loaded is False, "engine still not loaded after all five zero-inference guard rails")

print("\n--- Guard rail 6/7/8: OpenRouter rephrase (Phase 6) -- ZERO real inference ---")

# A separate DictationController built around a duck-typed _FakeEngine (same
# stand-in-engine pattern used by
# dev/test_filejob_cancel.py and dev/test_gui_smoke.py's engine-status
# bridge check), so these three checks exercise the REAL
# _TranscribeWorker -> openrouter.rephrase() wiring in dictation.py without
# spending any of this file's one real-inference budget on them. Kept
# separate from the shared `controller`/`engine`/`settings_manager` above so
# nothing here can affect the real happy-path run below.
from unittest.mock import patch  # noqa: E402

from saghi import openrouter  # noqa: E402
from saghi.engine import TranscribeResult  # noqa: E402


class _FakeEngine:
    def __init__(self, text: str, raw_text: str) -> None:
        self.is_loaded = False
        self.device = "cpu"
        self.stack_path = "fallback"
        self._text = text
        self._raw_text = raw_text
        self.calls = 0

    def transcribe_array(self, audio, sr, language="ar", cleanup_level="light"):
        self.calls += 1
        self.is_loaded = True
        return TranscribeResult(
            raw_text=self._raw_text, text=self._text, language=language,
            duration_s=len(audio) / float(sr or 1), inference_s=0.01,
        )


fake_engine_or = _FakeEngine(text="النص المحلي المنظف", raw_text="النص الخام")
fake_engine_status_or = EngineStatusBridge(fake_engine_or)
settings_manager_or = SettingsManager()
settings_manager_or.update(
    language="ar", cleanup_level="light", autopaste=False, save_recordings=False,
    openrouter_enabled=True, openrouter_model="test/model", openrouter_instructions="test instructions",
)
controller_or = DictationController(fake_engine_or, fake_engine_status_or, settings_manager_or)
# Deliberately never call controller_or.start() -- same reasoning as `controller` above.

print("Guard rail 6: rephrase FAILS -> falls back to the local cleaned text (never blocks/loses the result)")

hist_count_before_or = len(history.search(limit=200))
controller_or.recorder = _FakeRecorder(np.ones(16000, dtype=np.float32) * 0.01)

with patch("saghi.openrouter.rephrase") as mock_rephrase:
    mock_rephrase.return_value = openrouter.RephraseResult(
        ok=False, text="النص المحلي المنظف", error="network error: simulated failure"
    )

    controller_or._on_hold_started()
    controller_or._on_hold_ended()
    worker_or = controller_or._worker
    check(worker_or is not None, "guard rail 6: a transcription worker was created")

    loop_or = QEventLoop()
    worker_or.finished.connect(loop_or.quit)
    QTimer.singleShot(30_000, loop_or.quit)
    loop_or.exec()

    check(mock_rephrase.called, "guard rail 6: openrouter.rephrase() was actually called (openrouter_enabled=True)")

check(fake_engine_or.calls == 1, "guard rail 6: the fake engine ran exactly once -- zero real model inference")
check(controller_or.indicator.state == "hidden", "guard rail 6: indicator hides once the flow completes")
check(controller_or._busy is False, "guard rail 6: controller resets to not-busy")

latest_or = history.latest()
check(
    latest_or["text"] == "النص المحلي المنظف",
    f"guard rail 6: history/paste text is the LOCAL cleaned text when rephrase fails (got {latest_or['text']!r})",
)
check(paste.read_clipboard() == "النص المحلي المنظف", "guard rail 6: clipboard holds the local text when rephrase fails")
check(len(history.search(limit=200)) == hist_count_before_or + 1, "guard rail 6: exactly one new history row added")

print("Guard rail 7: rephrase SUCCEEDS -> the rephrased text is what gets pasted/saved")

hist_count_before_or_2 = len(history.search(limit=200))
controller_or.recorder = _FakeRecorder(np.ones(16000, dtype=np.float32) * 0.01)

with patch("saghi.openrouter.rephrase") as mock_rephrase:
    mock_rephrase.return_value = openrouter.RephraseResult(ok=True, text="النص المعاد صياغته", error=None)

    controller_or._on_hold_started()
    controller_or._on_hold_ended()
    worker_or2 = controller_or._worker
    check(worker_or2 is not None, "guard rail 7: a transcription worker was created")

    loop_or2 = QEventLoop()
    worker_or2.finished.connect(loop_or2.quit)
    QTimer.singleShot(30_000, loop_or2.quit)
    loop_or2.exec()

    check(mock_rephrase.called, "guard rail 7: openrouter.rephrase() was actually called")

check(fake_engine_or.calls == 2, "guard rail 7: the fake engine ran exactly twice total across guard rails 6+7 -- still zero real model inference")

latest_or2 = history.latest()
check(
    latest_or2["text"] == "النص المعاد صياغته",
    f"guard rail 7: history/paste text is the REPHRASED text when rephrase succeeds (got {latest_or2['text']!r})",
)
check(latest_or2["raw_text"] == "النص الخام", "guard rail 7: history raw_text is untouched by rephrase (still the pre-cleanup ASR text)")
check(paste.read_clipboard() == "النص المعاد صياغته", "guard rail 7: clipboard holds the rephrased text when rephrase succeeds")
check(len(history.search(limit=200)) == hist_count_before_or_2 + 1, "guard rail 7: exactly one new history row added")

print("Guard rail 8: openrouter_enabled=False (the default for every existing user) -- rephrase() is NEVER called")

# The one real coverage gap guard rails 6/7 alone leave open: both ran with
# openrouter_enabled=True, so neither proves the default-off path actually
# skips the call -- a wiring regression (e.g. an inverted condition) would
# make every dictation start hitting the network with the feature off, and
# guard rails 6/7 would stay green regardless. This check closes that gap.
settings_manager_or.update(openrouter_enabled=False)
# .update() fires DictationController._on_settings_changed on controller_or,
# which only reacts to a hotkey-string change -- safe to call here.

hist_count_before_or_3 = len(history.search(limit=200))
controller_or.recorder = _FakeRecorder(np.ones(16000, dtype=np.float32) * 0.01)

with patch("saghi.openrouter.rephrase") as mock_rephrase:
    controller_or._on_hold_started()
    controller_or._on_hold_ended()
    worker_or3 = controller_or._worker
    check(worker_or3 is not None, "guard rail 8: a transcription worker was created")

    loop_or3 = QEventLoop()
    worker_or3.finished.connect(loop_or3.quit)
    QTimer.singleShot(30_000, loop_or3.quit)
    loop_or3.exec()

    check(not mock_rephrase.called, "guard rail 8: openrouter.rephrase() is NEVER called when openrouter_enabled=False")

check(fake_engine_or.calls == 3, "guard rail 8: the fake engine ran a third time -- still zero real model inference across all three guard rails")

latest_or3 = history.latest()
check(
    latest_or3["text"] == "النص المحلي المنظف",
    f"guard rail 8: history/paste text is the local cleaned text, unchanged, when the feature is off (got {latest_or3['text']!r})",
)
check(len(history.search(limit=200)) == hist_count_before_or_3 + 1, "guard rail 8: exactly one new history row added")

# Defensive: settings_manager_or is a SEPARATE SettingsManager instance from
# the ORIGINAL `settings_manager` used by the real happy-path run below --
# its .update() calls write to the shared settings.json on disk but do NOT
# mutate the original settings_manager's own in-memory Settings object
# (nothing here ever calls settings_manager.reload()). Asserted directly
# rather than just relied upon, so a future refactor that changes this
# can't silently leak openrouter_enabled=True into the real run below.
check(
    settings_manager.current.openrouter_enabled is False,
    "the ORIGINAL settings_manager (used by the real happy-path run below) still has openrouter_enabled=False",
)
check(engine.is_loaded is False, "the REAL engine still not loaded after both OpenRouter guard rails (only the fake engine ran)")

# ============================================================================
# THE ONE REAL INFERENCE RUN -- happy path, real audio, real engine
# ============================================================================

print(f"\n--- Happy path: ONE real end-to-end dictation flow ({SAMPLE_WAV.name}) ---")
print("This genuinely takes 1-2+ minutes (model load + inference).")

sample_audio, sample_sr = sf.read(str(SAMPLE_WAV), always_2d=False)
sample_audio = np.asarray(sample_audio, dtype=np.float32)
check(sample_sr == 16000, f"sample1.wav is already 16kHz (got {sample_sr}Hz) -- matches what Recorder.stop() would hand back")

# A fresh scratch clipboard marker so the "clipboard now contains the
# transcript" check below can't accidentally pass against stale content
# left over from a previous test run.
paste.set_clipboard("PRE-DICTATION-MARKER-should-be-overwritten")

fake_real = _FakeRecorder(sample_audio)
controller.recorder = fake_real

controller._on_hold_started()
check(controller.indicator.state == "recording", "indicator shows recording for the real happy-path clip")

controller._on_hold_ended()
worker = controller._worker
check(worker is not None, "a transcription worker was created for a real, non-silent, long-enough recording")
check(controller.indicator.state == "processing", "indicator switches to processing once the worker starts")

loop = QEventLoop()
worker.finished.connect(loop.quit)  # QThread.finished fires once run() returns, either path

tick_count = {"n": 0}
tick_timer = QTimer()
tick_timer.timeout.connect(lambda: tick_count.__setitem__("n", tick_count["n"] + 1))
tick_timer.start(500)

timeout_timer = QTimer()
timeout_timer.setSingleShot(True)
timeout_timer.timeout.connect(loop.quit)
timeout_timer.start(TIMEOUT_MS)

t0 = time.time()
loop.exec()
wall_s = time.time() - t0

tick_timer.stop()
timeout_timer.stop()

check(wall_s < TIMEOUT_MS / 1000.0, f"the flow finished within the {TIMEOUT_MS/1000:.0f}s timeout (wall={wall_s:.1f}s)")
check(
    tick_count["n"] >= 3,
    f"Qt event loop stayed responsive while transcription ran ({tick_count['n']} ticks in {wall_s:.1f}s -- "
    "proves inference ran on the worker thread, not the UI thread)",
)
check(engine.is_loaded is True, "engine.is_loaded is True after the flow completes (lazy load happened)")

print(f"  wall time: {wall_s:.1f}s")

# ---- indicator / controller state after completion -------------------------

check(controller.indicator.state == "hidden", "indicator hides once the whole flow completes")
check(controller._busy is False, "controller._busy resets to False once the flow completes")
check(controller._pending_audio is None, "controller._pending_audio is cleared once the flow completes")

# ---- history row -------------------------------------------------------------

latest = history.latest()
check(latest is not None, "a history row exists after the happy-path run")
check(latest["source"] == "dictation", f"history row source is 'dictation' (got {latest['source']!r})")
check(latest["language"] == "ar", f"history row language is 'ar' (got {latest['language']!r})")
check(latest["cleanup_level"] == "light", f"history row cleanup_level is 'light' (got {latest['cleanup_level']!r})")
check(bool(latest["text"]), "history row's cleaned text is non-empty")
check(bool(latest["raw_text"]), "history row's raw_text is non-empty")
check(latest["duration_s"] > 0, f"history row duration_s is positive (got {latest['duration_s']})")
check(latest["inference_s"] > 0, f"history row inference_s is positive (got {latest['inference_s']})")

print(f"  transcribed text: {latest['text']!r}")
check(
    "more safe" in latest["text"].lower(),
    f"transcribed text contains the known 'more safe' fragment case-insensitively (got {latest['text']!r}) -- "
    "see module docstring for why this is a substring check, not exact equality",
)

# ---- clipboard (paste.py's ALWAYS-set-clipboard contract) --------------------

clipboard_text = paste.read_clipboard()
check(clipboard_text == latest["text"], f"clipboard contains exactly the final transcript text (got {clipboard_text!r})")

# ---- recording file (save_recordings=True) -----------------------------------

recordings = sorted(paths.recordings_dir().glob("*.wav"))
check(len(recordings) == 1, f"exactly one recording WAV was written (save_recordings=True) -- found {[p.name for p in recordings]}")

saved_audio, saved_sr = sf.read(str(recordings[0]), always_2d=False)
check(saved_sr == 16000, f"saved recording is 16kHz (got {saved_sr}Hz)")
check(
    abs(len(saved_audio) - len(sample_audio)) <= 1,
    f"saved recording has the same sample count as the source clip (got {len(saved_audio)} vs {len(sample_audio)})",
)

print(f"\nALL PASSED ({_checks} checks)")

#!/usr/bin/env python3
"""
Microphone device handling, without real audio hardware.

The v0.2.0 bug report ("no text, pill stops at 0:03, flat waveform") came
down to two things, both checked here with a fake `sounddevice` module:

  1. PortAudio reads the device list once, at import. A mic plugged in
     after Saghi started (USB-C earphones) was invisible: not in Settings,
     and recording silently used another device. recorder.refresh_devices()
     re-reads the list; Recorder.start() and the Settings page call it.
  2. The mic chosen in Settings ("iPhone Microphone", an iPhone Continuity
     mic) delivered bit-exact silence, and the pill blamed a missing
     permission. A silent *chosen* mic is now named in the error.

The fake backend behaves like PortAudio: `query_devices()` returns the
list as of the last `_initialize()`, and plugging a device in only shows
up after `_terminate()` + `_initialize()`.

Run from saghi-mac/:
    QT_QPA_PLATFORM=offscreen SAGHI_DATA_DIR=$(mktemp -d) PYTHONPATH=. python3 dev/test_mic_devices.py
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
assert os.environ.get("SAGHI_DATA_DIR"), "Run with SAGHI_DATA_DIR set to a scratch directory (never the real data dir)"

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

_checks = 0


def check(condition: bool, message: str) -> None:
    global _checks
    assert condition, f"FAILED: {message}"
    _checks += 1
    print(f"ok: {message}")


# ---- fake PortAudio ----------------------------------------------------------


def _dev(name: str, ins: int, outs: int = 0) -> dict:
    return {"name": name, "max_input_channels": ins, "max_output_channels": outs, "default_samplerate": 48000.0}


class FakeSD(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("sounddevice")
        self.hardware = [_dev("iPhone Microphone", 1), _dev("MacBook Air Microphone", 1), _dev("MacBook Air Speakers", 0, 2)]
        self._snapshot = list(self.hardware)
        self.init_calls = 0
        self.terminate_calls = 0
        self.opened_devices: list = []
        self.default = types.SimpleNamespace(device=[1, 2])
        fake = self

        class InputStream:
            def __init__(self, samplerate, channels, dtype, device, callback):
                self.device = device
                fake.opened_devices.append(device)

            def start(self):
                pass

            def stop(self):
                pass

            def close(self):
                pass

        self.InputStream = InputStream

    def query_devices(self, device=None, kind=None):
        if device is None:
            return list(self._snapshot)
        return self._snapshot[device]

    def _terminate(self):
        self.terminate_calls += 1

    def _initialize(self):
        self.init_calls += 1
        self._snapshot = list(self.hardware)


sd = FakeSD()
sys.modules["sounddevice"] = sd

from saghi import recorder  # noqa: E402

# ============================================================================
print("--- 1. A mic plugged in after launch is invisible until the list is refreshed ---")

sd.hardware.insert(1, _dev("Samsung USB C Earphones", 2))  # plugged in now; indexes shift
names_cached = [n for n, _i in recorder.list_input_devices()]
check("Samsung USB C Earphones" not in names_cached, "without a refresh the new mic is missing (PortAudio's cached list)")
names_fresh = [n for n, _i in recorder.list_input_devices(refresh=True)]
check("Samsung USB C Earphones" in names_fresh, "list_input_devices(refresh=True) shows the newly plugged mic")

# ============================================================================
print("\n--- 2. Recorder.start() refreshes, then resolves the saved name to the current index ---")

sd._snapshot = [_dev("iPhone Microphone", 1), _dev("MacBook Air Microphone", 1)]  # stale list again
init_before = sd.init_calls
rec = recorder.Recorder()
rec.start(device="Samsung USB C Earphones")
check(sd.init_calls == init_before + 1, "start() re-initialized PortAudio once before opening")
check(sd.opened_devices[-1] == 1, "the stream opened on the Samsung mic's CURRENT index (1), not a stale one")
check(rec.device_name == "Samsung USB C Earphones", "recorder.device_name names the mic actually used")

print("\n--- 3. No refresh while a stream is open (Pa_Terminate would kill it) ---")
init_before = sd.init_calls
check(recorder.refresh_devices() is False, "refresh_devices() refuses while recording")
check(recorder.list_input_devices(refresh=True) != [], "listing still works while recording (cached list)")
check(sd.init_calls == init_before, "PortAudio was not re-initialized mid-recording")
rec.stop()
check(recorder.refresh_devices() is True, "refresh_devices() works again once the stream is closed")

print("\n--- 3b. No refresh while a slow mic is still opening on another thread ---")
import threading  # noqa: E402
import time  # noqa: E402

opening = threading.Event()
release = threading.Event()
_orig_stream = sd.InputStream


class _SlowStream(_orig_stream):
    def __init__(self, *a, **kw):
        opening.set()
        release.wait(5)
        super().__init__(*a, **kw)


sd.InputStream = _SlowStream
slow_rec = recorder.Recorder()
th = threading.Thread(target=lambda: slow_rec.start(device="iPhone Microphone"))
th.start()
check(opening.wait(5), "a start is in the middle of opening the mic")
init_before = sd.init_calls
check(recorder.refresh_devices() is False, "refresh_devices() refuses while a mic is mid-open")
check(sd.init_calls == init_before, "PortAudio was not re-initialized under the opening stream")
release.set()
th.join(5)
sd.InputStream = _orig_stream
slow_rec.stop()
check(recorder._open_streams == 0, "count back to 0 after the slow stream closes")


class _FailingStream:
    def __init__(self, *a, **kw):
        raise RuntimeError("device gone")


sd.InputStream = _FailingStream
try:
    recorder.Recorder().start(device=None)
    failed = False
except recorder.MicUnavailableError:
    failed = True
sd.InputStream = _orig_stream
check(failed, "a mic that can't open raises MicUnavailableError")
check(recorder._open_streams == 0, "a failed open does not leave the count stuck (refresh still works)")

print("\n--- 4. A saved mic that is gone falls back to the default, and says so ---")
rec2 = recorder.Recorder()
rec2.start(device="Unplugged Headset")
check(sd.opened_devices[-1] is None, "an unknown saved name opens the system default")
check(rec2.device_name is None, "device_name is None when the system default is used")
rec2.cancel()
check(recorder._open_streams == 0, "every stream closed: open-stream count is back to 0")

# ============================================================================
print("\n--- 5. Settings page lists a newly plugged mic without restarting ---")

from PySide6.QtWidgets import QApplication  # noqa: E402

from saghi.settings import SettingsManager  # noqa: E402
from saghi.ui import strings  # noqa: E402
from saghi.ui.settings_page import SettingsPage  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
sm = SettingsManager()
sm.update(microphone="iPhone Microphone")
sd.hardware = [_dev("iPhone Microphone", 1), _dev("MacBook Air Microphone", 1)]
sd._initialize()
page = SettingsPage(sm)
items = [page.mic_combo.itemText(i) for i in range(page.mic_combo.count())]
check("Samsung USB C Earphones" not in items, "page built before the earphones were plugged in")
check(page.mic_combo.currentData() == "iPhone Microphone", "saved mic selected")

sd.hardware.append(_dev("Samsung USB C Earphones", 2))  # plug in while the app runs
page.refresh_microphones()  # what showEvent / opening the list do
items = [page.mic_combo.itemText(i) for i in range(page.mic_combo.count())]
check("Samsung USB C Earphones" in items, "after a refresh the earphones are in the list")
check(page.mic_combo.currentData() == "iPhone Microphone", "refresh keeps the saved selection")
check(sm.current.microphone == "iPhone Microphone", "refresh never writes the setting")

idx = page.mic_combo.findData("Samsung USB C Earphones")
page.mic_combo.setCurrentIndex(idx)
check(sm.current.microphone == "Samsung USB C Earphones", "choosing the earphones saves them")

sd.hardware = [_dev("MacBook Air Microphone", 1)]  # unplug
page.refresh_microphones()
check(page.mic_combo.currentData() == "Samsung USB C Earphones", "an unplugged saved mic stays selected")
check(
    page.mic_combo.currentText() == strings.mic_disconnected_label("Samsung USB C Earphones"),
    "and is marked as not connected",
)
check(sm.current.microphone == "Samsung USB C Earphones", "unplugging does not change the setting")

# ============================================================================
print("\n--- 6. Silent recording: name the chosen mic; blame permission only for the default ---")

from saghi.engine import SaghiEngine  # noqa: E402
from saghi.ui.dictation import DictationController  # noqa: E402
from saghi.ui.engine_status import EngineStatusBridge  # noqa: E402


class _SilentRecorder:
    def __init__(self, device_name):
        self.device_name = device_name

    def start(self, device=None):
        pass

    def stop(self):
        return np.zeros(16000 * 2, dtype=np.float32)

    def cancel(self):
        pass

    def level(self):
        return np.zeros(0, dtype=np.float32), 0.0


engine = SaghiEngine(Path(os.environ.get("SAGHI_DATA_DIR")) / "no-model")  # never loaded here
sm.update(sound_feedback=False, autopaste=False)
controller = DictationController(engine, EngineStatusBridge(engine), sm)

controller.recorder = _SilentRecorder("iPhone Microphone")
controller._on_hold_started()
controller._on_hold_ended()
check(controller.status == "error", "silent chosen mic -> error state")
check(controller.status_message == strings.mic_silent_text("iPhone Microphone"), "the error names «iPhone Microphone»")
check(controller._worker is None, "no transcription was started for silence")
controller.indicator.hide_indicator()

controller.recorder = _SilentRecorder(None)
controller._on_hold_started()
controller._on_hold_ended()
check(controller.status_message == strings.INDICATOR_ERROR_MIC, "silent system-default mic -> the permission message")
controller.indicator.hide_indicator()

# ============================================================================
print("\n--- 7. A slow mic (Continuity iPhone mic: 1-10 s to open) never freezes the UI ---")

import time  # noqa: E402

from saghi.ui import dictation as dictation_mod  # noqa: E402


class _SlowRecorder:
    """Opens like the real «iPhone Microphone» on the owner's Mac: slow, then silence."""

    def __init__(self, open_s, device_name="iPhone Microphone"):
        self.open_s = open_s
        self.device_name = device_name
        self.started = False
        self.cancelled = False

    def start(self, device=None):
        time.sleep(self.open_s)
        self.started = True

    def stop(self):
        return np.zeros(16000 * 2, dtype=np.float32)

    def cancel(self):
        self.cancelled = True

    def level(self):
        return np.zeros(0, dtype=np.float32), 0.0


def pump_until(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    return cond()


# 7a: released while the mic is still opening
controller.recorder = _SlowRecorder(1.0)
t0 = time.monotonic()
controller._on_hold_started()
blocked = time.monotonic() - t0
check(blocked < dictation_mod.MIC_OPEN_WAIT_S + 0.2, f"the UI thread came back after {blocked:.2f}s, not after the mic's 1s")
check(controller.status == "recording", "the pill shows recording (counting) while the mic opens")
controller._on_hold_ended()
check(controller._busy, "still busy: the mic has not answered yet")
check(pump_until(lambda: not controller._busy), "the flow settles once the mic answers")
check(controller.recorder.cancelled, "the late stream is closed, not left recording")
check(controller.status_message == strings.mic_silent_text("iPhone Microphone"), "the error names the slow mic")
controller.indicator.hide_indicator()

# 7b: Esc while the mic is still opening
controller.recorder = _SlowRecorder(0.6)
controller._on_hold_started()
controller._on_cancelled()
check(controller.indicator.state == "hidden", "Esc hides the pill at once, even mid-open")
check(pump_until(lambda: not controller._busy), "the flow settles once the mic answers")
check(controller.recorder.cancelled, "the late stream is closed after Esc")
check(controller.status == "ready", "back to ready, no error after Esc")

# 7c: slow mic that does answer while still held -> a normal recording
controller.recorder = _SlowRecorder(0.6, device_name="Samsung USB C Earphones")
controller._on_hold_started()
check(pump_until(lambda: controller._recording_active), "the recording becomes active when the mic answers")
check(controller.status == "recording", "still recording while held")
controller._on_cancelled()
check(controller._busy is False and controller.status == "ready", "a normal Esc after that still works")

# 7d: a fast mic keeps the old, synchronous path
fast = _SlowRecorder(0.0)
controller.recorder = fast
controller._on_hold_started()
check(controller._recording_active and fast.started, "a fast mic is recording the moment the press is handled")
controller._on_cancelled()

controller.stop()

print(f"\nALL PASSED ({_checks} checks)")

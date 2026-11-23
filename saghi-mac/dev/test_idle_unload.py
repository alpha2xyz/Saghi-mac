#!/usr/bin/env python3
"""
Tests for the engine's idle unload -- no real model, no microphone:

  a. loaded, then unloaded after the idle timeout (and it reloads lazily).
  b. calls spaced shorter than the timeout keep it loaded (a file job).
  c. never unloads while an in-flight call holds the engine lock.
  d. idle_unload_s=None (or <= 0) never unloads.
  e. EngineStatusBridge flips ready -> cold after an unload.

The model load and the inference are stubbed; only the timing logic runs.

Run:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=. <venv>/bin/python dev/test_idle_unload.py

Plain assert-based, no pytest -- same style as the other dev/ tests.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

SCRATCH = Path(tempfile.mkdtemp(prefix="saghi-test-idle-unload-"))
os.environ["SAGHI_DATA_DIR"] = str(SCRATCH / "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.pop("SAGHI_IDLE_UNLOAD_S", None)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
from PySide6.QtCore import QCoreApplication  # noqa: E402

from saghi.engine import SaghiEngine  # noqa: E402
from saghi.ui.engine_status import (  # noqa: E402
    STATE_COLD,
    STATE_READY,
    EngineStatusBridge,
)

_checks = 0


def check(condition: bool, message: str) -> None:
    global _checks
    assert condition, f"FAILED: {message}"
    _checks += 1
    print(f"ok: {message}")


class StubEngine(SaghiEngine):
    """SaghiEngine with the model load and inference replaced."""

    loads = 0
    infer_s = 0.0

    def __init__(self, idle_unload_s):
        super().__init__(SCRATCH, device="cpu", idle_unload_s=idle_unload_s)

    @staticmethod
    def native_stack_available() -> bool:
        return True

    def _load_native(self) -> None:
        StubEngine.loads += 1
        self._processor = object()
        self._model = object()

    def _infer_array(self, arr, sr, language) -> str:
        time.sleep(self.infer_s)
        return "test"


AUDIO = np.zeros(1600, dtype=np.float32)
TIMEOUT = 0.3


def run() -> None:
    # a. unloads after idle, reloads lazily on the next call
    eng = StubEngine(TIMEOUT)
    eng.transcribe_array(AUDIO, 16000)
    check(eng.is_loaded, "a: loaded after a transcription")
    time.sleep(TIMEOUT * 3)
    check(not eng.is_loaded, "a: unloaded after the idle timeout")
    check(eng._processor is None, "a: processor dropped too")
    eng.transcribe_array(AUDIO, 16000)
    check(eng.is_loaded and StubEngine.loads == 2, "a: next call reloads lazily")

    # b. calls spaced shorter than the timeout keep it loaded
    eng = StubEngine(TIMEOUT)
    for _ in range(12):
        eng.transcribe_array(AUDIO, 16000)
        time.sleep(TIMEOUT / 3)
        assert eng.is_loaded, "unloaded in the middle of a job"
    check(eng.is_loaded, "b: spaced calls (job with gaps) never unloaded it")
    time.sleep(TIMEOUT * 3)
    check(not eng.is_loaded, "b: unloads once the job is over")

    # c. never unloads while a call holds the lock
    eng = StubEngine(TIMEOUT)
    eng.infer_s = TIMEOUT * 3
    worker = threading.Thread(target=lambda: eng.transcribe_array(AUDIO, 16000))
    worker.start()
    time.sleep(TIMEOUT * 2)
    check(eng.is_loaded, "c: still loaded while a call is in flight")
    worker.join()
    check(eng.is_loaded, "c: still loaded right after the call ended")
    time.sleep(TIMEOUT * 3)
    check(not eng.is_loaded, "c: unloads after the call, once idle")
    with eng._lock:
        eng.load()
        time.sleep(TIMEOUT * 3)
        check(eng.is_loaded, "c: a held lock blocks the unload")
    time.sleep(TIMEOUT * 3)
    check(not eng.is_loaded, "c: unloads after the lock is released")

    # d. None / 0 never unloads
    for value in (None, 0):
        eng = StubEngine(value)
        eng.transcribe_array(AUDIO, 16000)
        time.sleep(TIMEOUT * 3)
        check(eng.is_loaded, f"d: idle_unload_s={value!r} never unloads")

    # env var overrides the argument
    os.environ["SAGHI_IDLE_UNLOAD_S"] = str(TIMEOUT)
    try:
        eng = StubEngine(None)
        eng.transcribe_array(AUDIO, 16000)
        time.sleep(TIMEOUT * 3)
        check(not eng.is_loaded, "env SAGHI_IDLE_UNLOAD_S overrides the argument")
    finally:
        os.environ.pop("SAGHI_IDLE_UNLOAD_S", None)

    # e. status bridge: ready -> cold after an unload
    app = QCoreApplication.instance() or QCoreApplication([])
    eng = StubEngine(TIMEOUT)
    eng.load()
    bridge = EngineStatusBridge(eng, poll_ms=50)
    seen = []
    bridge.status_changed.connect(lambda state, dev, stack: seen.append(state))
    check(bridge.state == STATE_READY, "e: bridge starts ready for a loaded engine")
    deadline = time.monotonic() + TIMEOUT * 6
    while time.monotonic() < deadline and bridge.state != STATE_COLD:
        app.processEvents()
        time.sleep(0.02)
    check(bridge.state == STATE_COLD and seen == [STATE_COLD], "e: bridge flipped ready -> cold once")
    eng.load()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and bridge.state != STATE_READY:
        app.processEvents()
        time.sleep(0.02)
    check(bridge.state == STATE_READY, "e: bridge goes back to ready after a reload")


if __name__ == "__main__":
    run()
    print(f"\nall {_checks} checks passed")

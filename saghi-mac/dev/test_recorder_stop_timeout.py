#!/usr/bin/env python3
"""
Recorder.stop() must never freeze the UI thread.

saghi.log showed the whole app frozen with the UI thread inside
sounddevice's stream.stop() -> Pa_StopStream -> CoreAudio
AudioOutputUnitStop, waiting on a CoreAudio mutex forever. stop() now
stops/closes the stream on a helper thread and waits at most
recorder.STOP_TIMEOUT_S. Checked here with fake streams:

  1. a normal stream: stop() returns the audio, the stream is closed and
     the open-stream count goes back to 0.
  2. a stream whose stop() blocks: stop() still returns the audio within
     the timeout; the stuck stream stays counted (so refresh_devices()
     cannot Pa_Terminate under it) until it finally finishes.

Run from saghi-mac/:
    SAGHI_DATA_DIR=$(mktemp -d) PYTHONPATH=. python3 dev/test_recorder_stop_timeout.py
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from saghi import recorder as rec  # noqa: E402

_checks = 0


def check(cond: bool, label: str) -> None:
    global _checks
    _checks += 1
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        sys.exit(1)


class _FakeStream:
    def __init__(self, release: threading.Event | None = None) -> None:
        self._release = release
        self.closed = False

    def stop(self) -> None:
        if self._release is not None:
            self._release.wait()

    def close(self) -> None:
        self.closed = True


def _recording(stream: _FakeStream) -> rec.Recorder:
    """A Recorder in the state start() leaves it in, with 1 s of audio captured."""
    r = rec.Recorder()
    with rec._pa_lock:
        rec._open_streams += 1
    r._stream = stream
    r._recording = True
    r._stream_samplerate = rec.TARGET_SAMPLE_RATE
    r._on_audio(np.full(rec.TARGET_SAMPLE_RATE, 0.1, dtype=np.float32))
    return r


# 1. normal stop
rec._open_streams = 0
s = _FakeStream()
r = _recording(s)
audio = r.stop()
check(audio.size == rec.TARGET_SAMPLE_RATE, "normal stop returns the captured audio")
check(s.closed, "normal stop closes the stream")
check(rec._open_streams == 0, "normal stop releases the open-stream count")
check(not r.is_recording, "not recording after stop")

# 2. stuck stop
release = threading.Event()
s = _FakeStream(release)
r = _recording(s)
t0 = time.monotonic()
audio = r.stop()
elapsed = time.monotonic() - t0
check(elapsed < rec.STOP_TIMEOUT_S + 0.5, f"stuck stop returns within the timeout ({elapsed:.2f}s)")
check(audio.size == rec.TARGET_SAMPLE_RATE, "stuck stop still returns the captured audio")
check(rec._open_streams == 1, "the stuck stream stays counted as open")
check(rec.refresh_devices() is False, "refresh_devices() is skipped while the stuck stream is open")
release.set()
deadline = time.monotonic() + 2.0
while rec._open_streams and time.monotonic() < deadline:
    time.sleep(0.01)
check(rec._open_streams == 0 and s.closed, "once the stream finally stops it is closed and released")

print(f"\nALL PASSED ({_checks} checks)")

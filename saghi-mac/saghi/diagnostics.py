"""
Diagnostics: a log file on disk and a UI-freeze watchdog.

Until v0.2.0 the app logged to stderr only, so when dictation went wrong on a
real Mac (pill stuck at 0:03, flat waveform, no text) there was nothing to
read afterwards. This module adds:

  - `setup_logging()` -- root logging to stderr (as before) AND to a rotating
    `saghi.log` in the data dir (~/Library/Application Support/Saghi/), plus
    `faulthandler` into a sibling `crash.log` so hard crashes leave a trace.
  - `FreezeWatchdog` -- a background thread that expects a heartbeat from the
    Qt main thread. If the main thread goes quiet for longer than
    `threshold_s`, it dumps the stack of every thread into the log once per
    freeze, which is exactly what is needed to find a deadlock or a blocking
    call on the UI thread.

ZERO Qt dependency (same convention as recorder.py/engine.py): the caller
drives `beat()` from a QTimer.
"""

from __future__ import annotations

import faulthandler
import io
import logging
import logging.handlers
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Optional

from . import paths

logger = logging.getLogger("saghi.diagnostics")

LOG_FORMAT = "%(asctime)s %(threadName)s %(name)s %(levelname)s: %(message)s"

_crash_file = None  # kept open for faulthandler for the life of the process


def log_path() -> Path:
    return paths.data_dir() / "saghi.log"


def setup_logging(level: int = logging.INFO) -> Optional[Path]:
    """Log to stderr and to saghi.log. Returns the log file path, or None if it couldn't be opened."""
    global _crash_file
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter(LOG_FORMAT)
    if not any(isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stderr for h in root.handlers):
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        logger.exception("Could not open log file %s -- logging to stderr only", path)
        return None

    try:
        _crash_file = open(path.parent / "crash.log", "a", encoding="utf-8")
        faulthandler.enable(file=_crash_file, all_threads=True)
    except OSError:
        logger.exception("Could not enable faulthandler")

    logger.info("---- Saghi starting (python %s, platform %s) -- log: %s", sys.version.split()[0], sys.platform, path)
    return path


def dump_all_threads() -> str:
    """Formatted stacks of every live Python thread."""
    frames = sys._current_frames()
    names = {t.ident: t.name for t in threading.enumerate()}
    out = io.StringIO()
    for ident, frame in frames.items():
        out.write(f"\n--- thread {names.get(ident, '?')} ({ident}) ---\n")
        out.write("".join(traceback.format_stack(frame)))
    return out.getvalue()


class FreezeWatchdog:
    """Logs every thread's stack once when `beat()` hasn't been called for `threshold_s`."""

    def __init__(self, threshold_s: float = 5.0, poll_s: float = 1.0) -> None:
        self._threshold_s = threshold_s
        self._poll_s = poll_s
        self._last_beat = time.monotonic()
        self._reported = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="saghi-watchdog", daemon=True)

    def start(self) -> None:
        self._last_beat = time.monotonic()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def beat(self) -> None:
        if self._reported:
            logger.warning("UI thread responsive again after %.1fs", time.monotonic() - self._last_beat)
            self._reported = False
        self._last_beat = time.monotonic()

    def _run(self) -> None:
        while not self._stop.wait(self._poll_s):
            stalled = time.monotonic() - self._last_beat
            if stalled > self._threshold_s and not self._reported:
                self._reported = True
                logger.error("UI thread frozen for %.1fs -- stacks of all threads:%s", stalled, dump_all_threads())

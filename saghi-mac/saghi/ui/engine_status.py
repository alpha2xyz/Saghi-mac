"""
Bridges SaghiEngine's plain-Python state (`is_loaded` / `device` /
`stack_path`) to a Qt signal so widgets (the main-window header chip, the
tray menu's status line) can react without polling SaghiEngine themselves.

The GUI must never load the model at startup: status starts "cold". A
caller that's about to do something which will trigger `engine.load()`
(e.g. the file-job page, right before starting a worker) should call
`mark_loading()` first so the UI shows "loading" instead of jumping
straight from "cold" to "ready". A QTimer separately polls
`engine.is_loaded` (cheap -- a None check) so a load triggered from
*outside* the GUI's own trigger points -- e.g. an external `/api/transcribe`
request from an external client, since the engine instance is shared between this app's
own API-server thread and the GUI, see ui/app.py -- still flips the UI to
"ready" once it completes, even without a matching mark_loading() call.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

STATE_COLD = "cold"
STATE_LOADING = "loading"
STATE_READY = "ready"

_DEFAULT_POLL_MS = 1000


class EngineStatusBridge(QObject):
    # (state, device, stack_path) -- device/stack_path are "" until ready
    status_changed = Signal(str, str, str)

    def __init__(self, engine, poll_ms: int = _DEFAULT_POLL_MS, parent=None):
        super().__init__(parent)
        self._engine = engine
        self._state = STATE_READY if engine.is_loaded else STATE_COLD
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(poll_ms)

    @property
    def state(self) -> str:
        return self._state

    @property
    def device(self) -> str:
        return self._engine.device or ""

    @property
    def stack_path(self) -> str:
        return self._engine.stack_path or ""

    def mark_loading(self) -> None:
        """Call right before doing something that will trigger engine.load()."""
        if self._engine.is_loaded:
            return
        if self._state != STATE_LOADING:
            self._state = STATE_LOADING
            self._emit()

    def _poll(self) -> None:
        if self._engine.is_loaded and self._state != STATE_READY:
            self._state = STATE_READY
            self._emit()

    def _emit(self) -> None:
        self.status_changed.emit(self._state, self.device, self.stack_path)

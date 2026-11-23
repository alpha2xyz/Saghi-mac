"""
The notch island: Saghi's only on-screen presence while dictating.

STUB (API contract only); the drawing and motion land in the next commit.
Drop-in replacement for `indicator.IndicatorWindow` as used by
`ui/dictation.py`: same signals, same show_*/hide_* methods, same `state`
values ("hidden", "recording", "processing", "error"), so the controller only
swaps the class. Pill-only knobs (style, colour, idle mode, anchor, glass)
are accepted and ignored.
"""

from __future__ import annotations

from typing import Callable, Optional, Tuple

import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

LevelFn = Callable[[], Tuple[np.ndarray, float]]


class NotchIsland(QWidget):
    state_changed = Signal(str)
    open_requested = Signal()  # the user clicked the island: open Saghi's window
    anchor_moved = Signal(int, int)  # never emitted (the island does not move)
    mode_change_requested = Signal(str)  # never emitted

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._state = "hidden"
        self._idle_status = "ready"

    # ---- dictation states ----
    def show_recording(self, level_fn: LevelFn) -> None:
        self._enter("recording")

    def show_processing(self, text: Optional[str] = None) -> None:
        self._enter("processing")

    def show_error(self, message: str, auto_hide_ms: int = 2500) -> None:
        self._enter("error")

    def show_discarded(self) -> None:
        """Recording thrown away (Esc): close at once, no processing step."""
        self._enter("hidden")

    def hide_indicator(self) -> None:
        self._enter("hidden")

    @property
    def state(self) -> str:
        return self._state

    def _enter(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.state_changed.emit(state)

    # ---- idle status (ready / loading / off), shown as a hint on hover ----
    def set_idle_status(self, status: str) -> None:
        self._idle_status = status

    @property
    def idle_status(self) -> str:
        return self._idle_status

    # ---- pill-only knobs, accepted and ignored ----
    def set_style(self, style: str) -> None:
        pass

    def set_color(self, hex_color: str) -> None:
        pass

    def set_idle_mode(self, mode: str) -> None:
        pass

    @property
    def idle_mode(self) -> str:
        return "active"

    def set_glass_enabled(self, enabled: bool) -> None:
        pass

    def set_anchor(self, point) -> None:
        pass

    def anchor(self) -> Tuple[int, int]:
        return (0, 0)

    def place_top(self) -> None:
        pass

    def place_bottom(self) -> None:
        pass

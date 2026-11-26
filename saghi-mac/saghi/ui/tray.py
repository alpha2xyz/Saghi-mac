"""
Menu-bar (tray) icon: the always-visible "what is Saghi doing" light plus a
small menu. This is what keeps Saghi "living in the background" per
README_AR.md ("أيقونة دائمة بجانب الساعة") -- closing the main window (see
MainWindow.closeEvent) just hides it; the icon and the process stay alive
until "إنهاء" is chosen here.

The icon follows the one status the DictationController publishes (see
ui/dictation.py, STATUS), so it always agrees with the floating pill:

    ready       waveform (full)           template glyph (tinted by macOS)
    recording   mic.fill in red           the only coloured icon
    processing  waveform, levels cycling  animated
    loading     hourglass, pulsing        animated
    error       exclamationmark.triangle
    disabled    mic.slash

Every glyph except the red recording mic is a template (mask) icon, so it
follows the menu bar's light/dark tint on its own. Frames are rendered once
up front; the animations only swap cached QIcons on a slow timer, which
runs in the two animated states and nowhere else.

Menu: status line, hotkey hint, "open Saghi settings" and "quit". A left
click only opens the menu; the window opens from "open Saghi settings",
the notch island, or launching Saghi.app again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import sf_symbols, strings
from .engine_status import EngineStatusBridge

# The app icon: still the Dock / window icon (ui/app.py); the menu bar shows
# the status glyphs below instead.
_ICON_PATH = Path(__file__).parent / "assets" / "saghi.png"

_ICON_SIZE = 18
_RECORDING_COLOR = "#FF3B30"
_STATUS_SYMBOLS = {
    "ready": "waveform",
    "recording": "mic.fill",
    "processing": "waveform",
    "loading": "hourglass",
    "error": "exclamationmark.triangle",
    "disabled": "mic.slash",
}
_PROCESSING_LEVELS = (0.2, 0.45, 0.7, 1.0)  # the waveform's variable value, one per frame
_LOADING_OPACITIES = (1.0, 0.55)  # the hourglass pulse
_ANIMATION_INTERVAL_MS = 160  # ~6 fps


def _with_opacity(icon: QIcon, opacity: float) -> QIcon:
    """A mask icon drawn at reduced alpha (the menu bar tints it and keeps the alpha).

    Built at both 1x and 2x, like sf_symbols.icon(): Qt's status item picks
    the pixmap from the app-wide devicePixelRatio (the highest of all
    screens), not the primary screen's, so a single-density frame could be
    drawn at half size on a mixed Retina/non-Retina setup.
    """
    result = QIcon()
    for dpr in (1.0, 2.0):
        source = icon.pixmap(QSize(_ICON_SIZE, _ICON_SIZE), dpr)
        faded = QPixmap(source.size())
        faded.setDevicePixelRatio(source.devicePixelRatio())
        faded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(faded)
        painter.setOpacity(opacity)
        painter.drawPixmap(0, 0, source)
        painter.end()
        result.addPixmap(faded)
    result.setIsMask(True)
    return result


def _build_frames() -> dict[str, list[QIcon]]:
    frames: dict[str, list[QIcon]] = {}
    for state, symbol in _STATUS_SYMBOLS.items():
        if state == "recording":
            frames[state] = [sf_symbols.icon(symbol, _ICON_SIZE, _RECORDING_COLOR, mask=False)]
        elif state == "processing":
            frames[state] = [
                sf_symbols.icon(symbol, _ICON_SIZE, mask=True, variable=level) for level in _PROCESSING_LEVELS
            ]
        elif state == "loading":
            base = sf_symbols.icon(symbol, _ICON_SIZE, mask=True)
            frames[state] = [base if opacity >= 1.0 else _with_opacity(base, opacity) for opacity in _LOADING_OPACITIES]
        else:
            frames[state] = [sf_symbols.icon(symbol, _ICON_SIZE, mask=True)]
    return frames


class SaghiTray(QSystemTrayIcon):
    def __init__(
        self,
        main_window,
        engine_status: EngineStatusBridge,
        on_quit,
        dictation_controller: Optional[object] = None,
        settings_manager=None,
        on_open=None,
        parent=None,
    ):
        frames = _build_frames()
        super().__init__(frames["ready"][0], parent)
        self._frames = frames
        self._main_window = main_window
        self._settings_manager = settings_manager
        self._on_open = on_open  # app-level "show the window" (also brings the app forward)
        self._state = "ready"
        self._frame_index = 0

        # Ticks only while the status is processing or loading.
        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(_ANIMATION_INTERVAL_MS)
        self._animation_timer.timeout.connect(self._on_animation_tick)

        # Menu: what Saghi is doing, which keys to hold, open settings, quit.
        # A click on the icon only opens this menu (it never opens the window).
        menu = QMenu()

        self._state_action = QAction(menu)
        self._state_action.setEnabled(False)
        menu.addAction(self._state_action)

        # Which keys to hold -- needs the settings, the hotkey is configurable.
        self._hint_action: Optional[QAction] = None
        if settings_manager is not None:
            self._hint_action = QAction(strings.hotkey_hint(settings_manager.current.hotkey), menu)
            self._hint_action.setEnabled(False)
            menu.addAction(self._hint_action)
            settings_manager.on_change(self._on_settings_changed)

        menu.addSeparator()

        open_action = QAction(strings.TRAY_OPEN, menu)
        open_action.triggered.connect(self._show_main_window)
        menu.addAction(open_action)

        menu.addSeparator()

        quit_action = QAction(strings.TRAY_QUIT, menu)
        quit_action.triggered.connect(on_quit)
        menu.addAction(quit_action)

        self.setContextMenu(menu)

        if dictation_controller is not None:
            dictation_controller.status_changed.connect(self.set_status)
            self.set_status(dictation_controller.status, dictation_controller.status_message)
        else:
            self.set_status("ready")

    # ---- status ---------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    def set_status(self, state: str, message: str = "") -> None:
        """Show `state` (disabled/recording/processing/error/loading/ready) in the icon, tooltip and menu."""
        if state not in self._frames:
            state = "ready"
        line = strings.status_line(state, message)
        self._state_action.setText(line)
        self.setToolTip(f"{strings.APP_TITLE} — {line}")

        if state == self._state:
            return  # icon and animation are already right; only the text could differ
        self._state = state
        self._frame_index = 0
        self.setIcon(self._frames[state][0])
        if len(self._frames[state]) > 1:
            self._animation_timer.start()
        else:
            self._animation_timer.stop()

    def _on_animation_tick(self) -> None:
        frames = self._frames[self._state]
        self._frame_index = (self._frame_index + 1) % len(frames)
        self.setIcon(frames[self._frame_index])

    # ---- menu ------------------------------------------------------------------

    def _on_settings_changed(self, settings) -> None:
        if self._hint_action is not None:
            self._hint_action.setText(strings.hotkey_hint(settings.hotkey))

    def show_main_window(self) -> None:
        self._main_window.show()
        self._main_window.raise_()
        self._main_window.activateWindow()

    def _show_main_window(self) -> None:
        if self._on_open is not None:
            self._on_open()
        else:
            self.show_main_window()

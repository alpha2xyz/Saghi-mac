"""
System tray icon: menu with "فتح صاغي" (show the main window), a disabled
engine-status line, a checkable "تفعيل الإملاء" (enable dictation) toggle
(Phase 5), and "إنهاء" (quit). This is what keeps Saghi "living in the
background" per README_AR.md ("أيقونة دائمة بجانب الساعة") -- closing the
main window (see MainWindow.closeEvent) just hides it; the tray icon and
the process stay alive until "إنهاء" is chosen here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import strings
from .engine_status import EngineStatusBridge

_ICON_PATH = Path(__file__).parent / "assets" / "saghi.png"


class SaghiTray(QSystemTrayIcon):
    def __init__(
        self,
        main_window,
        engine_status: EngineStatusBridge,
        on_quit,
        dictation_controller: Optional[object] = None,
        parent=None,
    ):
        super().__init__(QIcon(str(_ICON_PATH)), parent)
        self._main_window = main_window
        self.setToolTip(strings.APP_TITLE)

        menu = QMenu()

        open_action = QAction(strings.TRAY_OPEN, menu)
        open_action.triggered.connect(self._show_main_window)
        menu.addAction(open_action)

        self._status_action = QAction(
            strings.format_tray_engine_line(engine_status.state, engine_status.device), menu
        )
        self._status_action.setEnabled(False)
        menu.addAction(self._status_action)

        menu.addSeparator()

        # Phase 5: checkable toggle for live dictation, default on. Kept
        # optional (dictation_controller=None is a no-op action, still
        # shown but disabled) so this class stays constructible exactly as
        # before wherever a caller doesn't have a controller yet -- see
        # dev/test_gui_smoke.py's SaghiTray(win, bridge, on_quit=...) call,
        # unmodified since Phase 4.
        self._dictation_action = QAction(strings.TRAY_DICTATION_TOGGLE, menu)
        self._dictation_action.setCheckable(True)
        if dictation_controller is not None:
            self._dictation_action.setChecked(dictation_controller.enabled)
            self._dictation_action.toggled.connect(dictation_controller.set_enabled)
        else:
            self._dictation_action.setChecked(True)
            self._dictation_action.setEnabled(False)
        menu.addAction(self._dictation_action)

        menu.addSeparator()

        quit_action = QAction(strings.TRAY_QUIT, menu)
        quit_action.triggered.connect(on_quit)
        menu.addAction(quit_action)

        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

        engine_status.status_changed.connect(self._on_status_changed)

    def _on_status_changed(self, state: str, device: str, stack_path: str) -> None:
        self._status_action.setText(strings.format_tray_engine_line(state, device))

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_main_window()

    def _show_main_window(self) -> None:
        self._main_window.show()
        self._main_window.raise_()
        self._main_window.activateWindow()

"""
Main window: sidebar navigation between three pages (History, File
transcription, Settings) plus a header engine-status chip.

RTL note: the app sets `QApplication.setLayoutDirection(RightToLeft)` once,
globally, in ui/app.py. Qt then automatically mirrors every QHBoxLayout it
lays out from that point on -- this file builds the header/body in plain
"nav first, then content" order and Qt places the nav sidebar on the
visual right, exactly as README_AR.md's Arabic-first UI describes, with no
manual left/right juggling needed here.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import strings
from .engine_status import EngineStatusBridge
from .filejob_page import FileJobPage
from .history_page import HistoryPage
from .settings_page import SettingsPage

_QSS = """
QListWidget {
    background: #fafafa;
    border: none;
    outline: none;
    font-size: 14px;
    padding-top: 8px;
}
QListWidget::item {
    padding: 10px 14px;
    border-radius: 6px;
    margin: 2px 8px;
}
QListWidget::item:selected {
    background: #4A90D9;
    color: white;
}
#headerBar {
    background: #ffffff;
    border-bottom: 1px solid #e0e0e0;
}
#appTitle {
    font-size: 16px;
    font-weight: 600;
}
#statusChip {
    background: #eef2f7;
    border-radius: 10px;
    padding: 3px 12px;
    font-size: 12px;
    color: #555555;
}
#dropCard {
    background: #fafbfc;
    border: 1px dashed #b7c2cf;
    border-radius: 8px;
}
"""


class MainWindow(QMainWindow):
    def __init__(self, settings_manager, engine, engine_status: EngineStatusBridge, parent=None):
        super().__init__(parent)
        self.setWindowTitle(strings.APP_TITLE)
        self.resize(1100, 720)
        self.setStyleSheet(_QSS)

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        header.setObjectName("headerBar")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 10, 16, 10)
        title = QLabel(strings.APP_TITLE)
        title.setObjectName("appTitle")
        header_layout.addWidget(title)
        header_layout.addStretch()
        self.status_chip = QLabel(strings.format_engine_status(engine_status.state, engine_status.device))
        self.status_chip.setObjectName("statusChip")
        header_layout.addWidget(self.status_chip)
        outer.addWidget(header)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.nav = QListWidget()
        self.nav.setFixedWidth(180)

        self.pages = QStackedWidget()

        self.history_page = HistoryPage()
        self.filejob_page = FileJobPage(engine, engine_status)
        self.settings_page = SettingsPage(settings_manager)

        for label, widget in (
            (strings.NAV_HISTORY, self.history_page),
            (strings.NAV_FILEJOB, self.filejob_page),
            (strings.NAV_SETTINGS, self.settings_page),
        ):
            QListWidgetItem(label, self.nav)
            self.pages.addWidget(widget)

        self.nav.currentRowChanged.connect(self._on_nav_changed)
        self.nav.setCurrentRow(0)

        body_layout.addWidget(self.nav)
        body_layout.addWidget(self.pages)
        outer.addWidget(body)

        self.setCentralWidget(central)

        engine_status.status_changed.connect(self._on_engine_status_changed)

    def _on_nav_changed(self, row: int) -> None:
        self.pages.setCurrentIndex(row)
        if self.pages.currentWidget() is self.history_page:
            self.history_page.refresh()

    def _on_engine_status_changed(self, state: str, device: str, stack_path: str) -> None:
        self.status_chip.setText(strings.format_engine_status(state, device))

    def closeEvent(self, event) -> None:  # noqa: N802 -- Qt override naming
        # Tray app: closing the window just hides it. The process, tray
        # icon, and API server thread all keep running in the background
        # -- see ui/app.py / ui/tray.py -- matching README_AR.md's
        # documented tray behavior ("أيقونة دائمة بجانب الساعة").
        event.ignore()
        self.hide()

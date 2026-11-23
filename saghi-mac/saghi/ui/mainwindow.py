"""
Main window: a small macOS-style utility window. A sidebar (on the visual
right in RTL) with three pages (Home, Configuration, History), the engine
status dot at its foot, and calm rounded cards for content. The file
transcription page opens from Configuration as a separate small dialog.

There is no menu-bar icon and no Dock icon: the window is opened from the
notch island or by launching Saghi again, and closing it only hides it.

RTL note: the app sets `QApplication.setLayoutDirection(RightToLeft)` once,
globally, in ui/app.py. Qt then mirrors every QHBoxLayout, so the body is
built in plain "sidebar first, then content" order.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from . import strings, theme, widgets
from .engine_status import EngineStatusBridge
from .filejob_page import FileJobPage
from .history_page import HistoryPage
from .home_page import HomePage
from .settings_page import SettingsPage
from .sidebar import SidebarNav

_SIDEBAR_WIDTH = 168


class _PageFrame(QWidget):
    """A page under its large title + subtitle."""

    def __init__(self, title: str, subtitle: str, page: QWidget) -> None:
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QWidget()
        hv = QVBoxLayout(head)
        hv.setContentsMargins(24, 14, 24, 8)
        hv.setSpacing(2)
        self.title = widgets.label(title, "pageTitle")
        self.subtitle = widgets.label(subtitle, "pageSubtitle")
        hv.addWidget(self.title)
        hv.addWidget(self.subtitle)
        self.subtitle.setVisible(bool(subtitle))
        v.addWidget(head)
        v.addWidget(page, 1)

    def set_titles(self, title: str, subtitle: str) -> None:
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        self.subtitle.setVisible(bool(subtitle))


class MainWindow(QMainWindow):
    def changeEvent(self, event):  # noqa: N802 (Qt API name)
        # macOS light/dark (or accent colour) switch while the app is open:
        # rebuild the stylesheet and the tinted icons so nothing keeps the
        # old colours.
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            # (Can fire while __init__ is still building the widgets.)
            if hasattr(self, "nav") and not getattr(self, "_restyling", False):
                self._restyling = True
                try:
                    self._apply_theme()
                finally:
                    self._restyling = False
        super().changeEvent(event)

    # Asked of the app (ui/app.py), which owns the process lifecycle.
    quit_requested = Signal()
    restart_requested = Signal()

    def __init__(self, settings_manager, engine, engine_status: EngineStatusBridge, parent=None):
        super().__init__(parent)
        self.setWindowTitle(strings.APP_TITLE)
        self.resize(680, 560)
        self.setMinimumSize(600, 480)

        central = QWidget()
        body_layout = QHBoxLayout(central)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        # ---- sidebar ---------------------------------------------------------
        self.sidebar = QWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.sidebar.setFixedWidth(_SIDEBAR_WIDTH)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(0, 16, 0, 12)
        side.setSpacing(0)

        brand = QWidget()
        bh = QHBoxLayout(brand)
        bh.setContentsMargins(16, 0, 16, 12)
        bh.setSpacing(8)
        self._logo = QLabel()
        from .tray import _ICON_PATH

        logo_px = QPixmap(str(_ICON_PATH))
        if not logo_px.isNull():
            logo_px.setDevicePixelRatio(2.0)
            self._logo.setPixmap(logo_px.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        bh.addWidget(self._logo)
        bh.addWidget(widgets.label(strings.APP_TITLE, "appName"))
        bh.addStretch()
        side.addWidget(brand)

        self.nav = SidebarNav()
        side.addWidget(self.nav, 1)

        local_row = QWidget()
        lh = QHBoxLayout(local_row)
        lh.setContentsMargins(18, 6, 18, 2)
        lh.setSpacing(8)
        self.local_dot = widgets.StatusDot()
        self.local_dot.set_state("local")
        lh.addWidget(self.local_dot)
        self.local_chip = QLabel(strings.LOCAL_CHIP)
        self.local_chip.setObjectName("statusChip")
        lh.addWidget(self.local_chip)
        lh.addStretch()
        side.addWidget(local_row)

        status_row = QWidget()
        sh = QHBoxLayout(status_row)
        sh.setContentsMargins(18, 2, 18, 2)
        sh.setSpacing(8)
        self.status_dot = widgets.StatusDot()
        self.status_dot.set_state(engine_status.state)
        sh.addWidget(self.status_dot)
        self.status_chip = QLabel(strings.format_engine_status(engine_status.state, engine_status.device))
        self.status_chip.setObjectName("statusChip")
        sh.addWidget(self.status_chip)
        sh.addStretch()
        side.addWidget(status_row)

        version = widgets.label(strings.version_label(__version__), "versionLabel")
        version.setContentsMargins(18, 2, 18, 0)
        side.addWidget(version)

        # ---- pages -------------------------------------------------------------
        content = QWidget()
        content.setObjectName("contentArea")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        cv = QVBoxLayout(content)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)

        self.pages = widgets.FadeStack()
        cv.addWidget(self.pages)

        self.home_page = HomePage(settings_manager)
        self.history_page = HistoryPage()
        self.filejob_page = FileJobPage(engine, engine_status, settings_manager=settings_manager)
        self.settings_page = SettingsPage(settings_manager)
        self.settings_page.history_cleared.connect(self.history_page.refresh)
        self.settings_page.quit_requested.connect(self.quit_requested.emit)
        self.settings_page.filejob_requested.connect(self.show_filejob)
        self.home_page.restart_requested.connect(self.restart_requested.emit)

        # File transcription lives in its own small dialog, opened from Configuration.
        self.filejob_dialog = QDialog(self)
        self.filejob_dialog.setWindowTitle(strings.FILEJOB_WINDOW_TITLE)
        self.filejob_dialog.resize(560, 520)
        fv = QVBoxLayout(self.filejob_dialog)
        fv.setContentsMargins(0, 8, 0, 0)
        fv.addWidget(self.filejob_page)

        self._settings_frame = _PageFrame(strings.SETTINGS_SECTION_DICTATION, strings.SETTINGS_SUBTITLE_DICTATION, self.settings_page)
        self._page_index = {}
        for key, frame in (
            ("home", _PageFrame(strings.NAV_HOME, strings.PAGE_HOME_SUBTITLE, self.home_page)),
            ("settings", self._settings_frame),
            ("history", _PageFrame(strings.NAV_HISTORY, strings.PAGE_HISTORY_SUBTITLE, self.history_page)),
        ):
            self._page_index[key] = self.pages.count()
            self.pages.addWidget(frame)

        self.nav.navigated.connect(self.navigate)
        self.nav.select("home", emit=False)

        body_layout.addWidget(self.sidebar)
        body_layout.addWidget(content, 1)
        self.setCentralWidget(central)

        self._apply_theme()
        engine_status.status_changed.connect(self._on_engine_status_changed)

    def show_filejob(self) -> None:
        self.filejob_dialog.show()
        self.filejob_dialog.raise_()
        self.filejob_dialog.activateWindow()

    def restyle(self) -> None:
        """Re-apply the stylesheet after a theme input changed (e.g. the interface font)."""
        self._apply_theme()
        for widget in self.findChildren(QWidget):
            widget.updateGeometry()

    def _apply_theme(self) -> None:
        self.setStyleSheet("")
        self.setStyleSheet(theme.stylesheet())
        self.nav.restyle()

    # ---- navigation / status -----------------------------------------------

    _SECTION_TITLES = {
        "dictation": (strings.SETTINGS_SECTION_DICTATION, strings.SETTINGS_SUBTITLE_DICTATION),
        "text": (strings.SETTINGS_SECTION_TEXT, strings.SETTINGS_SUBTITLE_TEXT),
        "app": (strings.SETTINGS_SECTION_APP, strings.SETTINGS_SUBTITLE_APP),
    }

    def navigate(self, key: str) -> None:
        """Show a page: "home", "history" or "settings.<dictation|text|app>"."""
        if self.nav.current != key:
            self.nav.select(key, emit=False)
        top, _, section = key.partition(".")
        if top == "settings":
            section = section or "dictation"
            self._settings_frame.set_titles(*self._SECTION_TITLES[section])
            self.settings_page.show_section(section)
            if section == "app":
                self.settings_page.refresh_history_count()
        self.pages.setCurrentIndex(self._page_index[top])
        if top == "home":
            self.home_page.refresh(count_up=True)
        elif top == "history":
            self.history_page.refresh()

    def _on_engine_status_changed(self, state: str, device: str, stack_path: str) -> None:
        self.status_chip.setText(strings.format_engine_status(state, device))
        self.status_dot.set_state(state)

    def closeEvent(self, event) -> None:  # noqa: N802 -- Qt override naming
        # Closing the window just hides it: dictation, the notch island and
        # the API server keep running (see ui/app.py).
        event.ignore()
        self.hide()

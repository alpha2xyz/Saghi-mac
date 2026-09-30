"""
Main window, laid out like current macOS apps (Finder, System Settings):
a full-height sidebar -- app name, the three pages (History, File
transcription, Settings) with line icons, and the engine-status dot at the
bottom -- next to the page content, each page under a large title.

On macOS the title bar is merged into the window and the sidebar is real
system glass (see macos_glass.py); anywhere else, or if that fails, the
window keeps a normal title bar and a solid sidebar.

RTL note: the app sets `QApplication.setLayoutDirection(RightToLeft)` once,
globally, in ui/app.py. Qt then automatically mirrors every QHBoxLayout it
lays out from that point on -- this file builds the body in plain
"sidebar first, then content" order and Qt places the sidebar on the
visual right, exactly as README_AR.md's Arabic-first UI describes, with no
manual left/right juggling needed here.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QPixmap
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

from .. import __version__
from . import macos_glass, strings, theme, widgets
from .engine_status import EngineStatusBridge
from .filejob_page import FileJobPage
from .history_page import HistoryPage
from .settings_page import SettingsPage

_SIDEBAR_WIDTH = 220


class _PageFrame(QWidget):
    """A page under its large title + subtitle."""

    def __init__(self, title: str, subtitle: str, page: QWidget, top_inset: int) -> None:
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(widgets.DragArea(top_inset))
        head = QWidget()
        hv = QVBoxLayout(head)
        hv.setContentsMargins(28, 4, 28, 12)
        hv.setSpacing(2)
        hv.addWidget(widgets.label(title, "pageTitle"))
        if subtitle:
            hv.addWidget(widgets.label(subtitle, "pageSubtitle"))
        v.addWidget(head)
        v.addWidget(page, 1)


class MainWindow(QMainWindow):
    def changeEvent(self, event):  # noqa: N802 (Qt API name)
        # macOS light/dark (or accent colour) switch while the app is open:
        # rebuild the stylesheet and the tinted icons so nothing keeps the
        # old colours.
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            # (Can fire while __init__ is still building the widgets.)
            if hasattr(self, "_nav_icons") and not getattr(self, "_restyling", False):
                self._restyling = True
                try:
                    self._apply_theme()
                finally:
                    self._restyling = False
        super().changeEvent(event)

    def __init__(self, settings_manager, engine, engine_status: EngineStatusBridge, parent=None):
        super().__init__(parent)
        self.setWindowTitle(strings.APP_TITLE)
        self.resize(1080, 720)
        self.setMinimumSize(860, 560)

        # Decided before the native window exists (see macos_glass.py).
        self._wants_glass = macos_glass.wants_glass(settings_manager.current.glass_effect)
        if self._wants_glass:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._glass = False
        self._titlebar_merged = macos_glass.platform_ok()
        top_inset = macos_glass.TITLEBAR_INSET + 8 if self._titlebar_merged else 16

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
        side.setContentsMargins(0, 0, 0, 14)
        side.setSpacing(0)
        side.addWidget(widgets.DragArea(top_inset))

        brand = QWidget()
        bh = QHBoxLayout(brand)
        bh.setContentsMargins(20, 0, 20, 14)
        bh.setSpacing(10)
        self._logo = QLabel()
        from .tray import _ICON_PATH

        logo_px = QPixmap(str(_ICON_PATH))
        if not logo_px.isNull():
            logo_px.setDevicePixelRatio(2.0)
            self._logo.setPixmap(logo_px.scaled(56, 56, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        bh.addWidget(self._logo)
        bh.addWidget(widgets.label(strings.APP_TITLE, "appName"))
        bh.addStretch()
        side.addWidget(brand)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side.addWidget(self.nav, 1)

        status_row = QWidget()
        sh = QHBoxLayout(status_row)
        sh.setContentsMargins(22, 6, 22, 2)
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
        version.setContentsMargins(22, 2, 22, 0)
        side.addWidget(version)

        # ---- pages -------------------------------------------------------------
        content = QWidget()
        content.setObjectName("contentArea")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        cv = QVBoxLayout(content)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)

        self.pages = QStackedWidget()
        cv.addWidget(self.pages)

        self.history_page = HistoryPage()
        self.filejob_page = FileJobPage(engine, engine_status, settings_manager=settings_manager)
        self.settings_page = SettingsPage(settings_manager)
        self.settings_page.history_cleared.connect(self.history_page.refresh)

        self._nav_icons = ("history", "filejob", "settings")
        for label, subtitle, widget in (
            (strings.NAV_HISTORY, strings.PAGE_HISTORY_SUBTITLE, self.history_page),
            (strings.NAV_FILEJOB, strings.PAGE_FILEJOB_SUBTITLE, self.filejob_page),
            (strings.NAV_SETTINGS, strings.PAGE_SETTINGS_SUBTITLE, self.settings_page),
        ):
            QListWidgetItem(label, self.nav)
            self.pages.addWidget(_PageFrame(label, subtitle, widget, top_inset))

        self.nav.currentRowChanged.connect(self._on_nav_changed)
        self.nav.setCurrentRow(0)

        body_layout.addWidget(self.sidebar)
        body_layout.addWidget(content, 1)
        self.setCentralWidget(central)

        self._apply_theme()
        engine_status.status_changed.connect(self._on_engine_status_changed)

    # ---- native chrome -------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 -- Qt override
        super().showEvent(event)
        if not getattr(self, "_chrome_done", False):
            self._chrome_done = True
            if self._titlebar_merged:
                macos_glass.merge_titlebar(self)
            if self._wants_glass:
                self._glass = macos_glass.install_glass(self)
                self._apply_theme()

    def restyle(self) -> None:
        """Re-apply the stylesheet after a theme input changed (e.g. the interface font)."""
        self._apply_theme()
        for widget in self.findChildren(QWidget):
            widget.updateGeometry()

    def _apply_theme(self) -> None:
        self.setStyleSheet("")
        self.setStyleSheet(theme.stylesheet(glass=self._glass))
        for row, name in enumerate(self._nav_icons):
            item = self.nav.item(row)
            if item is not None:
                item.setIcon(widgets.icon(name))

    # ---- navigation / status -----------------------------------------------

    def _on_nav_changed(self, row: int) -> None:
        self.pages.setCurrentIndex(row)
        if row == 0:
            self.history_page.refresh()
        elif row == 2:
            self.settings_page.refresh_history_count()

    def _on_engine_status_changed(self, state: str, device: str, stack_path: str) -> None:
        self.status_chip.setText(strings.format_engine_status(state, device))
        self.status_dot.set_state(state)

    def closeEvent(self, event) -> None:  # noqa: N802 -- Qt override naming
        # Tray app: closing the window just hides it. The process, tray
        # icon, and API server thread all keep running in the background
        # -- see ui/app.py / ui/tray.py -- matching README_AR.md's
        # documented tray behavior ("أيقونة دائمة بجانب الساعة").
        event.ignore()
        self.hide()

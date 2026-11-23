"""
The window's sidebar navigation, macOS style: Home, Settings (with a
sub-menu: Dictation / Text / App) and History.

Clicking Settings opens its sub-menu with a short height animation and
selects its first entry; going to another top entry folds it away again.
The arrow next to Settings turns as the sub-menu opens. Every entry is a
small painted row (icon + text) so selection, hover and RTL mirroring all
come from one place.

Signal `navigated(key)`: "home", "history", or "settings.<section>" with
section one of SettingsPage.SECTION_KEYS.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import QEasingCurve, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from . import strings, theme, widgets

_ROW_H = 32
_SUB_ROW_H = 28
_OPEN_MS = 240


class NavRow(QWidget):
    """One clickable sidebar row."""

    clicked = Signal()

    def __init__(self, key: str, text: str, icon_name: Optional[str], sub: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.key = key
        self.icon_name = icon_name
        self.sub = sub
        self._selected = False
        self._hover = False
        self.setFixedHeight(_SUB_ROW_H if sub else _ROW_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        h = QHBoxLayout(self)
        # sub entries are indented under their parent's text
        h.setContentsMargins(38 if sub else 12, 0, 10, 0)
        h.setSpacing(8)
        self.icon = QLabel()
        self.icon.setFixedSize(18, 18)
        if icon_name:
            h.addWidget(self.icon)
        self.label = QLabel(text)
        h.addWidget(self.label)
        h.addStretch()
        self.arrow: Optional["_Arrow"] = None
        self.restyle()

    def add_arrow(self) -> "_Arrow":
        self.arrow = _Arrow(self)
        self.layout().addWidget(self.arrow)
        return self.arrow

    @property
    def selected(self) -> bool:
        return self._selected

    def set_selected(self, on: bool) -> None:
        if on != self._selected:
            self._selected = on
            self.restyle()

    def restyle(self) -> None:
        t = theme.tokens()
        on = self._selected and not self.sub
        color = t.accent_text if on else t.text
        if self.sub and self._selected:
            color = t.accent
        weight = 600 if (self._selected and self.sub) else 400
        size = theme.fs(12 if self.sub else 13)
        self.label.setStyleSheet(f"color: {color}; font-size: {size}px; font-weight: {weight}; background: transparent;")
        if self.icon_name:
            px = widgets.icon_pixmap(self.icon_name, 18, QColor(color) if on else None)
            px.setDevicePixelRatio(2.0)
            self.icon.setPixmap(px)
        if self.arrow is not None:
            self.arrow.set_color(QColor(color))
        self.update()

    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        fill = None
        if self._selected and not self.sub:
            fill = QColor(t.accent)
        elif self._hover:
            fill = theme.qcolor(t.hover)
        if fill is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(8, 1, -8, -1), 8, 8)
        p.fillPath(path, fill)


class _Arrow(QWidget):
    """A small chevron that turns from pointing sideways (closed) to down (open)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self._angle = 0.0  # 0 = closed, 1 = open
        self._color = QColor("#888888")

    def set_open_amount(self, v: float) -> None:
        self._angle = v
        self.update()

    def set_color(self, c: QColor) -> None:
        self._color = c
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(6, 6)
        # closed points to the reading end (left in RTL), open points down
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        start = 180.0 if rtl else 0.0
        end = 90.0
        p.rotate(start + (end - start) * self._angle)
        pen = QPen(self._color, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        path = QPainterPath()
        path.moveTo(-1.5, -3.5)
        path.lineTo(2.0, 0.0)
        path.lineTo(-1.5, 3.5)
        p.drawPath(path)


class SidebarNav(QWidget):
    navigated = Signal(str)

    SETTINGS_SECTIONS = (
        ("dictation", strings.SETTINGS_SECTION_DICTATION),
        ("text", strings.SETTINGS_SECTION_TEXT),
        ("app", strings.SETTINGS_SECTION_APP),
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        self.rows: Dict[str, NavRow] = {}

        self.home = self._add(v, NavRow("home", strings.NAV_HOME, "home"))
        self.settings = self._add(v, NavRow("settings", strings.NAV_SETTINGS, "settings"))
        self._arrow = self.settings.add_arrow()

        self.submenu = QWidget()
        sv = QVBoxLayout(self.submenu)
        sv.setContentsMargins(0, 0, 0, 2)
        sv.setSpacing(0)
        self.sub_rows: List[NavRow] = []
        for key, text in self.SETTINGS_SECTIONS:
            row = NavRow(f"settings.{key}", text, None, sub=True)
            row.clicked.connect(lambda k=row.key: self.select(k))
            sv.addWidget(row)
            self.sub_rows.append(row)
            self.rows[row.key] = row
        self._sub_full = len(self.sub_rows) * _SUB_ROW_H + 2
        self.submenu.setMaximumHeight(0)
        v.addWidget(self.submenu)

        self.history = self._add(v, NavRow("history", strings.NAV_HISTORY, "history"))
        v.addStretch()

        self.home.clicked.connect(lambda: self.select("home"))
        self.history.clicked.connect(lambda: self.select("history"))
        self.settings.clicked.connect(self._on_settings_clicked)

        self._open = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_open_amount)
        self.current = ""

    def _add(self, layout, row: NavRow) -> NavRow:
        layout.addWidget(row)
        self.rows[row.key] = row
        return row

    # ---- sub-menu --------------------------------------------------------
    @property
    def submenu_open(self) -> bool:
        return self._anim.endValue() == 1.0 if self._anim.state() == self._anim.State.Running else self._open >= 0.5

    def _set_open_amount(self, v) -> None:
        self._open = float(v)
        self.submenu.setMaximumHeight(int(round(self._sub_full * self._open)))
        self._arrow.set_open_amount(self._open)

    def set_submenu_open(self, on: bool, animate: bool = True) -> None:
        target = 1.0 if on else 0.0
        self._anim.stop()
        if not animate or not self.isVisible():
            self._set_open_amount(target)
            return
        self._anim.setStartValue(self._open)
        self._anim.setEndValue(target)
        self._anim.setDuration(int(_OPEN_MS * max(0.3, abs(target - self._open))))
        self._anim.start()

    def _on_settings_clicked(self) -> None:
        if self.current.startswith("settings."):
            # already in Settings: the arrow folds or unfolds the list, the page stays
            self.set_submenu_open(not self.submenu_open)
            return
        self.select("settings.dictation")

    # ---- selection ---------------------------------------------------------
    def select(self, key: str, emit: bool = True) -> None:
        if key == "settings":
            key = "settings.dictation"
        in_settings = key.startswith("settings.")
        for k, row in self.rows.items():
            row.set_selected(k == key or (k == "settings" and in_settings))
        self.set_submenu_open(in_settings)
        changed = key != self.current
        self.current = key
        if emit and changed:
            self.navigated.emit(key)

    def restyle(self) -> None:
        for row in self.rows.values():
            row.restyle()

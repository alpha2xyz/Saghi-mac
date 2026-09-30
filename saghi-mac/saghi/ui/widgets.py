"""
Small reusable pieces of the macOS-style design (see theme.py):

  ToggleSwitch   a macOS switch. Subclasses QCheckBox, so isChecked(),
                 setChecked() and the toggled signal behave exactly like
                 the checkboxes it replaces.
  SettingsCard   a rounded group of rows (title + optional hint on one
                 side, the control on the other, a hairline between rows),
                 like System Settings.
  ColorDot       a round colour swatch button (waveform colour presets).
  StatusDot      the little coloured engine-status dot.
  DragArea       an empty strip that moves the window when dragged (the
                 title bar is merged into the content on macOS, see
                 macos_glass.py).
  icon(name)     simple line icons drawn with QPainter, tinted per theme.

RTL: every row is a QHBoxLayout, which Qt mirrors automatically under the
app-wide RightToLeft direction (ui/app.py) -- titles land on the right and
controls on the left without any manual juggling. ToggleSwitch mirrors its
knob the same way (on = knob toward the leading edge's opposite side, as
macOS does in Arabic).
"""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import strings, theme


# ---- switch -----------------------------------------------------------------


class ToggleSwitch(QCheckBox):
    _W = 38
    _H = 22

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(self._W, self._H)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"knobPosition", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:  # noqa: N802 -- Qt override
        return QSize(self._W, self._H)

    def hitButton(self, pos) -> bool:  # noqa: N802 -- Qt override
        return self.rect().contains(pos)

    def setChecked(self, checked: bool) -> None:  # noqa: N802 -- Qt API name
        super().setChecked(checked)
        # Programmatic set (initial state): jump, don't animate.
        self._anim.stop()
        self._pos = 1.0 if checked else 0.0
        self.update()

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def _get_pos(self) -> float:
        return self._pos

    def _set_pos(self, value: float) -> None:
        self._pos = value
        self.update()

    knobPosition = Property(float, _get_pos, _set_pos)

    def paintEvent(self, event) -> None:  # noqa: N802 -- Qt override
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        track = QRectF(0.5, 0.5, self._W - 1, self._H - 1)
        off = QColor("#3A3A3C") if t.dark else QColor("#E3E3E8")
        on = QColor(t.accent)
        mix = self._pos
        color = QColor(
            int(off.red() + (on.red() - off.red()) * mix),
            int(off.green() + (on.green() - off.green()) * mix),
            int(off.blue() + (on.blue() - off.blue()) * mix),
        )
        if not self.isEnabled():
            color.setAlpha(110)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(track, track.height() / 2, track.height() / 2)

        margin = 2.0
        d = self._H - 2 * margin
        travel = self._W - 2 * margin - d
        frac = (1.0 - self._pos) if self.isRightToLeft() else self._pos
        x = margin + travel * frac
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawEllipse(QRectF(x, margin + 0.8, d, d))
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(x, margin, d, d))
        p.end()


# ---- cards ---------------------------------------------------------------------


def label(text: str = "", object_name: str = "", wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if object_name:
        lbl.setObjectName(object_name)
    if wrap:
        lbl.setWordWrap(True)
    return lbl


def separator() -> QFrame:
    line = QFrame()
    line.setObjectName("separator")
    line.setFixedHeight(1)
    return line


class SettingsCard(QFrame):
    """A rounded card of rows. Use add_row() for 'title/hint ... control' rows, add_widget() for anything else."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 4, 16, 4)
        self._layout.setSpacing(0)
        self._rows = 0

    def _add_separator_if_needed(self) -> None:
        if self._rows:
            self._layout.addWidget(separator())
        self._rows += 1

    def add_row(self, title: str, control: Optional[QWidget] = None, hint: str = "") -> QWidget:
        self._add_separator_if_needed()
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 10, 0, 10)
        h.setSpacing(16)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(label(title, "rowTitle"))
        if hint:
            texts.addWidget(label(hint, "rowHint", wrap=True))
        h.addLayout(texts, 1)
        if control is not None:
            h.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        self._layout.addWidget(row)
        return row

    def add_widget(self, widget: QWidget, margins: tuple = (0, 10, 0, 10)) -> QWidget:
        self._add_separator_if_needed()
        wrapper = QWidget()
        v = QVBoxLayout(wrapper)
        v.setContentsMargins(*margins)
        v.addWidget(widget)
        self._layout.addWidget(wrapper)
        return wrapper


def section(title: str, card: QWidget) -> QWidget:
    """A section title above a card, like System Settings' grouped lists."""
    box = QWidget()
    v = QVBoxLayout(box)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(6)
    v.addWidget(label(title, "sectionTitle"))
    v.addWidget(card)
    return box


def hbox(*widgets, stretch_end: bool = True, spacing: int = 8) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(spacing)
    for item in widgets:
        h.addWidget(item)
    if stretch_end:
        h.addStretch()
    return w


# ---- small visual pieces ----------------------------------------------------------


class ColorDot(QAbstractButton):
    """A round, checkable colour swatch."""

    def __init__(self, color: str, parent=None) -> None:
        super().__init__(parent)
        self.color = color
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(24, 24)
        self.setToolTip(color)

    def set_color(self, color: str) -> None:
        self.color = color
        self.setToolTip(color)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 -- Qt override
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        p.setPen(QPen(QColor(0, 0, 0, 40), 1))
        p.setBrush(QColor(self.color))
        p.drawEllipse(r)
        if self.isChecked():
            ring = QPen(QColor(self.color), 2)
            p.setPen(ring)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#FFFFFF"))
            p.drawEllipse(r.center(), 3, 3)
        p.end()


class StatusDot(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self._color = QColor(theme.STATUS_COLORS["cold"])

    def set_state(self, state: str) -> None:
        self._color = QColor(theme.STATUS_COLORS.get(state, theme.STATUS_COLORS["cold"]))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 -- Qt override
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(QRectF(1, 1, 8, 8))
        p.end()


class DragArea(QWidget):
    """Moves the window when dragged -- stands in for the title bar merged away on macOS."""

    double_clicked = Signal()

    def __init__(self, height: int, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def mousePressEvent(self, event) -> None:  # noqa: N802 -- Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 -- Qt override
        # macOS convention: double-clicking the title bar zooms the window.
        win = self.window()
        win.showNormal() if win.isMaximized() else win.showMaximized()
        super().mouseDoubleClickEvent(event)


# ---- line icons ----------------------------------------------------------------------


def _pen(color: QColor, width: float = 1.6) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _draw_clock(p: QPainter, s: float, c: QColor) -> None:
    p.setPen(_pen(c, s * 0.085))
    r = s * 0.38
    center = QPointF(s / 2, s / 2)
    p.drawEllipse(center, r, r)
    p.drawLine(center, QPointF(s / 2, s / 2 - r * 0.6))
    p.drawLine(center, QPointF(s / 2 + r * 0.45, s / 2 + r * 0.2))


def _draw_wave(p: QPainter, s: float, c: QColor) -> None:
    p.setPen(_pen(c, s * 0.09))
    heights = (0.18, 0.4, 0.62, 0.34, 0.5, 0.22)
    step = s * 0.13
    x0 = s / 2 - step * (len(heights) - 1) / 2
    for i, h in enumerate(heights):
        x = x0 + i * step
        p.drawLine(QPointF(x, s / 2 - s * h / 2), QPointF(x, s / 2 + s * h / 2))


def _draw_gear(p: QPainter, s: float, c: QColor) -> None:
    center = QPointF(s / 2, s / 2)
    outer, inner, teeth = s * 0.42, s * 0.31, 8
    path = QPainterPath()
    for i in range(teeth * 2):
        # alternate outer/inner radius with a flat top on each tooth
        a0 = (i / (teeth * 2)) * 2 * math.pi
        a1 = ((i + 1) / (teeth * 2)) * 2 * math.pi
        r = outer if i % 2 == 0 else inner
        for a in (a0, a1):
            pt = QPointF(center.x() + r * math.cos(a), center.y() + r * math.sin(a))
            if path.elementCount() == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
    path.closeSubpath()
    p.setPen(_pen(c, s * 0.075))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.drawEllipse(center, s * 0.12, s * 0.12)


def _draw_tray(p: QPainter, s: float, c: QColor) -> None:
    p.setPen(_pen(c, s * 0.06))
    # arrow down
    p.drawLine(QPointF(s / 2, s * 0.14), QPointF(s / 2, s * 0.58))
    p.drawLine(QPointF(s * 0.34, s * 0.43), QPointF(s / 2, s * 0.59))
    p.drawLine(QPointF(s * 0.66, s * 0.43), QPointF(s / 2, s * 0.59))
    # open tray
    path = QPainterPath(QPointF(s * 0.16, s * 0.6))
    path.lineTo(QPointF(s * 0.16, s * 0.82))
    path.lineTo(QPointF(s * 0.84, s * 0.82))
    path.lineTo(QPointF(s * 0.84, s * 0.6))
    p.drawPath(path)


def _draw_doc(p: QPainter, s: float, c: QColor) -> None:
    p.setPen(_pen(c, s * 0.06))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(s * 0.24, s * 0.14, s * 0.52, s * 0.72), s * 0.08, s * 0.08)
    for y in (0.36, 0.5, 0.64):
        p.drawLine(QPointF(s * 0.34, s * y), QPointF(s * 0.66, s * y))


_ICONS = {
    "history": _draw_clock,
    "filejob": _draw_wave,
    "settings": _draw_gear,
    "drop": _draw_tray,
    "empty": _draw_doc,
}


def icon_pixmap(name: str, size: int, color: Optional[QColor] = None, device_pixel_ratio: float = 2.0) -> QPixmap:
    color = color or QColor(theme.tokens().text)
    px = QPixmap(int(size * device_pixel_ratio), int(size * device_pixel_ratio))
    px.setDevicePixelRatio(device_pixel_ratio)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    _ICONS[name](p, float(size), color)
    p.end()
    return px


def icon(name: str, size: int = 18) -> QIcon:
    """Normal-state icon in the text colour, selected-state icon in white (sits on the accent capsule)."""
    ic = QIcon()
    ic.addPixmap(icon_pixmap(name, size), QIcon.Mode.Normal)
    ic.addPixmap(icon_pixmap(name, size, QColor(theme.tokens().accent_text)), QIcon.Mode.Selected)
    return ic


def confirm_destructive(parent, title: str, text: str) -> bool:
    """A macOS-style 'are you sure?' sheet with a red Delete button. Returns True to go ahead."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(title)
    box.setInformativeText(text)
    delete_btn = box.addButton(strings.DELETE, QMessageBox.ButtonRole.DestructiveRole)
    cancel_btn = box.addButton(strings.CANCEL, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel_btn)
    box.exec()
    return box.clickedButton() is delete_btn

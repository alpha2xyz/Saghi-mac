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
  icon(name)     SF Symbol icons (see sf_symbols.py), tinted per theme.

RTL: every row is a QHBoxLayout, which Qt mirrors automatically under the
app-wide RightToLeft direction (ui/app.py) -- titles land on the right and
controls on the left without any manual juggling. ToggleSwitch mirrors its
knob the same way (on = knob toward the leading edge's opposite side, as
macOS does in Arabic).
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
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

from . import sf_symbols, strings, theme


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


# ---- icons ---------------------------------------------------------------------------
# The drawing (native SF Symbols on macOS, QPainter look-alikes elsewhere) lives in
# sf_symbols.py. This section keeps the short names the pages were written with
# working; any SF Symbol name passes straight through.

_LEGACY_ICON_NAMES = {
    "history": "clock",
    "filejob": "waveform",
    "settings": "gearshape",
    "drop": "square.and.arrow.down",
    "empty": "doc.text",
}


def icon_pixmap(name: str, size: int, color: Optional[QColor] = None, device_pixel_ratio: float = 2.0) -> QPixmap:
    return sf_symbols.pixmap(_LEGACY_ICON_NAMES.get(name, name), size, color, dpr=device_pixel_ratio)


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

"""
Floating recording indicator (README_AR.md: "مؤشر صغير جدًا أسفل الشاشة
بلا ظل، مع حد مضيء بلون تختاره" / "موجة حقيقية تتبع مستوى الميكروفون ولا
تتحرك اصطناعيًا عند الصمت").

A frameless, always-on-top, non-activating small window (~260x56, bottom-
center of the primary screen), rounded rect with a glowing border in
`settings.waveform_color`. Three states:

  RECORDING  -- live waveform, ~30fps, driven by polling a caller-supplied
                `level_fn() -> (recent_samples: np.ndarray, rms: float)`
                (the exact shape of `Recorder.level()`, see recorder.py --
                this module never imports Recorder directly, so it can be
                driven by a fake in tests). Rendered in one of four styles
                (bars/line/dots/pulse, per `settings.VALID_WAVEFORM_STYLES`)
                -- ALL FOUR are a pure function of the current audio level,
                with NO time-based motion term, so real silence renders as
                a genuinely flat/minimal shape and never appears to move on
                its own (this is a hard behavior-contract requirement, not
                a style choice -- verified by
                `dev/test_indicator_render.py`'s "two renders of the same
                all-zero buffer are byte-identical" check, once per style).
  PROCESSING -- the waveform stops; a short status text ("جارٍ المعالجة…"
                by default, or a caller-supplied override e.g. "جارٍ تحميل
                النموذج…" for a cold-engine first run) plus a subtle
                indeterminate three-dot animation. This is the ONE place a
                time-based animation is allowed -- there's no "silence"
                concept to violate once recording has stopped.
  ERROR      -- a short error message, auto-hides after a few seconds.

CRITICAL: this window must NEVER steal keyboard focus from whatever app the
user is dictating into -- `paste.py`'s Cmd+V synthesis targets the
frontmost app, so if showing this indicator itself changed which app is
frontmost, autopaste would land in the wrong place. `Qt.WindowDoesNotAcceptFocus`
+ `WA_ShowWithoutActivating` are both set for this reason; callers must
never call `raise_()`/`activateWindow()`/`setFocus()` on this widget (there
is deliberately no code path here that does). This specific property is
NOT verifiable under an offscreen Qt platform -- it needs a manual check
("does TextEdit's caret stay put when the
indicator appears").
"""

from __future__ import annotations

import math
from typing import Callable, Optional, Tuple

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..settings import VALID_WAVEFORM_STYLES
from . import strings

STATE_HIDDEN = "hidden"
STATE_RECORDING = "recording"
STATE_PROCESSING = "processing"
STATE_ERROR = "error"

_WIDTH = 260
_HEIGHT = 56
_BOTTOM_MARGIN = 28  # gap between the indicator's bottom edge and the screen's bottom edge
_BIN_COUNT = 28
# Amplitude that maps to a "full-height" bar/dot/line-peak/pulse-radius.
# Tuned conservatively against dev/probe_devices.py's real quiet-room
# capture (~0.0075 peak) so ordinary speech (much louder than room tone)
# visibly fills the indicator without clipping flat at 1.0 immediately;
# a packaging-time pass on real hardware may want to revisit this.
_LEVEL_REFERENCE = 0.18
_TICK_MS = 33  # ~30fps, per the task spec

LevelFn = Callable[[], Tuple[np.ndarray, float]]


def _compute_bins(samples: np.ndarray, n_bins: int = _BIN_COUNT, reference: float = _LEVEL_REFERENCE) -> np.ndarray:
    """
    Deterministic: the same `samples` array always produces the same bins
    array -- an all-zero (or empty) input always produces all-zero bins, no
    randomness/state anywhere in this function. That determinism is what
    lets dev/test_indicator_render.py assert byte-identical repeated
    renders of silence.
    """
    if samples.size == 0:
        return np.zeros(n_bins, dtype=np.float32)
    chunks = np.array_split(samples, n_bins)
    levels = np.array(
        [float(np.sqrt(np.mean(c.astype(np.float64) ** 2))) if c.size else 0.0 for c in chunks],
        dtype=np.float32,
    )
    return np.clip(levels / reference, 0.0, 1.0)


def _draw_bars(painter: QPainter, rect: QRectF, bins: np.ndarray, color: QColor) -> None:
    n = len(bins)
    if n == 0:
        return
    gap = 3.0
    bar_w = max(2.0, (rect.width() - gap * (n - 1)) / n)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    cy = rect.center().y()
    for i, v in enumerate(bins):
        h = max(1.5, float(v) * (rect.height() / 2))
        x = rect.left() + i * (bar_w + gap)
        painter.drawRoundedRect(QRectF(x, cy - h, bar_w, h * 2), 1.5, 1.5)


def _draw_line(painter: QPainter, rect: QRectF, bins: np.ndarray, color: QColor) -> None:
    n = len(bins)
    if n == 0:
        return
    pen = QPen(color)
    pen.setWidthF(2.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    cy = rect.center().y()
    step = rect.width() / max(1, n - 1)
    path = QPainterPath()
    for i, v in enumerate(bins):
        x = rect.left() + i * step
        y = cy - float(v) * (rect.height() / 2)
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    painter.drawPath(path)


def _draw_dots(painter: QPainter, rect: QRectF, bins: np.ndarray, color: QColor) -> None:
    n = len(bins)
    if n == 0:
        return
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    cy = rect.center().y()
    step = rect.width() / max(1, n - 1)
    r_min, r_max = 2.0, 5.0
    for i, v in enumerate(bins):
        x = rect.left() + i * step
        y = cy - float(v) * (rect.height() / 2)
        r = r_min + float(v) * (r_max - r_min)
        painter.drawEllipse(QPointF(x, y), r, r)


def _draw_pulse(painter: QPainter, rect: QRectF, bins: np.ndarray, color: QColor) -> None:
    # Purely a function of the current level (max of the bins array) --
    # deliberately NO time/sin(t) breathing term, which is what would make
    # this style "move artificially during silence" (an earlier, rejected
    # design). See module docstring / dev/test_indicator_render.py.
    level = float(np.max(bins)) if len(bins) else 0.0
    cx, cy = rect.center().x(), rect.center().y()
    base_r = 6.0
    max_extra = max(0.0, min(rect.width(), rect.height()) / 2 - base_r)
    r = base_r + level * max_extra

    glow = QColor(color)
    glow.setAlpha(70)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(glow)
    painter.drawEllipse(QPointF(cx, cy), r * 1.6, r * 1.6)
    painter.setBrush(color)
    painter.drawEllipse(QPointF(cx, cy), r, r)


_STYLE_DRAWERS = {
    "bars": _draw_bars,
    "line": _draw_line,
    "dots": _draw_dots,
    "pulse": _draw_pulse,
}


class IndicatorWindow(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(_WIDTH, _HEIGHT)

        self._style = "bars"
        self._color = QColor("#4A90D9")
        self._state = STATE_HIDDEN
        self._level_fn: Optional[LevelFn] = None
        self._processing_text = strings.INDICATOR_PROCESSING
        self._error_text = ""
        self._anim_t = 0.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)

        self._error_hide_timer = QTimer(self)
        self._error_hide_timer.setSingleShot(True)
        self._error_hide_timer.timeout.connect(self.hide_indicator)

    # ---- configuration (settings.py's waveform_style / waveform_color) ----

    def set_style(self, style: str) -> None:
        if style in VALID_WAVEFORM_STYLES:
            self._style = style
            self.update()

    def set_color(self, hex_color: str) -> None:
        color = QColor(hex_color)
        if color.isValid():
            self._color = color
            self.update()

    @property
    def state(self) -> str:
        return self._state

    # ---- state transitions -------------------------------------------------

    def show_recording(self, level_fn: LevelFn) -> None:
        self._level_fn = level_fn
        self._state = STATE_RECORDING
        self._error_hide_timer.stop()
        self._position()
        self.show()
        self._timer.start(_TICK_MS)

    def show_processing(self, text: Optional[str] = None) -> None:
        self._level_fn = None
        self._state = STATE_PROCESSING
        self._processing_text = text or strings.INDICATOR_PROCESSING
        self._anim_t = 0.0
        self._error_hide_timer.stop()
        self._position()
        self.show()
        self._timer.start(_TICK_MS)

    def show_error(self, message: str, auto_hide_ms: int = 2500) -> None:
        self._level_fn = None
        self._state = STATE_ERROR
        self._error_text = message
        self._timer.stop()
        self._position()
        self.show()
        self.update()
        self._error_hide_timer.start(auto_hide_ms)

    def hide_indicator(self) -> None:
        self._timer.stop()
        self._error_hide_timer.stop()
        self._level_fn = None
        self._state = STATE_HIDDEN
        self.hide()

    # ---- positioning --------------------------------------------------------

    def _position(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = geo.x() + (geo.width() - _WIDTH) // 2
        y = geo.y() + geo.height() - _HEIGHT - _BOTTOM_MARGIN
        self.move(x, y)

    # ---- ticking / painting --------------------------------------------------

    def _on_tick(self) -> None:
        self._anim_t += _TICK_MS / 1000.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 -- Qt override naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            self._paint_background(painter)
            if self._state == STATE_RECORDING:
                self._paint_waveform(painter)
            elif self._state == STATE_PROCESSING:
                self._paint_processing(painter)
            elif self._state == STATE_ERROR:
                self._paint_error(painter)
        finally:
            painter.end()

    def _paint_background(self, painter: QPainter) -> None:
        # A glass capsule in the macOS 26/27 style: a dark, slightly
        # see-through pill, a hairline highlight along its top edge (the
        # "lit glass" rim), and the waveform colour as a soft glowing
        # border. All deterministic strokes -- no blur filter or drop
        # shadow (README_AR.md's "بلا ظل").
        rect = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        radius = rect.height() / 2

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(22, 22, 26, 218))
        painter.drawRoundedRect(rect, radius, radius)

        for inset, alpha in ((4.0, 30), (3.0, 55), (2.0, 90), (0.5, 170)):
            pen = QPen(QColor(self._color.red(), self._color.green(), self._color.blue(), alpha))
            pen.setWidthF(1.2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(-inset, -inset, inset, inset), radius + inset, radius + inset)

        rim = QPen(QColor(255, 255, 255, 46))
        rim.setWidthF(1.0)
        painter.setPen(rim)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        top = QPainterPath()
        inner = rect.adjusted(1.5, 1.5, -1.5, -1.5)
        r = inner.height() / 2
        top.moveTo(inner.left() + r, inner.top())
        top.lineTo(inner.right() - r, inner.top())
        painter.drawPath(top)

    def _paint_waveform(self, painter: QPainter) -> None:
        samples = np.zeros(0, dtype=np.float32)
        if self._level_fn is not None:
            try:
                samples, _rms = self._level_fn()
            except Exception:
                samples = np.zeros(0, dtype=np.float32)

        bins = _compute_bins(samples)
        area = QRectF(self.rect()).adjusted(22, 10, -22, -10)
        drawer = _STYLE_DRAWERS.get(self._style, _draw_bars)
        drawer(painter, area, bins, self._color)

    def _paint_processing(self, painter: QPainter) -> None:
        text_rect = QRectF(self.rect()).adjusted(16, 6, -16, -20)
        painter.setPen(QColor(225, 225, 230))
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignCenter), self._processing_text)

        # Subtle indeterminate three-dot animation -- the one place a
        # time-based term is allowed (see module docstring).
        cy = self.rect().bottom() - 14
        cx = self.rect().center().x()
        spacing = 14.0
        for i in range(3):
            phase = (self._anim_t * 2.2 + i * 0.6) % (2 * math.pi)
            alpha = int(90 + 120 * (0.5 + 0.5 * math.sin(phase)))
            dot_color = QColor(self._color)
            dot_color.setAlpha(alpha)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(dot_color)
            x = cx - spacing + i * spacing
            painter.drawEllipse(QPointF(x, cy), 3.5, 3.5)

    def _paint_error(self, painter: QPainter) -> None:
        text_rect = QRectF(self.rect()).adjusted(16, 6, -16, -6)
        painter.setPen(QColor(235, 120, 120))
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextWordWrap),
            self._error_text,
        )

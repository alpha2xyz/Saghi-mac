"""
Floating status pill (README_AR.md: "مؤشر صغير جدًا أسفل الشاشة بلا ظل، مع
حد مضيء بلون تختاره" / "موجة حقيقية تتبع مستوى الميكروفون ولا تتحرك
اصطناعيًا عند الصمت"), grown from the old recording-only indicator into a
small always-visible, draggable capsule that also tells the user whether
Saghi is ready.

A frameless, always-on-top, non-activating window that paints everything
itself (no child widgets), in a glass style: white glass in light mode, a
smoky dark glass in dark mode, a 1px rim and a hairline highlight along the
top. With `set_glass_enabled(True)` on macOS, real system glass (see
macos_panel.PillGlass) is put behind the Qt content and the Qt fill is
reduced to a light tint. No drop shadow (the spec says بلا ظل).

Looks, by `state`:

  hidden      IDLE. With idle mode "always" the window stays on screen as a
              COMPACT pill (36px high): `mic` + a green dot + "جاهز" when
              ready, an orange `hourglass` + "جارٍ تحميل النموذج" while the
              model loads, a dimmed `mic.slash` + "الإملاء متوقف" when
              dictation is switched off (`set_idle_status`). With idle mode
              "active" the window is hidden while idle, as the old
              indicator was.
  recording   EXPANDED pill (52px high, 264 wide): a red `mic.fill`, the live
              waveform in the configured style/colour, an elapsed "m:ss"
              timer, and a thin rim in the waveform colour. The waveform is
              driven by polling a caller-supplied
              `level_fn() -> (recent_samples: np.ndarray, rms: float)` (the
              exact shape of `Recorder.level()`; this module never imports
              Recorder, so tests can drive it with a fake) once per ~33ms
              tick, smoothed as `prev*0.7 + new*0.3` (as in the Handy app).
              All four styles (bars/line/dots/pulse) are a pure function of
              those smoothed bins with NO time-based motion term, so real
              silence renders as a genuinely flat shape and never appears to
              move on its own (a hard behavior-contract requirement, verified
              by `dev/test_indicator_render.py`'s "two renders of the same
              all-zero buffer are byte-identical" check, once per style; the
              only thing that changes over time is the timer's digits, once a
              second).
  processing  EXPANDED: a `waveform` icon, the status text ("جارٍ المعالجة…"
              or a caller-supplied override) and an indeterminate three-dot
              animation. This is the ONE place a time-based animation is
              allowed -- there is no "silence" concept once recording ended.
  error       EXPANDED: an orange `exclamationmark.triangle.fill` and a short
              message (fits its text up to ~420px, then elides); after
              `auto_hide_ms` it calls `hide_indicator()`, i.e. goes back to
              the idle look.

Layout is done by hand and mirrored when `isRightToLeft()` (the app is RTL:
the icon sits at the right end and the content flows leftward; the waveform
history flows leftward too).

POSITION: the pill has an ANCHOR, its global centre. The default anchor is
the bottom centre of the primary screen; the user can drag the pill anywhere
(the window is moved manually with move(), like whisper-writer does, not with
startSystemMove) and the release emits `anchor_moved(x, y)` so the caller can
persist it. Compact <-> expanded changes keep the centre. The whole window is
kept inside the availableGeometry of the screen that contains the anchor
(re-clamped when screens are added/removed or their work area changes), and
it is never placed at (0, 0). A press-and-release without moving more than a
few pixels is a CLICK: `open_requested` fires, but only while idle or showing
an error, so a stray click cannot pull the app forward mid-dictation. The
right-click menu offers open / placement (bottom or top of the screen) /
"hide while idle" (`mode_change_requested("active")`; the caller owns
settings and calls `set_idle_mode`).

NATIVE (macOS): `macos_panel.pin_floating(self)` runs after every show() and
on every WinIdChange, giving the window a floating level, "on every space /
over full-screen apps" collection behaviour and a non-activating panel
style; the native window is only created when Qt first shows it, so this
cannot happen in __init__.

CRITICAL: this window must NEVER steal keyboard focus from whatever app the
user is dictating into -- `paste.py`'s Cmd+V synthesis targets the
frontmost app, so if showing this indicator itself changed which app is
frontmost, autopaste would land in the wrong place. `Qt.WindowDoesNotAcceptFocus`
+ `WA_ShowWithoutActivating` are both set for this reason; callers must
never call `raise_()`/`activateWindow()`/`setFocus()` on this widget (there
is deliberately no code path here that does; the context menu is not opened
while recording/processing for the same reason). This specific property is
NOT verifiable under an offscreen Qt platform -- it needs a manual check
("does TextEdit's caret stay put when the indicator appears").
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

import numpy as np
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from ..settings import VALID_WAVEFORM_STYLES
from . import macos_panel, sf_symbols, strings, theme

logger = logging.getLogger("saghi.ui.indicator")

STATE_HIDDEN = "hidden"
STATE_RECORDING = "recording"
STATE_PROCESSING = "processing"
STATE_ERROR = "error"

IDLE_MODES = ("always", "active")
IDLE_STATUSES = ("ready", "loading", "disabled")

# Transparent border around the pill, for the rim / glow and antialiasing.
_MARGIN = 8

# (pill height, horizontal padding, gap, icon size)
_COMPACT = (36, 14, 8, 16)
_EXPANDED = (52, 18, 12, 18)
_RECORDING_W = 264
_PROCESSING_MIN_W = 220
_ERROR_MAX_W = 420
_DOTS_W = 25.0  # three 5px dots, 5px apart

# Default anchor: the pill's centre sits this far above the screen's bottom
# (28px gap under the expanded pill, whose half height is 26px).
_BOTTOM_GAP = 28
_TOP_GAP = 14
_DRAG_THRESHOLD = 4  # manhattanLength, in px

_BIN_COUNT = 28
# Amplitude that maps to a "full-height" bar/dot/line-peak/pulse-radius.
# Tuned conservatively against dev/probe_devices.py's real quiet-room
# capture (~0.0075 peak) so ordinary speech (much louder than room tone)
# visibly fills the indicator without clipping flat at 1.0 immediately;
# a packaging-time pass on real hardware may want to revisit this.
_LEVEL_REFERENCE = 0.18
_TICK_MS = 33  # ~30fps, per the task spec
_SMOOTH_KEEP = 0.7  # smoothed = prev * 0.7 + new * 0.3

_RED = QColor("#FF3B30")
_ORANGE = QColor("#FF9F0A")
_GREEN = QColor("#34C759")

# idle status -> (SF symbol, label)
_IDLE_LOOK = {
    "ready": ("mic", strings.PILL_READY),
    "loading": ("hourglass", strings.PILL_LOADING),
    "disabled": ("mic.slash", strings.PILL_DISABLED),
}

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

@dataclass(frozen=True)
class _Look:
    """Pill metrics for the current state, in logical px (window = pill + 2*_MARGIN)."""

    w: int
    h: int
    pad: int
    gap: int
    icon: int
    text: str = ""
    text_w: float = 0.0
    timer_w: float = 0.0


class IndicatorWindow(QWidget):
    state_changed = Signal(str)
    open_requested = Signal()
    anchor_moved = Signal(int, int)
    mode_change_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        # Plain state first: Qt may deliver events (palette/font changes)
        # while the window flags and attributes below are being set.
        self._style = "bars"
        self._color = QColor("#4A90D9")
        self._state = STATE_HIDDEN
        self._level_fn: Optional[LevelFn] = None
        self._processing_text = strings.INDICATOR_PROCESSING
        self._error_text = ""
        self._anim_t = 0.0
        self._idle_mode = "active"  # the controller applies settings.floating_pill_mode
        self._idle_status = "ready"
        self._bins = np.zeros(_BIN_COUNT, dtype=np.float32)
        self._clock: Callable[[], float] = time.monotonic  # replaceable in tests
        self._rec_start = 0.0
        self._timer_text = "0:00"
        self._anchor: Optional[Tuple[int, int]] = None  # None = default (bottom centre)
        self._dark = theme.is_dark()
        self._glass_enabled = False
        self._glass = None
        self._menu: Optional[QMenu] = None
        self._press_global: Optional[QPoint] = None
        self._press_center = (0, 0)
        self._dragging = False
        self._lay = self._measure()

        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # Keep the tool window on screen while another app is active.
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setToolTip(strings.PILL_TOOLTIP)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setFixedSize(self._lay.w + 2 * _MARGIN, self._lay.h + 2 * _MARGIN)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)

        self._error_hide_timer = QTimer(self)
        self._error_hide_timer.setSingleShot(True)
        self._error_hide_timer.timeout.connect(self.hide_indicator)

        app = QGuiApplication.instance()
        if app is not None:
            app.screenAdded.connect(self._on_screen_added)
            app.screenRemoved.connect(self._on_screens_changed)
            app.primaryScreenChanged.connect(self._on_screens_changed)
        for screen in QGuiApplication.screens():
            screen.availableGeometryChanged.connect(self._on_screens_changed)
        self._apply_geometry()

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

    def set_idle_mode(self, mode: str) -> None:
        """"always": stay on screen as the compact pill when idle; "active": hide when idle."""
        if mode not in IDLE_MODES:
            return
        self._idle_mode = mode
        if self._state == STATE_HIDDEN:
            self._apply_idle_visibility()

    def set_idle_status(self, status: str) -> None:
        """What the idle pill says: "ready", "loading" (model) or "disabled" (dictation off)."""
        if status not in IDLE_STATUSES:
            return
        self._idle_status = status
        if self._state == STATE_HIDDEN:
            self._relayout()

    def set_glass_enabled(self, enabled: bool) -> None:
        """Use real system glass behind the pill (settings.glass_effect); macOS only, no-op elsewhere."""
        self._glass_enabled = bool(enabled)
        self._sync_glass()
        self.update()

    @property
    def state(self) -> str:
        return self._state

    @property
    def idle_mode(self) -> str:
        return self._idle_mode

    @property
    def idle_status(self) -> str:
        return self._idle_status

    # ---- state transitions -------------------------------------------------

    def show_recording(self, level_fn: LevelFn) -> None:
        self._level_fn = level_fn
        self._error_hide_timer.stop()
        self._rec_start = self._clock()
        self._timer_text = self._elapsed_text()
        # One immediate poll, unsmoothed, so the first paint isn't empty.
        self._bins = self._read_bins()
        changed = self._enter(STATE_RECORDING)
        self._timer.start(_TICK_MS)
        if changed:
            self.state_changed.emit(STATE_RECORDING)

    def show_processing(self, text: Optional[str] = None) -> None:
        self._level_fn = None
        self._processing_text = text or strings.INDICATOR_PROCESSING
        self._anim_t = 0.0
        self._error_hide_timer.stop()
        changed = self._enter(STATE_PROCESSING)
        self._timer.start(_TICK_MS)
        if changed:
            self.state_changed.emit(STATE_PROCESSING)

    def show_error(self, message: str, auto_hide_ms: int = 2500) -> None:
        self._level_fn = None
        self._error_text = message
        self._timer.stop()
        changed = self._enter(STATE_ERROR)
        self._error_hide_timer.start(auto_hide_ms)
        if changed:
            self.state_changed.emit(STATE_ERROR)

    def hide_indicator(self) -> None:
        """Back to IDLE: the compact pill in mode "always", nothing on screen in mode "active"."""
        self._timer.stop()
        self._error_hide_timer.stop()
        self._level_fn = None
        self._bins = np.zeros(_BIN_COUNT, dtype=np.float32)
        changed = self._enter(STATE_HIDDEN)
        if changed:
            self.state_changed.emit(STATE_HIDDEN)

    def _enter(self, state: str) -> bool:
        """Switch state, resize, show/hide accordingly. Returns whether the state changed."""
        changed = state != self._state
        self._state = state
        self._relayout()
        if state == STATE_HIDDEN:
            self._apply_idle_visibility()
        else:
            self._show_pinned()
        self.update()
        return changed

    def _apply_idle_visibility(self) -> None:
        if self._idle_mode == "always":
            self._show_pinned()
        else:
            self.hide()

    def _show_pinned(self) -> None:
        self.show()
        self._pin()
        self._sync_glass()

    # ---- anchor / positioning -------------------------------------------------

    def set_anchor(self, point: Optional[Tuple[int, int]]) -> None:
        """Set the pill's centre (global px); None = the default, bottom centre of the primary screen."""
        if point is None:
            self._anchor = None
        else:
            try:
                self._anchor = (int(point[0]), int(point[1]))
            except (TypeError, ValueError, IndexError):
                return
        self._apply_geometry()

    def anchor(self) -> Tuple[int, int]:
        """The pill's current centre in global coordinates (after clamping to its screen)."""
        centre = self._effective_center()
        if centre is None:
            g = self.geometry()
            return (g.x() + g.width() // 2, g.y() + g.height() // 2)
        return centre

    def place_bottom(self) -> None:
        screen = self._screen_for(self._anchor)
        if screen is None:
            return
        self._anchor = self._default_center(screen.availableGeometry())
        self._apply_geometry()
        self.anchor_moved.emit(*self.anchor())

    def place_top(self) -> None:
        screen = self._screen_for(self._anchor)
        if screen is None:
            return
        # Just under the menu bar / notch (availableGeometry already excludes them).
        geo = screen.availableGeometry()
        self._anchor = (geo.x() + geo.width() // 2, geo.y() + _TOP_GAP + self._lay.h // 2)
        self._apply_geometry()
        self.anchor_moved.emit(*self.anchor())

    @staticmethod
    def _screen_for(point: Optional[Tuple[int, int]]):
        """The screen containing `point`, else the primary one (None if there is no screen at all)."""
        screen = QGuiApplication.screenAt(QPoint(*point)) if point is not None else None
        return screen or QGuiApplication.primaryScreen()

    @staticmethod
    def _default_center(geo) -> Tuple[int, int]:
        return (geo.x() + geo.width() // 2, geo.y() + geo.height() - _BOTTOM_GAP - _EXPANDED[0] // 2)

    def _effective_center(self) -> Optional[Tuple[int, int]]:
        """The anchor (or the default) clamped so the whole window is inside its screen's work area."""
        raw = self._anchor
        screen = self._screen_for(raw)
        if screen is None:
            return None
        geo = screen.availableGeometry()
        if raw is None:
            raw = self._default_center(geo)
        w, h = self.width(), self.height()
        x = max(geo.x(), min(raw[0] - w // 2, geo.x() + geo.width() - w))
        y = max(geo.y(), min(raw[1] - h // 2, geo.y() + geo.height() - h))
        return (x + w // 2, y + h // 2)

    def _apply_geometry(self) -> None:
        centre = self._effective_center()
        if centre is None:
            return
        self.move(centre[0] - self.width() // 2, centre[1] - self.height() // 2)

    def _on_screen_added(self, screen) -> None:
        screen.availableGeometryChanged.connect(self._on_screens_changed)
        self._apply_geometry()

    def _on_screens_changed(self, *_args) -> None:
        self._apply_geometry()

    # ---- native ---------------------------------------------------------------

    def _pin(self) -> None:
        try:
            macos_panel.pin_floating(self)
        except Exception:
            logger.exception("pin_floating failed")

    def _sync_glass(self) -> None:
        """Install/remove the system glass to match `set_glass_enabled` and visibility."""
        if not self._glass_enabled:
            self._drop_glass()
            return
        if self._glass is None and self.isVisible():
            try:
                self._glass = macos_panel.PillGlass.install(self, _MARGIN, self._lay.h / 2, self._dark)
            except Exception:
                logger.exception("PillGlass.install failed")
                self._glass = None

    def _drop_glass(self) -> None:
        glass, self._glass = self._glass, None
        if glass is not None:
            try:
                glass.remove()
            except Exception:
                logger.exception("PillGlass.remove failed")

    def event(self, event) -> bool:  # noqa: N802 -- Qt override naming
        if event.type() == QEvent.Type.WinIdChange:
            # The native window was (re)created: the old glass view is gone with it.
            self._drop_glass()
            self._pin()
            self._sync_glass()
        return super().event(event)

    def changeEvent(self, event) -> None:  # noqa: N802 -- Qt override naming
        kind = event.type()
        if kind in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            dark = theme.is_dark()
            if dark != self._dark:
                self._dark = dark
                if self._glass is not None:
                    self._glass.set_dark(dark)
            self.update()
        elif kind in (QEvent.Type.FontChange, QEvent.Type.ApplicationFontChange):
            self._relayout()
        super().changeEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 -- Qt override naming
        if self._glass is not None:
            self._glass.set_geometry(_MARGIN, self._lay.h / 2)
        super().resizeEvent(event)

    # ---- mouse: drag, click, context menu -------------------------------------

    def _pill_rect(self) -> QRectF:
        return QRectF(_MARGIN, _MARGIN, self._lay.w, self._lay.h)

    def mousePressEvent(self, event) -> None:  # noqa: N802 -- Qt override naming
        if event.button() != Qt.MouseButton.LeftButton or not self._pill_rect().contains(event.position()):
            event.ignore()
            return
        self._press_global = event.globalPosition().toPoint()
        self._press_center = self.anchor()
        self._dragging = False
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 -- Qt override naming
        if self._press_global is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            event.ignore()
            return
        delta = event.globalPosition().toPoint() - self._press_global
        if not self._dragging:
            if delta.manhattanLength() <= _DRAG_THRESHOLD:
                return
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self._anchor = (self._press_center[0] + delta.x(), self._press_center[1] + delta.y())
        self._apply_geometry()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 -- Qt override naming
        if event.button() != Qt.MouseButton.LeftButton or self._press_global is None:
            event.ignore()
            return
        dragged = self._dragging
        self._press_global = None
        self._dragging = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        event.accept()
        if dragged:
            # Remember where the pill actually ended up (after clamping).
            self._anchor = self.anchor()
            self.anchor_moved.emit(*self._anchor)
        elif self._state in (STATE_HIDDEN, STATE_ERROR):
            self.open_requested.emit()

    def contextMenuEvent(self, event) -> None:  # noqa: N802 -- Qt override naming
        # A popup menu could pull the app forward, and mid-dictation the
        # frontmost app must not change (see module docstring).
        if self._state in (STATE_RECORDING, STATE_PROCESSING):
            event.ignore()
            return
        menu = self._build_context_menu()
        menu.aboutToHide.connect(menu.deleteLater)
        menu.destroyed.connect(lambda *_: setattr(self, "_menu", None))
        self._menu = menu
        menu.popup(event.globalPos())
        event.accept()

    def _build_context_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction(strings.PILL_MENU_OPEN).triggered.connect(lambda *_: self.open_requested.emit())
        place = menu.addMenu(strings.PILL_MENU_PLACE)
        place.addAction(strings.PILL_PLACE_BOTTOM).triggered.connect(lambda *_: self.place_bottom())
        place.addAction(strings.PILL_PLACE_TOP).triggered.connect(lambda *_: self.place_top())
        menu.addSeparator()
        menu.addAction(strings.PILL_MENU_HIDE).triggered.connect(lambda *_: self.mode_change_requested.emit("active"))
        return menu

    # ---- metrics ----------------------------------------------------------------

    def _font(self, bold: bool = True) -> QFont:
        font = QFont(QApplication.font().family())
        font.setPixelSize(theme.fs(13))
        font.setBold(bold)
        return font

    def _measure(self) -> _Look:
        fm = QFontMetricsF(self._font(bold=True))
        state = self._state
        # +2px of slack so a centred, exactly-fitting text rect never clips.
        if state == STATE_RECORDING:
            h, pad, gap, icon = _EXPANDED
            timer_w = math.ceil(QFontMetricsF(self._font(bold=False)).horizontalAdvance("00:00")) + 2
            return _Look(_RECORDING_W, h, pad, gap, icon, timer_w=timer_w)
        if state == STATE_PROCESSING:
            h, pad, gap, icon = _EXPANDED
            text, text_w = self._fit_text(fm, self._processing_text, _ERROR_MAX_W - 2 * pad - icon - 2 * gap - _DOTS_W)
            content = icon + gap + text_w + gap + _DOTS_W
            return _Look(max(_PROCESSING_MIN_W, math.ceil(2 * pad + content)), h, pad, gap, icon, text, text_w)
        if state == STATE_ERROR:
            h, pad, gap, icon = _EXPANDED
            text, text_w = self._fit_text(fm, self._error_text, _ERROR_MAX_W - 2 * pad - icon - gap)
            return _Look(math.ceil(2 * pad + icon + gap + text_w), h, pad, gap, icon, text, text_w)
        h, pad, gap, icon = _COMPACT
        text, text_w = self._fit_text(fm, _IDLE_LOOK[self._idle_status][1], _ERROR_MAX_W)
        return _Look(math.ceil(2 * pad + icon + gap + text_w), h, pad, gap, icon, text, text_w)

    @staticmethod
    def _fit_text(fm: QFontMetricsF, text: str, max_w: float) -> Tuple[str, float]:
        text = " ".join(text.split())
        max_w -= 3  # the +2px slack below must not push the pill past its cap
        if fm.horizontalAdvance(text) > max_w:
            text = fm.elidedText(text, Qt.TextElideMode.ElideRight, max_w)
        return text, math.ceil(fm.horizontalAdvance(text)) + 2

    def _relayout(self) -> None:
        """Recompute the pill's size for the current state/text and resize, keeping the centre."""
        self._lay = self._measure()
        size = (self._lay.w + 2 * _MARGIN, self._lay.h + 2 * _MARGIN)
        if size != (self.width(), self.height()):
            self.setFixedSize(*size)
        self._apply_geometry()
        self.update()

    # ---- ticking --------------------------------------------------------------

    def _elapsed_text(self) -> str:
        secs = max(0, int(self._clock() - self._rec_start))
        return f"{secs // 60}:{secs % 60:02d}"

    def _read_bins(self) -> np.ndarray:
        samples = np.zeros(0, dtype=np.float32)
        if self._level_fn is not None:
            try:
                samples, _rms = self._level_fn()
            except Exception:
                samples = np.zeros(0, dtype=np.float32)
        return _compute_bins(np.asarray(samples))

    def _on_tick(self) -> None:
        if self._state == STATE_RECORDING:
            smoothed = (self._bins * _SMOOTH_KEEP + self._read_bins() * (1.0 - _SMOOTH_KEEP)).astype(np.float32)
            smoothed[smoothed < 1e-3] = 0.0  # let a decaying tail settle to exactly zero
            text = self._elapsed_text()
            if text != self._timer_text or not np.array_equal(smoothed, self._bins):
                self._bins = smoothed
                self._timer_text = text
                self.update()
        else:
            self._anim_t += _TICK_MS / 1000.0
            self.update()

    # ---- painting ----------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 -- Qt override naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        try:
            painter.translate(_MARGIN, _MARGIN)  # pill-local coordinates from here on
            self._paint_surface(painter)
            if self._state == STATE_RECORDING:
                self._paint_recording(painter)
            elif self._state == STATE_PROCESSING:
                self._paint_processing(painter)
            elif self._state == STATE_ERROR:
                self._paint_error(painter)
            else:
                self._paint_idle(painter)
        finally:
            painter.end()

    def _text_color(self) -> QColor:
        return QColor("#F5F5F7") if self._dark else QColor("#1D1D1F")

    def _paint_surface(self, p: QPainter) -> None:
        # Glass capsule: translucent fill, a rim, and a hairline highlight
        # along the top edge. All deterministic strokes -- no blur filter and
        # no drop shadow (README_AR.md's "بلا ظل").
        lay = self._lay
        rect = QRectF(0, 0, lay.w, lay.h)
        radius = lay.h / 2.0
        dark = self._dark
        native = self._glass is not None

        if native:  # the system glass supplies the material; Qt adds a tint only
            fill = QColor(30, 30, 36, 72) if dark else QColor(255, 255, 255, 51)
        else:  # no blur behind us, so keep the text legible over any backdrop
            fill = QColor(30, 30, 36, 179) if dark else QColor(255, 255, 255, 168)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(rect, radius, radius)

        if not native:  # a hairline just outside, so the glass reads on a white page
            edge = QPen(QColor(0, 0, 0, 89) if dark else QColor(0, 0, 0, 26))
            edge.setWidthF(1.0)
            p.setPen(edge)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(-0.5, -0.5, 0.5, 0.5), radius + 0.5, radius + 0.5)

        rim = QPen(QColor(255, 255, 255, 41) if dark else QColor(255, 255, 255, 199))
        rim.setWidthF(1.0)
        p.setPen(rim)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)

        # Inner top highlight, fading out towards both ends.
        y = 1.5
        x0, x1 = radius * 0.7, lay.w - radius * 0.7
        grad = QLinearGradient(x0, 0, x1, 0)
        peak = QColor(255, 255, 255, 46 if dark else 230)
        clear = QColor(255, 255, 255, 0)
        grad.setColorAt(0.0, clear)
        grad.setColorAt(0.5, peak)
        grad.setColorAt(1.0, clear)
        p.setPen(QPen(grad, 1.0))
        p.drawLine(QPointF(x0, y), QPointF(x1, y))

        if self._state == STATE_RECORDING:  # "حد مضيء بلون تختاره"
            for grow, alpha in ((3.0, 28), (1.5, 70), (0.0, 215)):
                ring = QColor(self._color)
                ring.setAlpha(alpha)
                pen = QPen(ring)
                pen.setWidthF(1.5)
                p.setPen(pen)
                edge = rect.adjusted(0.75 - grow, 0.75 - grow, grow - 0.75, grow - 0.75)
                p.drawRoundedRect(edge, edge.height() / 2, edge.height() / 2)

    def _slot(self, start: float, width: float, height: Optional[float] = None) -> QRectF:
        """A vertically centred box `width` wide whose START edge is `start` px in
        (the right edge when the layout is RTL)."""
        lay = self._lay
        height = lay.h if height is None else height
        x = lay.w - start - width if self.isRightToLeft() else start
        return QRectF(x, (lay.h - height) / 2.0, width, height)

    def _draw_icon(self, p: QPainter, name: str, rect: QRectF, color: QColor) -> None:
        pix = sf_symbols.pixmap(name, self._lay.icon, color, dpr=self.devicePixelRatioF())  # cached there
        p.drawPixmap(rect, pix, QRectF(pix.rect()))

    def _draw_text(self, p: QPainter, rect: QRectF, text: str, color: QColor, bold: bool = True) -> None:
        p.setFont(self._font(bold))
        p.setPen(color)
        flags = int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextSingleLine)
        p.drawText(rect, flags, text)

    def _paint_idle(self, p: QPainter) -> None:
        lay = self._lay
        status = self._idle_status
        name = _IDLE_LOOK[status][0]
        icon_color = _ORANGE if status == "loading" else self._text_color()
        if status == "disabled":
            p.setOpacity(0.6)
        icon_rect = self._slot(lay.pad, lay.icon, lay.icon)
        self._draw_icon(p, name, icon_rect, icon_color)
        if status == "ready":
            # Status dot at the icon's lower-left, ringed in the glass colour to lift it off the mic.
            centre = QPointF(icon_rect.left() + 1.5, icon_rect.bottom() - 1.5)
            ring = QPen(QColor(36, 36, 42, 235) if self._dark else QColor(255, 255, 255, 235))
            ring.setWidthF(1.5)
            p.setPen(ring)
            p.setBrush(_GREEN)
            p.drawEllipse(centre, 3.5, 3.5)
        self._draw_text(p, self._slot(lay.pad + lay.icon + lay.gap, lay.text_w), lay.text, self._text_color())

    def _paint_recording(self, p: QPainter) -> None:
        lay = self._lay
        self._draw_icon(p, "mic.fill", self._slot(lay.pad, lay.icon, lay.icon), _RED)

        wave_start = lay.pad + lay.icon + lay.gap
        wave_w = lay.w - lay.pad - lay.timer_w - lay.gap - wave_start
        area = self._slot(wave_start, wave_w, lay.h - 24)
        bins = self._bins[::-1] if self.isRightToLeft() else self._bins  # time flows leftward in RTL
        _STYLE_DRAWERS.get(self._style, _draw_bars)(p, area, bins, self._color)

        p.setOpacity(0.7)
        timer_rect = self._slot(lay.w - lay.pad - lay.timer_w, lay.timer_w)
        self._draw_text(p, timer_rect, strings.isolate_ltr(self._timer_text), self._text_color(), bold=False)

    def _paint_processing(self, p: QPainter) -> None:
        lay = self._lay
        content = lay.icon + lay.gap + lay.text_w + lay.gap + _DOTS_W
        x = (lay.w - content) / 2.0
        self._draw_icon(p, "waveform", self._slot(x, lay.icon, lay.icon), self._color)
        x += lay.icon + lay.gap
        self._draw_text(p, self._slot(x, lay.text_w), lay.text, self._text_color())
        x += lay.text_w + lay.gap

        # Subtle indeterminate three-dot animation -- the one place a
        # time-based term is allowed (see module docstring).
        dots = self._slot(x, _DOTS_W, 5.0)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            phase = (self._anim_t * 4.4 + i * 0.9) % (2 * math.pi)
            color = QColor(self._color)
            color.setAlpha(int(90 + 150 * (0.5 + 0.5 * math.sin(phase))))
            p.setBrush(color)
            p.drawEllipse(QPointF(dots.left() + 2.5 + i * 10.0, dots.center().y()), 2.5, 2.5)

    def _paint_error(self, p: QPainter) -> None:
        lay = self._lay
        self._draw_icon(p, "exclamationmark.triangle.fill", self._slot(lay.pad, lay.icon, lay.icon), _ORANGE)
        self._draw_text(p, self._slot(lay.pad + lay.icon + lay.gap, lay.text_w), lay.text, self._text_color())

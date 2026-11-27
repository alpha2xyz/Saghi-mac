"""
The notch island: Saghi's only on-screen presence while dictating.

One black shape fused to the top edge of the screen. At rest it is exactly
the hardware notch (so it is invisible); when dictation starts it springs
open a little wider and a 40 pt strip taller, and shows what is happening:

  hidden      closed and dormant: nothing is drawn and the window ignores the
              mouse, so other notch apps (and the menu bar) get every hover and
              click there. The island never opens Saghi's window (in any
              state).
  recording   open; a row of dots that scroll left at a constant pace, one
              slot every 0.2 s. Each slot becomes a bar as tall as the voice
              was loud in that 0.2 s (growing in over 0.12 s). Silence stays a
              flat row of dots: the scroll is the clock, the height is only
              ever the real microphone level.
  processing  the bars fade to 20 % and a label shimmers over them.
  error       the label (orange) instead of the bars; closes after a while.
  discarded   closes at once with nothing new drawn (Esc).

Drop-in replacement for `indicator.IndicatorWindow` as used by
`ui/dictation.py`: the same signals, show_*/hide_* methods and `state` values
("hidden", "recording", "processing", "error"). Pill-only knobs (style,
colour, idle mode, anchor, glass) are accepted and ignored.

GEOMETRY (points): the notch size comes from NSScreen (auxiliaryTopLeftArea /
auxiliaryTopRightArea / safeAreaInsets.top). Without a notch (older Macs,
external displays) a virtual one the height of the menu bar is used and the
closed shape is not drawn at all, so the island only appears while active.
Open = notch + 6 pt each side, + 40 pt strip. Bottom corners use Apple's
continuous ("squircle") curve, 12 pt closed and 18 pt open; two concave
10 pt fillets join the shape to the screen's top edge.

MOTION: opening is an underdamped spring (0.5 s, bounce 0.35), closing a
critically damped one; both integrated in 1 ms steps, and retargeting keeps
the current velocity. Content fades in (ease-out 0.25 s) as the shape opens
and out (ease-in 0.2 s) as it closes.

WINDOW: a fixed-size frameless, translucent, non-activating Qt.Tool window at
NSStatusWindowLevel (above the menu bar), on every Space and over full-screen
apps (macos_panel.pin_floating). The input mask is updated to the drawn shape
every frame, so the transparent rest of the window never eats clicks meant
for the menu bar or the app below. Once closed and settled the island goes
dormant: NSWindow.ignoresMouseEvents is set and nothing is painted, so the
notch belongs to whatever other app wants it until dictation starts again.
While open (recording, processing, error) it takes the mouse over its shape,
but a click never opens Saghi's window.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Callable, Optional, Tuple

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QBrush,
    QRegion,
)
from PySide6.QtWidgets import QWidget

from . import macos_glass, macos_panel, strings

logger = logging.getLogger("saghi.ui.notch")

LevelFn = Callable[[], Tuple[np.ndarray, float]]

STATE_HIDDEN = "hidden"
STATE_RECORDING = "recording"
STATE_PROCESSING = "processing"
STATE_ERROR = "error"

# ---- geometry (points) ----
_FALLBACK_NOTCH = (185.0, 32.0)  # used when NSScreen cannot be asked (tests, other OS)
_STRIP = 40.0  # content strip under the notch when open
_WING = 6.0  # the open shape reaches past the notch on each side
_HOVER_WING = 7.0  # extra width on hover
_HOVER_DROP = 3.0  # extra height on hover
_CLOSED_R = 12.0
_OPEN_R = 18.0
_EAR = 10.0
_OVERSHOOT = 16.0  # room for the open spring's overshoot

# ---- springs ----
_OPEN_K = (2 * math.pi / 0.5) ** 2
_OPEN_C = 4 * math.pi * (1 - 0.35) / 0.5
_CLOSE_K = 405.6
_CLOSE_C = 40.3
_HOVER_K = (2 * math.pi / 0.35) ** 2
_HOVER_C = 4 * math.pi * (1 - 0.2) / 0.35

# ---- waveform ----
_INTERVAL = 0.2  # one level slot every 0.2 s
_SETTLE = 0.9  # after stop, the scroll eases to rest over this long
_PITCH = 6.0
_DOT = 3.0
_GROW = 0.12
_WAVE_H = 30.0
_WAVE_PAD = 14.0
_LEVEL_REFERENCE = 0.04  # RMS that fills the strip's height (normal speech peaks ~0.08, RMS ~0.02)
_LEVEL_GAMMA = 0.6  # lifts quiet speech so it reads as speech
_PLACEHOLDER = QColor("#4a4c51")
_RECORDED = QColor("#b4b6bb")

# ---- label / shimmer ----
_MUTED = QColor("#8b8f98")
_INK = QColor("#f2f3f5")
_ERROR_INK = QColor("#ff9f0a")
_SHIMMER_PERIOD = 1.6
_LABEL_PT = 13

_MIN_PROCESSING_S = 0.6  # never flash "processing" shorter than this
_FRAME_MS = 16


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _smooth(u: float) -> float:
    return u * u * (3 - 2 * u)


def _ease_out(u: float) -> float:
    return 1 - (1 - u) ** 3


def _ease_in(u: float) -> float:
    return u ** 3


class _Spring:
    """Damped spring, mass 1, integrated in 1 ms steps; retargeting keeps velocity."""

    def __init__(self, k: float, c: float) -> None:
        self.x = 0.0
        self.v = 0.0
        self.target = 0.0
        self.k, self.c = k, c

    def set(self, target: float, k: float, c: float) -> None:
        self.target, self.k, self.c = target, k, c

    def step(self, dt: float) -> None:
        n = max(1, int(round(dt * 1000)))
        h = dt / n
        for _ in range(n):
            a = -self.k * (self.x - self.target) - self.c * self.v
            self.v += a * h
            self.x += self.v * h
        if abs(self.x - self.target) < 1e-4 and abs(self.v) < 1e-3:
            self.x, self.v = self.target, 0.0

    @property
    def settled(self) -> bool:
        return self.x == self.target and self.v == 0.0


class _Fade:
    """A value moving to a target over a duration on an easing curve."""

    def __init__(self, v: float = 0.0) -> None:
        self.v = v
        self._a: Optional[tuple] = None

    def to(self, target: float, now: float, dur: float, ease=_ease_out, delay: float = 0.0) -> None:
        self.value(now)
        if dur <= 0:
            self.v, self._a = target, None
            return
        self._a = (self.v, target, now + delay, dur, ease)

    def value(self, now: float) -> float:
        if self._a is None:
            return self.v
        v0, v1, t0, dur, ease = self._a
        u = (now - t0) / dur
        if u <= 0:
            self.v = v0
        elif u >= 1:
            self.v, self._a = v1, None
        else:
            self.v = v0 + (v1 - v0) * ease(u)
        return self.v

    @property
    def moving(self) -> bool:
        return self._a is not None


def continuous_bottom_path(x: float, y: float, w: float, h: float, r: float) -> QPainterPath:
    """Rectangle with a flat, square top edge and Apple-style continuous bottom corners."""
    path = QPainterPath()
    r = min(r, w / 2, h)
    k = min(r, w / 2 / 1.52866, h / 1.52866)
    R, B = x + w, y + h
    path.moveTo(x, y)
    path.lineTo(R, y)
    path.lineTo(R, B - 1.52866 * k)
    path.cubicTo(R, B - 1.08849 * k, R, B - 0.86841 * k, R - 0.07491 * k, B - 0.63149 * k)
    path.cubicTo(R - 0.16905 * k, B - 0.37282 * k, R - 0.37282 * k, B - 0.16905 * k, R - 0.63149 * k, B - 0.07491 * k)
    path.cubicTo(R - 0.86841 * k, B, R - 1.08849 * k, B, R - 1.52866 * k, B)
    path.lineTo(x + 1.52866 * k, B)
    path.cubicTo(x + 1.08849 * k, B, x + 0.86841 * k, B, x + 0.63149 * k, B - 0.07491 * k)
    path.cubicTo(x + 0.37282 * k, B - 0.16905 * k, x + 0.16905 * k, B - 0.37282 * k, x + 0.07491 * k, B - 0.63149 * k)
    path.cubicTo(x, B - 0.86841 * k, x, B - 1.08849 * k, x, B - 1.52866 * k)
    path.closeSubpath()
    return path


def level_from_rms(rms: float) -> float:
    """Microphone RMS -> 0..1 bar height. 0 stays exactly 0 (silence is flat)."""
    if rms <= 0:
        return 0.0
    return float(_clamp((rms / _LEVEL_REFERENCE) ** _LEVEL_GAMMA, 0.0, 1.0))


def notch_geometry() -> Tuple[float, float, float, bool]:
    """(centre x, width, height, has_notch) of the main screen's notch, in Qt points.

    Without a notch: a virtual one centred on the primary screen, the menu
    bar's height tall, and has_notch False.
    """
    screen = QGuiApplication.primaryScreen()
    geo = screen.geometry() if screen is not None else QRectF(0, 0, 1440, 900)
    cx = geo.x() + geo.width() / 2
    menubar = 24.0
    if screen is not None:
        menubar = max(24.0, float(screen.availableGeometry().y() - geo.y()))
    if macos_glass.platform_ok():
        try:
            import AppKit

            for s in AppKit.NSScreen.screens():
                top = float(s.safeAreaInsets().top)
                if top <= 0:
                    continue
                left = s.auxiliaryTopLeftArea()
                right = s.auxiliaryTopRightArea()
                frame = s.frame()
                width = float(right.origin.x - (left.origin.x + left.size.width))
                if width <= 0:
                    continue
                # NSScreen x is in the same global points as Qt on the primary screen.
                ncx = float(frame.origin.x + left.origin.x + left.size.width + width / 2)
                return ncx, width, top, True
        except Exception:  # noqa: BLE001 -- AppKit missing or changed: fall back
            logger.debug("Could not read the notch from NSScreen", exc_info=True)
    if not macos_glass.platform_ok():
        return cx, _FALLBACK_NOTCH[0], _FALLBACK_NOTCH[1], True
    return cx, 160.0, menubar, False


class NotchIsland(QWidget):
    state_changed = Signal(str)
    open_requested = Signal()  # never emitted: the island does not open the window
    anchor_moved = Signal(int, int)  # never emitted (the island does not move)
    mode_change_requested = Signal(str)  # never emitted

    def __init__(self, parent=None, clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(parent)
        self._clock = clock
        self._state = STATE_HIDDEN
        self._idle_status = "ready"
        self._label = ""
        self._label_is_error = False

        self._spring = _Spring(_OPEN_K, _OPEN_C)
        self._hover = _Spring(_HOVER_K, _HOVER_C)
        self._reveal = _Fade(0.0)
        self._wave_op = _Fade(1.0)
        self._label_op = _Fade(0.0)
        self._shimmer_t0 = 0.0
        self._open = False
        self._dormant = True  # closed and settled: draws nothing, mouse passes through
        self._last = self._clock()

        # waveform bookkeeping
        self._level_fn: Optional[LevelFn] = None
        self._levels: list[float] = []
        self._arrivals: list[float] = []
        self._started_at = -100.0
        self._stopped_at: Optional[float] = -100.0
        self._acc: list[float] = []
        self._next_slot = 0.0
        self._dropped = 0

        self._pending_close_at: Optional[float] = None
        self._processing_since = 0.0
        self._error_timer = QTimer(self)
        self._error_timer.setSingleShot(True)
        self._error_timer.timeout.connect(self.hide_indicator)

        self._timer = QTimer(self)
        self._timer.setInterval(_FRAME_MS)
        self._timer.timeout.connect(self._tick)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setMouseTracking(True)

        self._label_font = QFont()
        self._label_font.setPointSizeF(_LABEL_PT)
        self._label_font.setWeight(QFont.Weight.Medium)

        self._place()
        app = QGuiApplication.instance()
        if app is not None:
            app.screenAdded.connect(lambda *_: self._place())
            app.screenRemoved.connect(lambda *_: self._place())
            app.primaryScreenChanged.connect(lambda *_: self._place())
        self.show()
        self._pin()
        self._update_mask()

    # ------------------------------------------------------------ geometry
    def _place(self) -> None:
        cx, w, h, has = notch_geometry()
        self._cx_global, self._notch_w, self._notch_h, self._has_notch = cx, w, h, has
        self._canvas_w = w + 2 * (_WING + _HOVER_WING) + 2 * _OVERSHOOT + 2 * _EAR
        self._canvas_h = h + _STRIP + _OVERSHOOT
        self.setFixedSize(int(math.ceil(self._canvas_w)), int(math.ceil(self._canvas_h)))
        self._move_native()

    def _move_native(self) -> None:
        x = self._cx_global - self.width() / 2
        screen = QGuiApplication.primaryScreen()
        top = screen.geometry().y() if screen is not None else 0
        self.move(int(round(x)), int(top))
        if not macos_glass.platform_ok() or not self.isVisible():
            return
        try:  # Qt keeps windows under the menu bar; AppKit lets it sit on the screen's top edge
            win = macos_glass._ns_window(self)
            if win is None:
                return
            import AppKit

            main = AppKit.NSScreen.screens()[0].frame()
            win.setFrameTopLeftPoint_((x, main.origin.y + main.size.height))
        except Exception:  # noqa: BLE001
            logger.debug("Could not place the island on the top edge", exc_info=True)

    def _pin(self) -> None:
        if macos_panel.pin_floating(self):
            logger.info("Notch island pinned (notch %.0fx%.0f, real notch: %s)", self._notch_w, self._notch_h, self._has_notch)
        self._move_native()
        self._apply_passthrough()

    def _apply_passthrough(self) -> None:
        """Dormant: the native window lets every mouse event through to what is below."""
        if not macos_glass.platform_ok() or not self.isVisible():
            return
        try:
            win = macos_glass._ns_window(self)
            if win is not None and bool(win.ignoresMouseEvents()) != self._dormant:
                win.setIgnoresMouseEvents_(self._dormant)
        except Exception:  # noqa: BLE001
            logger.debug("Could not set the island's mouse pass-through", exc_info=True)

    def _set_dormant(self, value: bool) -> None:
        if value != self._dormant:
            self._dormant = value
            if value:
                self._hover.set(0.0, _HOVER_K, _HOVER_C)
                self._hover.x, self._hover.v = 0.0, 0.0
            self._apply_passthrough()
            self._update_mask()
            self.update()

    @property
    def dormant(self) -> bool:
        return self._dormant

    def showEvent(self, event) -> None:  # noqa: N802 -- Qt override
        super().showEvent(event)
        QTimer.singleShot(0, self._pin)

    # ------------------------------------------------------------ shape
    def _shape(self) -> Tuple[QPainterPath, float, float, float, float, float]:
        """(path, x0, width, height, radius, visibility) for this frame."""
        p = self._spring.x
        hv = self._hover.x
        w = self._notch_w + 2 * _WING * p + 2 * _HOVER_WING * hv * (1 - _clamp(p, 0, 1))
        h = self._notch_h + _STRIP * p + _HOVER_DROP * hv * (1 - _clamp(p, 0, 1))
        r = _CLOSED_R + (_OPEN_R - _CLOSED_R) * _clamp(p, 0, 1)
        x0 = self.width() / 2 - w / 2
        vis = 1.0 if self._has_notch else _clamp(p * 4, 0.0, 1.0)
        return continuous_bottom_path(x0, 0, w, h, r), x0, w, h, r, vis

    def _ears(self, x0: float, w: float) -> QPainterPath:
        """Concave fillets joining the shape's top corners to the screen's top edge."""
        e = _EAR
        path = QPainterPath()
        left = QPainterPath()
        left.moveTo(x0 + 0.5, 0)
        left.lineTo(x0 + 0.5, e)
        left.lineTo(x0, e)
        left.arcTo(QRectF(x0 - 2 * e, 0, 2 * e, 2 * e), 0, 90)
        left.closeSubpath()
        r = x0 + w
        right = QPainterPath()
        right.moveTo(r - 0.5, 0)
        right.lineTo(r - 0.5, e)
        right.lineTo(r, e)
        right.arcTo(QRectF(r, 0, 2 * e, 2 * e), 180, -90)
        right.closeSubpath()
        path.addPath(left)
        path.addPath(right)
        return path

    def _update_mask(self) -> None:
        path, x0, w, h, _r, vis = self._shape()
        if vis <= 0.01 or self._dormant:
            # nothing drawn: a 1px region keeps the window alive but click-through
            self.setMask(QRegion(0, 0, 1, 1))
            return
        poly = path.toFillPolygon().toPolygon()
        self.setMask(QRegion(poly).united(QRegion(int(x0 - _EAR), 0, int(w + 2 * _EAR), int(_EAR))))

    # ------------------------------------------------------------ public API
    def show_recording(self, level_fn: LevelFn) -> None:
        now = self._clock()
        self._level_fn = level_fn
        self._levels, self._arrivals, self._acc = [], [], []
        self._started_at = now
        self._stopped_at = None
        self._dropped = 0
        self._next_slot = now + _INTERVAL
        self._pending_close_at = None
        self._error_timer.stop()
        self._label = ""
        self._wave_op.to(1.0, now, 0.25)
        self._label_op.to(0.0, now, 0)
        self._set_open(True, now)
        self._enter(STATE_RECORDING)

    def show_processing(self, text: Optional[str] = None) -> None:
        now = self._clock()
        self._stop_wave(now)
        self._label = text or strings.INDICATOR_PROCESSING
        self._label_is_error = False
        if self._state != STATE_PROCESSING:
            self._processing_since = now
            self._shimmer_t0 = now
            self._wave_op.to(0.2, now, 0.45, _ease_out)
            self._label_op.to(1.0, now, 0.5, _smooth, delay=0.05)
        self._pending_close_at = None
        self._set_open(True, now)
        self._enter(STATE_PROCESSING)

    def show_error(self, message: str, auto_hide_ms: int = 2500) -> None:
        now = self._clock()
        self._stop_wave(now)
        self._label = message
        self._label_is_error = True
        self._wave_op.to(0.0, now, 0.25)
        self._label_op.to(1.0, now, 0.25)
        self._pending_close_at = None
        self._set_open(True, now)
        self._enter(STATE_ERROR)
        self._error_timer.start(max(500, int(auto_hide_ms)))

    def show_discarded(self) -> None:
        """Recording thrown away (Esc): close at once, no processing step."""
        now = self._clock()
        self._stop_wave(now)
        self._pending_close_at = None
        self._set_open(False, now)
        self._enter(STATE_HIDDEN)

    def hide_indicator(self) -> None:
        now = self._clock()
        self._error_timer.stop()
        self._stop_wave(now)
        if self._state == STATE_PROCESSING and now - self._processing_since < _MIN_PROCESSING_S:
            # the state is idle at once (the controller may start again); only the
            # visual close waits, so "processing" never flashes for a few frames
            self._pending_close_at = self._processing_since + _MIN_PROCESSING_S
            self._enter(STATE_HIDDEN)
            self._kick()
            return
        self._pending_close_at = None
        self._set_open(False, now)
        self._enter(STATE_HIDDEN)

    @property
    def state(self) -> str:
        return self._state

    def _enter(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.state_changed.emit(state)

    # ---- idle status (ready / loading / disabled) ----
    def set_idle_status(self, status: str) -> None:
        self._idle_status = status

    @property
    def idle_status(self) -> str:
        return self._idle_status

    # ---- pill-only knobs, accepted and ignored ----
    def set_style(self, style: str) -> None:
        pass

    def set_color(self, hex_color: str) -> None:
        pass

    def set_idle_mode(self, mode: str) -> None:
        pass

    @property
    def idle_mode(self) -> str:
        return "active"

    def set_glass_enabled(self, enabled: bool) -> None:
        pass

    def set_anchor(self, point) -> None:
        pass

    def anchor(self) -> Tuple[int, int]:
        return (int(self._cx_global), 0)

    def place_top(self) -> None:
        pass

    def place_bottom(self) -> None:
        pass

    # ------------------------------------------------------------ internals
    def _set_open(self, value: bool, now: float) -> None:
        if value:
            self._set_dormant(False)
        if value != self._open:
            self._open = value
            if value:
                self._spring.set(1.0, _OPEN_K, _OPEN_C)
                self._reveal.to(1.0, now, 0.25, _ease_out)
            else:
                self._reveal.to(0.0, now, 0.2, _ease_in)
                self._spring.set(0.0, _CLOSE_K, _CLOSE_C)
        self._kick()

    def _stop_wave(self, now: float) -> None:
        if self._stopped_at is None:
            self._stopped_at = now
        self._level_fn = None

    def _kick(self) -> None:
        if not self._timer.isActive():
            self._last = self._clock()
            self._timer.start()

    def _scroll_time(self, now: float) -> float:
        if self._stopped_at is None:
            return now - self._started_at
        u = _clamp((now - self._stopped_at) / _SETTLE, 0.0, 1.0)
        return self._stopped_at - self._started_at + _SETTLE * (u - u * u / 2)

    def _sample_level(self, now: float) -> None:
        if self._level_fn is None:
            return
        try:
            _samples, rms = self._level_fn()
            self._acc.append(float(rms))
        except Exception:  # noqa: BLE001 -- a recorder hiccup must not stop the animation
            logger.debug("level_fn failed", exc_info=True)
        while now >= self._next_slot:
            level = level_from_rms(float(np.mean(self._acc))) if self._acc else 0.0
            self._acc = []
            self._levels.append(level)
            self._arrivals.append(self._next_slot)
            self._next_slot += _INTERVAL
            if len(self._levels) > 400:
                del self._levels[:100]
                del self._arrivals[:100]
                self._dropped = self._dropped + 100

    def _tick(self) -> None:
        now = self._clock()
        dt = _clamp(now - self._last, 0.0, 0.05)
        self._last = now
        if self._pending_close_at is not None and now >= self._pending_close_at:
            self._pending_close_at = None
            self._set_open(False, now)
            self._enter(STATE_HIDDEN)
        self._sample_level(now)
        for fade in (self._reveal, self._wave_op, self._label_op):
            fade.value(now)  # finish fades even when nothing is painted (offscreen)
        self._spring.step(dt)
        self._hover.step(dt)
        self._update_mask()
        self.update()
        idle = (
            not self._open
            and self._spring.settled
            and self._hover.settled
            and not self._reveal.moving
            and self._pending_close_at is None
        )
        if idle:
            self._timer.stop()
            self._set_dormant(True)

    # ------------------------------------------------------------ events
    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover.set(1.0, _HOVER_K, _HOVER_C)
        self._kick()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover.set(0.0, _HOVER_K, _HOVER_C)
        self._kick()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        # The island only shows dictation; it never opens Saghi's window.
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------ painting
    def paintEvent(self, _event) -> None:  # noqa: N802
        now = self._clock()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path, x0, w, h, _r, vis = self._shape()
        if vis <= 0.001 or self._dormant:
            return
        painter.setOpacity(vis)
        black = QColor(0, 0, 0)
        painter.fillPath(path, black)
        painter.fillPath(self._ears(x0, w), black)

        reveal = self._reveal.value(now)
        if reveal <= 0.001:
            return
        strip_top = h - _STRIP
        # content slides up 3 pt as it fades in, clipped to the shape
        painter.save()
        painter.setClipPath(path)
        painter.translate(0, 3 * (1 - reveal))
        gate = _smooth(_clamp((self._spring.x - 0.3) / 0.5, 0.0, 1.0))  # content waits for room
        cx = self.width() / 2
        body_w = self._notch_w + 2 * _WING

        wave_alpha = self._wave_op.value(now) * reveal * gate
        if wave_alpha > 0.001:
            painter.setOpacity(vis * wave_alpha)
            self._paint_wave(painter, QRectF(cx - body_w / 2 + _WAVE_PAD, strip_top + (_STRIP - _WAVE_H) / 2, body_w - 2 * _WAVE_PAD, _WAVE_H), now)

        label_alpha = self._label_op.value(now) * reveal * gate
        if label_alpha > 0.001 and self._label:
            painter.setOpacity(vis * label_alpha)
            self._paint_label(painter, QRectF(cx - body_w / 2 + 10, strip_top, body_w - 20, _STRIP), now)
        painter.restore()

    def _paint_wave(self, painter: QPainter, rect: QRectF, now: float) -> None:
        width, height = rect.width(), rect.height()
        mid = rect.top() + height / 2
        sl = self._scroll_time(now) / _INTERVAL
        newest = int(math.floor(sl))
        slots = int(width / _PITCH) + 3
        dropped = self._dropped
        painter.setPen(Qt.PenStyle.NoPen)
        for j in range(newest, newest - slots - 1, -1):
            x = rect.left() + width - _DOT / 2 + _PITCH - (sl - j) * _PITCH
            if x <= rect.left() - _PITCH:
                break
            # soft edges: fade the first 5 % and the last 3 % of the strip
            u = (x - rect.left()) / width
            edge = _clamp(u / 0.05, 0.0, 1.0) * _clamp((1 - u) / 0.03, 0.0, 1.0)
            if edge <= 0:
                continue
            i = j - 1 - dropped
            if j < 1 or i < 0 or i >= len(self._levels):
                color = QColor(_PLACEHOLDER if j < 1 else _RECORDED)
                color.setAlphaF(edge)
                painter.setBrush(color)
                painter.drawEllipse(QPointF(x, mid), _DOT / 2, _DOT / 2)
                continue
            g = _clamp((now - self._arrivals[i]) / _GROW, 0.0, 1.0)
            bh = _DOT + (max(_DOT, self._levels[i] * height) - _DOT) * _smooth(g)
            color = QColor(_RECORDED)
            color.setAlphaF(edge)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(x - _DOT / 2, mid - bh / 2, _DOT, bh), _DOT / 2, _DOT / 2)

    def _paint_label(self, painter: QPainter, rect: QRectF, now: float) -> None:
        painter.setFont(self._label_font)
        fm = QFontMetricsF(self._label_font)
        text = fm.elidedText(self._label, Qt.TextElideMode.ElideRight, rect.width())
        tw = fm.horizontalAdvance(text)
        tx = rect.center().x() - tw / 2
        if self._label_is_error:
            painter.setPen(_ERROR_INK)
        else:
            # a highlight band sweeping across the text (right to left, Arabic reading order)
            elapsed = max(0.0, now - self._shimmer_t0) + 0.75 * _SHIMMER_PERIOD
            u = (elapsed % _SHIMMER_PERIOD) / _SHIMMER_PERIOD
            raw = 2 * u
            c = raw - 2 if raw >= 1.5 else raw
            c = -c  # sweep right-to-left
            grad = QLinearGradient(tx + (c - 1) * tw + tw, 0, tx + (c + 1) * tw + tw, 0)
            for stop, col in ((0.0, _MUTED), (0.4, _MUTED), (0.5, _INK), (0.6, _MUTED), (1.0, _MUTED)):
                grad.setColorAt(stop, col)
            painter.setPen(QPen(QBrush(grad), 1))
        painter.drawText(QRectF(tx, rect.top(), tw + 1, rect.height()), Qt.AlignmentFlag.AlignCenter, text)

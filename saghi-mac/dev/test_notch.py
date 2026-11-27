"""
Checks for saghi/ui/notch.py (the notch island), driven with a fake clock.

Run from saghi-mac/:
  QT_QPA_PLATFORM=offscreen PYTHONPATH=. SAGHI_DATA_DIR=$(mktemp -d) python3 dev/test_notch.py
"""

import math
import sys

import numpy as np
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from saghi.ui import notch  # noqa: E402

failures = 0
checks = 0


def check(cond, label):
    global failures, checks
    checks += 1
    if not cond:
        failures += 1
        print("FAIL:", label)


T = [0.0]
isl = notch.NotchIsland(clock=lambda: T[0])
states = []
isl.state_changed.connect(states.append)


def run(sec, step=0.016):
    for _ in range(int(sec / step)):
        T[0] += step
        isl._tick()


def render() -> QImage:
    img = QImage(isl.size(), QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    isl.render(img)
    return img


# --- pure helpers ---
check(notch.level_from_rms(0.0) == 0.0, "silence maps to exactly 0")
check(notch.level_from_rms(-1.0) == 0.0, "negative rms maps to 0")
check(notch.level_from_rms(10.0) == 1.0, "loud is clamped to 1")
check(0 < notch.level_from_rms(0.01) < notch.level_from_rms(0.03) < 1, "level grows with rms")
p = notch.continuous_bottom_path(0, 0, 100, 40, 12)
r = p.boundingRect()
check(abs(r.width() - 100) < 0.01 and abs(r.height() - 40) < 0.01, "shape path spans its rect")
check(p.contains(QPoint(50, 1)) and not p.contains(QPoint(1, 39)), "flat top, rounded bottom corner")

# --- springs ---
s = notch._Spring(notch._OPEN_K, notch._OPEN_C)
s.set(1.0, notch._OPEN_K, notch._OPEN_C)
peak = 0.0
for _ in range(200):
    s.step(0.01)
    peak = max(peak, s.x)
check(peak > 1.02, "open spring overshoots (bounce)")
check(s.settled and s.x == 1.0, "open spring settles on its target")
c = notch._Spring(notch._CLOSE_K, notch._CLOSE_C)
c.x = 1.0
c.set(0.0, notch._CLOSE_K, notch._CLOSE_C)
low = 1.0
for _ in range(200):
    c.step(0.01)
    low = min(low, c.x)
check(low >= -0.01, "close spring does not undershoot (critically damped)")

# --- states ---
check(isl.state == "hidden", "starts hidden")
closed_h = isl._shape()[3]
check(abs(closed_h - isl._notch_h) < 0.01, "closed shape is the notch height")

silence = lambda: (np.zeros(8), 0.0)  # noqa: E731
isl.show_recording(silence)
check(isl.state == "recording" and states[-1] == "recording", "show_recording -> recording")
run(1.0)
check(isl._shape()[3] > isl._notch_h + 30, "recording opens the strip")
check(len(isl._levels) >= 4 and all(v == 0.0 for v in isl._levels), "silence records flat levels")
a = render().constBits().tobytes()
run(0.016 * 3)
check(all(v == 0.0 for v in isl._levels), "still flat after more frames")

loud = lambda: (np.zeros(8), 0.08)  # noqa: E731
isl.show_recording(loud)
run(1.0)
check(any(v > 0.5 for v in isl._levels), "voice gives tall bars")

isl.show_processing()
check(isl.state == "processing", "processing state")
check(isl._label, "processing shows a label")
run(0.1)
isl.hide_indicator()
check(isl.state == "hidden", "an early hide is idle at once")
check(isl._shape()[3] > isl._notch_h + 30, "but stays open for the minimum processing time")
run(0.8)
check(isl._pending_close_at is None, "then closes")
run(1.0)
check(abs(isl._shape()[3] - isl._notch_h) < 0.01, "closed back to the notch")
check(not isl._timer.isActive(), "animation timer stops when settled")

isl.show_recording(silence)
run(0.3)
isl.show_discarded()
check(isl.state == "hidden", "discard closes at once")
run(1.0)

isl.show_error("x", auto_hide_ms=500)
check(isl.state == "error", "error state")

clicked = []
isl.open_requested.connect(lambda: clicked.append(1))
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402

ev = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(10, 10), QPointF(10, 10), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
isl.mouseReleaseEvent(ev)
check(clicked == [1], "a click asks to open Saghi")

# idle = dormant: nothing drawn, mouse passes through to other notch apps
check(not isl.dormant, "open (error) is not dormant")
isl.hide_indicator()  # the error timer runs on the real clock
run(2.0)
check(isl.state == "hidden" and isl.dormant, "closed and settled goes dormant")
img = QImage(isl.size(), QImage.Format.Format_ARGB32)
img.fill(QColor(0, 0, 0, 0))
isl.render(img)
check(all(img.pixelColor(x, y).alpha() == 0 for x in range(0, img.width(), 4) for y in range(0, img.height(), 4)), "dormant island paints nothing")
check(isl.mask().boundingRect().width() <= 1, "dormant mask is empty")
isl.mouseReleaseEvent(ev)
check(clicked == [1], "a click while dormant does not open Saghi")
isl.show_recording(silence)
check(not isl.dormant, "dictation wakes the island at once")
run(0.6)
check(isl._shape()[3] > isl._notch_h + 30, "and it opens as before")
isl.hide_indicator()
run(2.0)
check(isl.dormant, "dormant again after dictation")

# drop-in API used by ui/dictation.py
for name in ("set_style", "set_color", "set_idle_mode", "set_glass_enabled", "set_anchor", "set_idle_status", "place_top", "place_bottom", "anchor"):
    check(callable(getattr(isl, name, None)), f"has {name}")

if failures:
    print(f"{failures} of {checks} checks FAILED")
    sys.exit(1)
print(f"ALL PASSED ({checks} checks)")

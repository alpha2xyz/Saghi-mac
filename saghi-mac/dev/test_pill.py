#!/usr/bin/env python3
"""
Offscreen Qt test for the floating status pill (saghi.ui.indicator.IndicatorWindow):
idle/expanded looks and sizes, state signals, anchor + clamping, drag vs click,
the context menu, RTL mirroring, waveform smoothing, native hooks (pin/glass, via
fakes) and review screenshots. No engine, no recorder, no hotkey, no model.

sf_symbols / macos_panel are written by other modules; if their real files are
missing from a checkout, minimal stand-ins are injected via sys.modules so this
test still runs (the real ones are used as soon as they exist).

Run:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=. <venv>/bin/python dev/test_pill.py

Writes dev/screenshots/pill-<state>.png (ready, loading, disabled, recording,
processing, error) in the light theme, for a human to eyeball.
"""

from __future__ import annotations

import ast
import logging
import os
import sys
import tempfile
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SAGHI_DATA_DIR", tempfile.mkdtemp(prefix="saghi-test-pill-"))

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
from PySide6.QtCore import QEvent, QEventLoop, QPoint, QPointF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QContextMenuEvent, QImage, QMouseEvent, QPainter, QPalette, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_UI_DIR = REPO_ROOT / "saghi" / "ui"

if not (_UI_DIR / "sf_symbols.py").exists():
    _sf = types.ModuleType("saghi.ui.sf_symbols")

    def _stub_pixmap(name, size, color=None, *, weight="medium", variable=None, dpr=2.0):
        pm = QPixmap(int(size * dpr), int(size * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color) if color is not None else QColor("black"))
        p.drawEllipse(2, 2, size - 4, size - 4)
        p.end()
        return pm

    _sf.pixmap = _stub_pixmap
    sys.modules["saghi.ui.sf_symbols"] = _sf

if not (_UI_DIR / "macos_panel.py").exists():
    _mp = types.ModuleType("saghi.ui.macos_panel")
    _mp.pin_floating = lambda widget: False

    class _StubPillGlass:
        @staticmethod
        def install(widget, margin, radius, dark):
            return None

    _mp.PillGlass = _StubPillGlass
    sys.modules["saghi.ui.macos_panel"] = _mp

from saghi.settings import VALID_WAVEFORM_STYLES  # noqa: E402
from saghi.ui import indicator as ind  # noqa: E402
from saghi.ui import macos_panel, strings, theme  # noqa: E402
from saghi.ui.indicator import IndicatorWindow  # noqa: E402

_checks = 0


def check(condition: bool, message: str) -> None:
    global _checks
    assert condition, f"FAILED: {message}"
    _checks += 1
    print(f"ok: {message}")


app = QApplication.instance() or QApplication(sys.argv)
app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)  # as in the real app
theme.apply_ui_font(app, "amiri")  # the real UI font, for realistic metrics / screenshots

M = ind._MARGIN
COMPACT_H, EXPANDED_H = 36, 52


def spin(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def geo():
    return app.primaryScreen().availableGeometry()


def centre(win: IndicatorWindow) -> tuple[int, int]:
    g = win.geometry()
    return (g.x() + g.width() // 2, g.y() + g.height() // 2)


def pill_inside(win: IndicatorWindow) -> bool:
    return geo().contains(win.geometry().adjusted(M, M, -M, -M))


def new_pill(mode: str = "always") -> IndicatorWindow:
    win = IndicatorWindow()
    win.set_idle_mode(mode)
    return win


def silent():
    return np.zeros(1600, dtype=np.float32), 0.0


def loud():
    t = np.arange(1600, dtype=np.float32)
    return (0.25 * np.sin(t * 0.3)).astype(np.float32), 0.17


def mouse(win, kind, glob, button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton, local=None):
    if local is None:
        local = QPointF(win.width() / 2, win.height() / 2)
    if kind == QEvent.Type.MouseMove:
        button = Qt.MouseButton.NoButton
    if kind == QEvent.Type.MouseButtonRelease:
        buttons = Qt.MouseButton.NoButton
    ev = QMouseEvent(kind, QPointF(local), QPointF(glob), button, buttons, Qt.KeyboardModifier.NoModifier)
    handler = {
        QEvent.Type.MouseButtonPress: win.mousePressEvent,
        QEvent.Type.MouseMove: win.mouseMoveEvent,
        QEvent.Type.MouseButtonRelease: win.mouseReleaseEvent,
    }[kind]
    handler(ev)
    return ev


class Spy:
    def __init__(self, *signals):
        self.calls: list = []
        for sig in signals:
            sig.connect(lambda *a: self.calls.append(a))

    def clear(self):
        self.calls.clear()


P, R, MV = QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease, QEvent.Type.MouseMove

# ---- 1. hidden / idle visibility ---------------------------------------------------------

print("--- 1. idle visibility ---")

win = IndicatorWindow()
check(win.state == "hidden", "a new pill starts in the hidden (idle) state")
check(not win.isVisible(), "a new pill is not on screen")
check(win.idle_mode == "active", "the default idle mode is the old behaviour (hidden when idle)")
win.hide_indicator()
check(not win.isVisible(), "idle mode 'active': hide_indicator() keeps it off screen")
win.set_idle_mode("always")
check(win.isVisible() and win.state == "hidden", "set_idle_mode('always') puts the compact pill on screen")
win.set_idle_mode("bogus")
check(win.idle_mode == "always", "an invalid idle mode is ignored")
win.set_idle_mode("active")
check(not win.isVisible(), "set_idle_mode('active') takes the idle pill off screen again")
win.set_idle_mode("always")
win.hide_indicator()
check(win.isVisible(), "idle mode 'always': hide_indicator() leaves the compact pill on screen")

# ---- 2. sizes, states, signals ----------------------------------------------------------------

print("\n--- 2. compact vs expanded, state signals ---")

win = new_pill()
spy = Spy(win.state_changed)
idle_w = win.width()
check(win.height() == COMPACT_H + 2 * M, f"idle pill is {COMPACT_H}px high (+2*{M}px margin)")
check(win._lay.icon == 16 and win._lay.pad == 14 and win._lay.gap == 8, "idle metrics: icon 16, padding 14, gap 8")

win.show_recording(silent)
check(win.height() == EXPANDED_H + 2 * M and win.width() == 264 + 2 * M, "recording pill is 264x52 (+margin)")
check(win._lay.icon == 18 and win._lay.pad == 18 and win._lay.gap == 12, "expanded metrics: icon 18, padding 18, gap 12")
check(win.state == "recording" and win.isVisible(), "state is recording and the pill is visible")

win.show_processing()
proc_w = win.width()
check(win.height() == EXPANDED_H + 2 * M and proc_w >= 220 + 2 * M, "processing pill is expanded and at least 220 wide")
win.show_processing("جارٍ تحميل النموذج… يرجى الانتظار قليلًا")
check(win.width() >= proc_w and win._processing_text.startswith("جارٍ تحميل"), "a longer processing text never makes the pill narrower")
win.show_processing(strings.INDICATOR_PROCESSING)

win.show_error("خطأ", auto_hide_ms=60000)
short_w = win.width()
win.show_error(strings.INDICATOR_ERROR_MIC + " — تأكد من أذونات الميكروفون في إعدادات النظام", auto_hide_ms=60000)
long_w = win.width()
check(long_w > short_w, "the error pill grows with a longer message")
win.show_error("تعذر الوصول إلى الميكروفون " * 20, auto_hide_ms=60000)
check(400 <= win.width() - 2 * M <= 420, f"a very long error is capped at 420px (got {win.width() - 2 * M})")
check(win._lay.text.endswith("…") and len(win._lay.text) < 200, "a very long error message is elided")
win.hide_indicator()
check(win.state == "hidden" and win.width() == idle_w and win.height() == COMPACT_H + 2 * M, "back to idle: compact again")
check(
    [c[0] for c in spy.calls] == ["recording", "processing", "error", "hidden"],
    f"state_changed fires once per real change, in order: {[c[0] for c in spy.calls]}",
)

win = new_pill("active")
spy = Spy(win.state_changed)
check(not win.isVisible(), "idle mode 'active': not visible while idle")
win.show_recording(silent)
check(win.isVisible(), "idle mode 'active': visible while recording")
win.hide_indicator()
check(not win.isVisible() and win.state == "hidden", "idle mode 'active': hides again when idle")
win.show_error("خطأ", auto_hide_ms=40)
check(win.isVisible(), "idle mode 'active': visible while showing an error")
spin(250)
check(not win.isVisible() and win.state == "hidden", "idle mode 'active': the error auto-hides")
check([c[0] for c in spy.calls] == ["recording", "hidden", "error", "hidden"], "state_changed sequence in 'active' mode")

win = new_pill()
win.show_error("تعذّر الإملاء", auto_hide_ms=40)
spin(250)
check(win.state == "hidden" and win.isVisible(), "an error auto-returns to the idle pill, which stays on screen")
check(win.height() == COMPACT_H + 2 * M, "... in its compact look")

# ---- 3. idle status ------------------------------------------------------------------------------

print("\n--- 3. idle status ---")

win = new_pill()
widths = {}
images = {}
for status in ("ready", "loading", "disabled"):
    win.set_idle_status(status)
    widths[status] = win.width()
    images[status] = win.grab().toImage()
check(win.idle_status == "disabled", "set_idle_status stores the status")
check(widths["loading"] > widths["ready"], f"the 'loading' label is wider than 'ready' ({widths})")
check(widths["disabled"] != widths["ready"], "the 'disabled' label has its own width")
check(images["ready"] != images["loading"] != images["disabled"], "each idle status renders differently")
win.set_idle_status("nonsense")
check(win.idle_status == "disabled", "an invalid idle status is ignored")
win.set_idle_status("ready")
check(win.width() == widths["ready"], "going back to 'ready' restores the width")

# ---- 4. anchor / clamping ----------------------------------------------------------------------------

print("\n--- 4. anchor and clamping ---")

g = geo()
win = new_pill()
cx, cy = win.anchor()
check(g.contains(QPoint(cx, cy)) and (cx, cy) != (0, 0), "the default anchor is inside the primary screen and not (0, 0)")
check(cx == g.x() + g.width() // 2, "the default anchor is horizontally centred")
check(cy == g.y() + g.height() - 28 - 26, "the default anchor's centre is 28+26px above the bottom of the work area")
check(centre(win) == (cx, cy) and pill_inside(win), "the window is centred on the anchor, inside the work area")

win.set_anchor((300, 250))
check(win.anchor() == (300, 250) and centre(win) == (300, 250), "set_anchor moves the pill's centre to the point")
win.show_recording(silent)
check(centre(win) == (300, 250), "compact -> expanded keeps the centre")
win.show_error("رسالة خطأ طويلة نسبيًا لاختبار تغيير العرض", auto_hide_ms=60000)
check(centre(win) == (300, 250), "a different width keeps the centre too")
win.hide_indicator()
check(centre(win) == (300, 250), "expanded -> compact keeps the centre")

for label, point in (
    ("far top-left", (-5000, -5000)),
    ("far bottom-right", (9000, 9000)),
    ("just off the left edge", (g.x() + 2, g.y() + g.height() // 2)),
    ("just under the top edge", (g.x() + g.width() // 2, g.y() + 1)),
):
    win.set_anchor(point)
    check(pill_inside(win), f"an anchor {label} is clamped so the whole pill stays inside the work area")
    win.show_recording(silent)
    check(pill_inside(win), f"... also when expanded ({label})")
    win.hide_indicator()
    check(geo().contains(QPoint(*win.anchor())), f"anchor() reports the clamped centre ({label})")

win.set_anchor((9000, 9000))
win.show_recording(silent)
win.hide_indicator()
win.set_anchor((300, 250))
win.set_anchor(None)
check(win.anchor() == (cx, cy), "set_anchor(None) returns to the default position")
win.set_anchor(["bad"])
check(win.anchor() == (cx, cy), "a malformed anchor is ignored")
win.set_anchor([310, 260])
check(win.anchor() == (310, 260), "set_anchor accepts the [x, y] list stored in settings")

spy = Spy(win.anchor_moved)
win.place_top()
check(spy.calls == [(win.anchor()[0], win.anchor()[1])], "place_top emits anchor_moved with the new centre")
check(win.anchor()[1] == g.y() + 14 + COMPACT_H // 2, "place_top: just under the menu bar (top + 14 + half the pill height)")
check(win.anchor()[0] == g.x() + g.width() // 2 and pill_inside(win), "place_top: horizontally centred, inside the work area")
spy.clear()
win.place_bottom()
check(win.anchor() == (cx, cy) and len(spy.calls) == 1, "place_bottom: back to the default bottom centre, emits anchor_moved")

win.set_anchor((g.x() + g.width() - 10, g.y() + g.height() - 10))
win.move(-3000, -3000)  # simulate the work area changing under the pill
app.primaryScreen().availableGeometryChanged.emit(g)
check(pill_inside(win), "availableGeometryChanged re-clamps the pill")
win.move(-3000, -3000)
app.screenRemoved.emit(app.primaryScreen())
check(pill_inside(win), "screenRemoved re-clamps the pill")
win.move(-3000, -3000)
app.screenAdded.emit(app.primaryScreen())
check(pill_inside(win), "screenAdded re-clamps the pill")

# ---- 5. mouse: drag vs click ------------------------------------------------------------------------------

print("\n--- 5. drag and click ---")

win = new_pill()
win.set_anchor((400, 300))
opened = Spy(win.open_requested)
moved = Spy(win.anchor_moved)
check(win.cursor().shape() == Qt.CursorShape.OpenHandCursor, "the pill shows an open hand")
check(win.toolTip() == strings.PILL_TOOLTIP, "the tooltip is PILL_TOOLTIP")

start = QPointF(400, 300)
mouse(win, P, start)
mouse(win, MV, start + QPointF(2, 2))
check(centre(win) == (400, 300) and win.cursor().shape() == Qt.CursorShape.OpenHandCursor, "a 4px jiggle (manhattan <= 4) is not a drag yet")
mouse(win, MV, start + QPointF(30, -20))
check(win.cursor().shape() == Qt.CursorShape.ClosedHandCursor, "past 4px the cursor becomes a closed hand")
check(centre(win) == (430, 280), "dragging moves the window with the pointer")
mouse(win, MV, start + QPointF(60, 40))
mouse(win, R, start + QPointF(60, 40))
check(centre(win) == (460, 340), "the window follows the whole drag (delta from the press point)")
check(moved.calls == [(460, 340)], f"release after a drag emits anchor_moved(x, y) with the new centre: {moved.calls}")
check(opened.calls == [], "a drag does NOT emit open_requested")
check(win.cursor().shape() == Qt.CursorShape.OpenHandCursor and win.anchor() == (460, 340), "cursor back to open hand, anchor updated")

moved.clear()
opened.clear()
mouse(win, P, QPointF(460, 340))
mouse(win, R, QPointF(460, 340))
check(opened.calls == [()] and moved.calls == [], "press+release without moving is a click: open_requested, no anchor_moved")
opened.clear()
mouse(win, P, QPointF(460, 340))
mouse(win, MV, QPointF(462, 341))
mouse(win, R, QPointF(462, 341))
check(len(opened.calls) == 1 and moved.calls == [], "a click with a 3px wobble is still a click")

opened.clear()
mouse(win, P, QPointF(460, 340), button=Qt.MouseButton.RightButton, buttons=Qt.MouseButton.RightButton)
mouse(win, R, QPointF(460, 340), button=Qt.MouseButton.RightButton)
check(opened.calls == [] and moved.calls == [], "a right-button press/release is neither a click nor a drag")
mouse(win, P, QPointF(460, 340), local=QPointF(2, 2))
mouse(win, R, QPointF(460, 340), local=QPointF(2, 2))
check(opened.calls == [], "a press on the transparent margin is ignored")

QTest.mouseClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(win.width() // 2, win.height() // 2))
check(len(opened.calls) == 1, "a real QTest click on the pill emits open_requested")

# dragging into a corner: clamped, and the emitted anchor is the clamped centre
moved.clear()
mouse(win, P, QPointF(460, 340))
mouse(win, MV, QPointF(460 + 5000, 340 + 5000))
mouse(win, R, QPointF(460 + 5000, 340 + 5000))
check(pill_inside(win) and moved.calls == [win.anchor()], "dragging off the screen clamps; anchor_moved reports the clamped centre")

# clicks are ignored while recording / processing; honoured while showing an error
win.set_anchor((400, 300))
for state_name, enter in (
    ("recording", lambda: win.show_recording(silent)),
    ("processing", lambda: win.show_processing()),
):
    enter()
    opened.clear()
    c = centre(win)
    mouse(win, P, QPointF(*c))
    mouse(win, R, QPointF(*c))
    check(opened.calls == [], f"a click while {state_name} does NOT emit open_requested")
win.show_error("خطأ", auto_hide_ms=60000)
opened.clear()
c = centre(win)
mouse(win, P, QPointF(*c))
mouse(win, R, QPointF(*c))
check(len(opened.calls) == 1, "a click while showing an error emits open_requested")
win.show_recording(silent)
moved.clear()
opened.clear()
c = centre(win)
mouse(win, P, QPointF(*c))
mouse(win, MV, QPointF(c[0] + 25, c[1]))
mouse(win, R, QPointF(c[0] + 25, c[1]))
check(len(moved.calls) == 1 and opened.calls == [], "the pill can be dragged while recording (no focus involved)")
win.hide_indicator()

# ---- 6. context menu ------------------------------------------------------------------------------------------

print("\n--- 6. context menu ---")

win = new_pill()
opened = Spy(win.open_requested)
moved = Spy(win.anchor_moved)
mode_req = Spy(win.mode_change_requested)
menu = win._build_context_menu()
acts = menu.actions()
check(len(acts) == 4, "the menu has four entries: open, place (submenu), separator, hide")
check(acts[0].text() == strings.PILL_MENU_OPEN, "entry 1 is PILL_MENU_OPEN")
check(acts[1].text() == strings.PILL_MENU_PLACE and acts[1].menu() is not None, "entry 2 is the PILL_MENU_PLACE submenu")
check(acts[2].isSeparator(), "entry 3 is a separator")
check(acts[3].text() == strings.PILL_MENU_HIDE, "entry 4 is PILL_MENU_HIDE")
sub = acts[1].menu().actions()
check([a.text() for a in sub] == [strings.PILL_PLACE_BOTTOM, strings.PILL_PLACE_TOP], "the submenu offers bottom and top")
acts[0].trigger()
check(len(opened.calls) == 1, "PILL_MENU_OPEN emits open_requested")
sub[1].trigger()
check(len(moved.calls) == 1 and win.anchor()[1] == g.y() + 14 + COMPACT_H // 2, "PILL_PLACE_TOP moves the pill to the top and emits anchor_moved")
sub[0].trigger()
check(len(moved.calls) == 2 and win.anchor() == (cx, cy), "PILL_PLACE_BOTTOM moves it back and emits anchor_moved")
acts[3].trigger()
check(mode_req.calls == [("active",)], "PILL_MENU_HIDE emits mode_change_requested('active')")
check(win.isVisible() and win.idle_mode == "always", "the pill itself doesn't change mode -- the owner does")

ev = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(20, 20), QPoint(g.x() + 100, g.y() + 100))
win.contextMenuEvent(ev)
check(win._menu is not None and win._menu.isVisible(), "right-click while idle pops the menu up")
win._menu.close()
spin(30)
win.show_recording(silent)
win._menu = None
ev = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(20, 20), QPoint(g.x() + 100, g.y() + 100))
win.contextMenuEvent(ev)
check(win._menu is None, "no menu while recording (nothing may pull focus mid-dictation)")
win.hide_indicator()

# ---- 7. waveform smoothing, timer, silence ---------------------------------------------------------------------

print("\n--- 7. levels, smoothing, timer ---")

win = new_pill()
win.show_recording(loud)
first = win._bins.copy()
check(float(first.max()) > 0.5, "show_recording polls the level once immediately (first paint is not empty)")
target = ind._compute_bins(loud()[0])
level = {"fn": silent}
win.show_recording(lambda: level["fn"]())
check(not win._bins.any(), "silence starts from all-zero bins")
level["fn"] = loud
win._on_tick()
check(np.allclose(win._bins, target * 0.3, atol=1e-5), "one tick from silence: smoothed = 0*0.7 + new*0.3")
for _ in range(60):
    win._on_tick()
check(np.allclose(win._bins, target, atol=1e-3), "with a steady level the bins converge to it")
prev = win._bins.copy()
level["fn"] = silent
win._on_tick()
check(np.allclose(win._bins, prev * 0.7, atol=1e-5), "one tick towards silence: smoothed = prev*0.7")
for _ in range(80):
    win._on_tick()
check(not win._bins.any(), "the tail settles to exactly zero")

clock = {"t": 1000.0}
win._clock = lambda: clock["t"]
win.show_recording(silent)
check(win._timer_text == "0:00", "the timer starts at 0:00")
clock["t"] += 65.4
win._on_tick()
check(win._timer_text == "1:05", "the timer shows m:ss after 65s")
clock["t"] += 600
win._on_tick()
check(win._timer_text == "11:05", "the timer keeps counting minutes")
win.hide_indicator()

for rtl in (True, False):
    for style in VALID_WAVEFORM_STYLES:
        w = new_pill()
        w.setLayoutDirection(Qt.LayoutDirection.RightToLeft if rtl else Qt.LayoutDirection.LeftToRight)
        w.set_style(style)
        w.show_recording(silent)
        app.processEvents()
        a = w.grab().toImage()
        spin(150)
        b = w.grab().toImage()
        check(a == b, f"{'RTL' if rtl else 'LTR'} style={style!r}: silent renders ~150ms apart are pixel-identical")
        w.show_recording(loud)
        w.hide_indicator()

# ---- 8. RTL / LTR mirroring -------------------------------------------------------------------------------------

print("\n--- 8. RTL mirroring ---")


def colour_centroid_x(img: QImage, rgb: tuple[int, int, int], tol: int = 60):
    xs = []
    for y in range(img.height()):
        for x in range(img.width()):
            c = QColor(img.pixel(x, y))
            if c.alpha() > 200 and all(abs(a - b) <= tol for a, b in zip((c.red(), c.green(), c.blue()), rgb)):
                xs.append(x)
    return (sum(xs) / len(xs)) if xs else None


win = new_pill()
res = {}
for rtl in (True, False):
    win.setLayoutDirection(Qt.LayoutDirection.RightToLeft if rtl else Qt.LayoutDirection.LeftToRight)
    win.set_idle_status("loading")
    res[("loading", rtl)] = colour_centroid_x(win.grab().toImage(), (255, 159, 10))
    win.show_recording(silent)
    res[("recording", rtl)] = colour_centroid_x(win.grab().toImage(), (255, 59, 48))
    win.hide_indicator()
half = win.grab().toImage().width() / 2
check(res[("loading", True)] is not None and res[("loading", True)] > half, "RTL idle: the icon sits at the right end")
check(res[("loading", False)] is not None and res[("loading", False)] < half, "LTR idle: the icon sits at the left end")
check(res[("recording", True)] is not None and res[("recording", True)] > 132 + M, "RTL recording: the red mic sits at the right end")
check(res[("recording", False)] is not None and res[("recording", False)] < 132 + M, "LTR recording: the red mic sits at the left end")
win.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

# ---- 9. flags, focus safety ------------------------------------------------------------------------------------------

print("\n--- 9. window flags and focus safety ---")

win = new_pill()
flags = win.windowFlags()
for name in ("FramelessWindowHint", "WindowStaysOnTopHint", "Tool", "WindowDoesNotAcceptFocus"):
    check(bool(flags & getattr(Qt.WindowType, name)), f"window flag {name}")
for name in ("WA_ShowWithoutActivating", "WA_TranslucentBackground", "WA_MacAlwaysShowToolWindow"):
    check(win.testAttribute(getattr(Qt.WidgetAttribute, name)), f"attribute {name}")
tree = ast.parse(Path(ind.__file__).read_text(encoding="utf-8"))
banned = {"raise_", "activateWindow", "setFocus", "startSystemMove"}
called = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
check(not (called & banned), f"indicator.py never calls raise_/activateWindow/setFocus/startSystemMove (found: {called & banned})")

# ---- 10. native hooks (fakes) ---------------------------------------------------------------------------------------------

print("\n--- 10. macos_panel hooks ---")

calls: dict[str, list] = {"pin": [], "install": [], "geometry": [], "dark": [], "remove": []}


class FakeGlass:
    def set_geometry(self, margin, radius):
        calls["geometry"].append((margin, radius))

    def set_dark(self, dark):
        calls["dark"].append(dark)

    def remove(self):
        calls["remove"].append(True)


class FakePillGlass:
    @staticmethod
    def install(widget, margin, radius, dark):
        calls["install"].append((widget, margin, radius, dark))
        return FakeGlass()


real_pin, real_glass = macos_panel.pin_floating, macos_panel.PillGlass
macos_panel.pin_floating = lambda widget: calls["pin"].append(widget) or True
macos_panel.PillGlass = FakePillGlass
try:
    win = new_pill("active")
    check(calls["pin"] == [], "nothing is pinned before the first show()")
    win.show_recording(silent)
    check(len(calls["pin"]) >= 1 and calls["pin"][-1] is win, "pin_floating(self) runs after show()")
    n = len(calls["pin"])
    win.show_processing()
    check(len(calls["pin"]) > n, "... and after every later show()")
    check(calls["install"] == [], "no glass unless enabled (default off)")
    check(win._glass is None, "the default is no system glass")

    plain = win.grab().toImage()
    win.set_glass_enabled(True)
    check(len(calls["install"]) == 1 and calls["install"][0][:2] == (win, M), "set_glass_enabled(True) installs PillGlass with the margin")
    check(calls["install"][0][2] == EXPANDED_H / 2 and calls["install"][0][3] is False, "... radius = height/2, dark flag from the theme")
    check(win.grab().toImage() != plain, "with native glass the Qt fill is reduced to a tint (different pixels)")
    calls["geometry"].clear()
    win.show_recording(silent)
    win.hide_indicator()  # -> hidden in mode 'active': the window is hidden but keeps its glass
    win.set_idle_mode("always")
    check((M, COMPACT_H / 2) in calls["geometry"], "PillGlass.set_geometry(margin, radius) follows a resize")
    calls["geometry"].clear()
    win.show_recording(silent)
    check(calls["geometry"] and calls["geometry"][-1] == (M, EXPANDED_H / 2), "... and the resize back to expanded")

    dark_pal = QPalette(app.palette())
    dark_pal.setColor(QPalette.ColorRole.Window, QColor("#1E1E20"))
    dark_pal.setColor(QPalette.ColorRole.WindowText, QColor("#F5F5F7"))
    light_pal = QPalette(app.palette())
    app.setPalette(dark_pal)
    spin(20)
    check(win._dark is True and calls["dark"][-1:] == [True], "a palette change to dark updates the flag and PillGlass.set_dark")
    dark_img = win.grab().toImage()
    app.setPalette(light_pal)
    spin(20)
    check(win._dark is False and calls["dark"][-1:] == [False], "... and back to light")
    check(dark_img != win.grab().toImage(), "dark and light render differently")

    n_install, n_pin = len(calls["install"]), len(calls["pin"])
    calls["remove"].clear()
    win.event(QEvent(QEvent.Type.WinIdChange))
    check(len(calls["pin"]) == n_pin + 1, "WinIdChange calls pin_floating again")
    check(calls["remove"] == [True] and len(calls["install"]) == n_install + 1, "... and swaps the glass for a new one on the new native window")
    calls["remove"].clear()
    win.set_glass_enabled(False)
    check(calls["remove"] == [True] and win._glass is None, "set_glass_enabled(False) removes the glass")

    # a failing native layer must never break the pill
    logging.getLogger("saghi.ui.indicator").setLevel(logging.CRITICAL)  # it logs the tracebacks
    macos_panel.pin_floating = lambda widget: 1 / 0
    macos_panel.PillGlass = type("Boom", (), {"install": staticmethod(lambda *a: 1 / 0)})
    win.set_glass_enabled(True)
    win.show_processing()
    check(win.state == "processing" and win._glass is None, "an exception in pin_floating / PillGlass.install is swallowed")
finally:
    macos_panel.pin_floating, macos_panel.PillGlass = real_pin, real_glass
    app.setPalette(light_pal)
    spin(20)

# ---- 11. screenshots -------------------------------------------------------------------------------------------------------

print("\n--- 11. review screenshots (light theme) ---")

OUT = REPO_ROOT / "dev" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)
assert not theme.is_dark(), "screenshots are taken in the light theme"


def synthetic_level():
    rng = np.random.default_rng(7)
    n = 1600
    envelope = np.abs(np.sin(np.linspace(0.0, 3.2 * np.pi, n))) ** 1.5 * np.linspace(0.35, 1.0, n)
    samples = (0.20 * envelope * rng.standard_normal(n)).astype(np.float32)
    return samples, float(np.sqrt(np.mean(samples**2)))


def backdrop(w: int, h: int) -> QImage:
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor("#EDEFF5"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#FFFFFF"))
    p.drawRoundedRect(20, 14, w - 40, h - 28, 10, 10)
    p.setBrush(QColor("#D9DCE6"))
    for i in range(4):
        p.drawRoundedRect(40, 30 + i * 22, int((w - 80) * (0.9 - 0.15 * i)), 8, 4, 4)
    p.end()
    return img


def snapshot(win: IndicatorWindow, name: str) -> None:
    pix = win.grab()
    w, h = max(pix.width() + 40, 320), pix.height() + 60
    img = backdrop(w, h)
    p = QPainter(img)
    p.drawPixmap((w - pix.width()) // 2, (h - pix.height()) // 2 + 26, pix)
    p.end()
    img = img.scaled(img.width() * 2, img.height() * 2, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    path = OUT / f"pill-{name}.png"
    img.save(str(path))
    check(path.exists() and path.stat().st_size > 1500, f"wrote {path.name} ({path.stat().st_size} bytes)")


shot = new_pill()
shot.set_color("#4A90D9")
shot.set_style("bars")
for status in ("ready", "loading", "disabled"):
    shot.set_idle_status(status)
    snapshot(shot, status)
shot.set_idle_status("ready")
shot.show_recording(synthetic_level)
for _ in range(45):
    shot._on_tick()
snapshot(shot, "recording")
shot.show_processing()
shot._anim_t = 0.5
snapshot(shot, "processing")
shot.show_error(strings.INDICATOR_ERROR_MIC, auto_hide_ms=60000)
snapshot(shot, "error")
shot.hide_indicator()

# ---- nearest screen for off-screen points (multi-monitor drags) --------------

from unittest.mock import patch as _patch  # noqa: E402

from PySide6.QtCore import QRect  # noqa: E402


class _FakeScreen:
    def __init__(self, name, rect):
        self.name, self._rect = name, rect

    def geometry(self):
        return self._rect


_primary = _FakeScreen("A", QRect(0, 0, 1440, 900))
_second = _FakeScreen("B", QRect(1440, 0, 1920, 1080))


def _screen_at(point):
    for sc in (_primary, _second):
        if sc.geometry().contains(point):
            return sc
    return None


with _patch("saghi.ui.indicator.QGuiApplication.screens", return_value=[_primary, _second]), \
        _patch("saghi.ui.indicator.QGuiApplication.screenAt", side_effect=_screen_at), \
        _patch("saghi.ui.indicator.QGuiApplication.primaryScreen", return_value=_primary):
    check(IndicatorWindow._screen_for((2000, 500)).name == "B", "a point on the second screen maps to it")
    check(IndicatorWindow._screen_for((3398, 1000)).name == "B",
          "a mid-drag point just past the second screen's right edge stays on that screen (not the primary)")
    check(IndicatorWindow._screen_for((2400, 1150)).name == "B", "...and just past its bottom edge too")
    check(IndicatorWindow._screen_for((-40, 300)).name == "A", "a point left of everything maps to the nearest (left) screen")
    check(IndicatorWindow._screen_for(None).name == "A", "no point -> the primary screen")

_top = _FakeScreen("T", QRect(0, -1080, 1920, 1080))  # a monitor stacked above

with _patch("saghi.ui.indicator.QGuiApplication.screens", return_value=[_primary, _top]), \
        _patch("saghi.ui.indicator.QGuiApplication.screenAt",
               side_effect=lambda pt: next((sc for sc in (_primary, _top) if sc.geometry().contains(pt)), None)), \
        _patch("saghi.ui.indicator.QGuiApplication.primaryScreen", return_value=_primary):
    check(IndicatorWindow._screen_for((900, -1100)).name == "T",
          "a point just above a vertically stacked monitor maps to that monitor (vertical distance counts)")

# ---- timers and the error auto-hide -------------------------------------------------

from PySide6.QtCore import QEventLoop as _Loop, QTimer as _QT  # noqa: E402


def _spin(ms: int) -> None:
    loop = _Loop()
    _QT.singleShot(ms, loop.quit)
    loop.exec()


timers = IndicatorWindow()
timers.set_idle_mode("always")
timers.show_error("خطأ", auto_hide_ms=100)
timers.show_recording(lambda: (np.zeros(1600, dtype=np.float32), 0.0))
_spin(300)
check(timers.state == "recording", "a new recording right after an error is not cut short by the error's auto-hide")
timers.show_error("خطأ", auto_hide_ms=100)
timers.show_processing()
_spin(300)
check(timers.state == "processing", "...and neither is processing")
timers.hide_indicator()
check(not timers._timer.isActive(), "the 30 fps timer is off while the pill rests (no idle CPU use)")
timers.show_error("خطأ", auto_hide_ms=5000)
check(not timers._timer.isActive(), "...and while it shows an error")
timers.hide_indicator()

print(f"\nALL PASSED ({_checks} checks)")

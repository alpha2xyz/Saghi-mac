#!/usr/bin/env python3
"""
Tests for saghi.ui.macos_panel -- the native macOS behaviour of the status pill
(pin_floating + PillGlass). No macOS, no pyobjc: everything AppKit-shaped is a
fake injected into sys.modules, with macos_glass.platform_ok patched to True,
so what is checked is the exact sequence of AppKit calls and their values:

  1. Off-mac (real platform, "darwin but offscreen", "darwin without pyobjc"):
     pin_floating() is False and PillGlass.install() is None, nothing raised.
  2. pin_floating on a fake NSPanel: level 25, collectionBehavior ASSIGNED to
     exactly 337 (Qt's MoveToActiveSpace bit is dropped, not OR-ed in), the
     NonactivatingPanel bit OR-ed into the style mask only for panels and only
     once, floatingPanel/becomesKeyOnlyIfNeeded set, no shadow, not hidden on
     deactivate, and the level is applied AFTER setFloatingPanel: (which resets
     it in real AppKit). Re-pinning after Qt "resets" the window restores it.
  3. Every AppKit call failing in turn -> False, never an exception, and the
     other steps still run.
  4. PillGlass: NSGlassEffectView when objc.lookUpClass finds it, otherwise an
     NSVisualEffectView (HUD material, behind-window, active state, rounded via
     a 9-slice maskImage -- never a layer corner radius); frame inset by the
     margin below Qt's content view; set_geometry / set_dark / remove;
     SAGHI_NO_GLASS; half-installed views are cleaned up; nothing raises.

Real behaviour (does the pill really stay over a full-screen app on every
Space, does clicking it leave TextEdit's caret alone, does the glass look
right on macOS 13 and 26) needs a manual check on a Mac.

Run:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=. <venv>/bin/python dev/test_macos_panel.py

Plain assert-based, no pytest -- same style as the other dev/ tests.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
logging.disable(logging.CRITICAL)  # the failure cases below log warnings on purpose

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from saghi.ui import macos_glass, macos_panel  # noqa: E402
from saghi.ui.macos_panel import PillGlass, pin_floating  # noqa: E402

_checks = 0


def check(condition: bool, message: str) -> None:
    global _checks
    assert condition, f"FAILED: {message}"
    _checks += 1
    print(f"ok: {message}")


# ---- fake AppKit / objc --------------------------------------------------------


class Rect:
    """NSRect look-alike (pyobjc exposes .origin.x / .size.width the same way)."""

    def __init__(self, x, y, w, h):
        self.origin = types.SimpleNamespace(x=x, y=y)
        self.size = types.SimpleNamespace(width=w, height=h)

    def t(self):
        return (self.origin.x, self.origin.y, self.size.width, self.size.height)


class Recorder:
    """Base for fakes: logs every setter and can be told to raise on chosen ones."""

    def __init__(self):
        self.log: list[tuple] = []
        self.fail: set[str] = set()  # raise every time
        self.fail_once: set[str] = set()  # raise on the first call only

    def _rec(self, name, *args):
        self.log.append((name, *args))
        if name in self.fail:
            raise RuntimeError(f"boom {name}")
        if name in self.fail_once:
            self.fail_once.discard(name)
            raise RuntimeError(f"boom once {name}")

    def calls(self, name):
        return [entry for entry in self.log if entry[0] == name]


class NSPanel:  # the class AppKit.NSPanel stands for
    pass


class FakeWindow(Recorder):
    """A plain NSWindow: no NSPanel-only selectors (calling one raises AttributeError)."""

    def __init__(self, content):
        super().__init__()
        self.level_value = 8  # what Qt leaves for WindowStaysOnTopHint
        self.behavior = (1 << 8) | (1 << 1)  # Qt's FullScreenAuxiliary | MoveToActiveSpace
        self.mask = (1 << 4) | (1 << 1) | (1 << 2) | (1 << 3)  # utility|closable|miniaturizable|resizable
        self.shadow = True
        self.hides = True
        self.can_hide = True
        self.content = content

    def isKindOfClass_(self, cls):
        self._rec("isKindOfClass_", cls)
        return isinstance(self, cls)

    def styleMask(self):
        return self.mask

    def setStyleMask_(self, value):
        self._rec("setStyleMask_", value)
        self.mask = value

    def collectionBehavior(self):
        return self.behavior

    def setCollectionBehavior_(self, value):
        self._rec("setCollectionBehavior_", value)
        self.behavior = value

    def setLevel_(self, value):
        self._rec("setLevel_", value)
        self.level_value = value

    def setHidesOnDeactivate_(self, value):
        self._rec("setHidesOnDeactivate_", value)
        self.hides = value

    def setCanHide_(self, value):
        self._rec("setCanHide_", value)
        self.can_hide = value

    def setHasShadow_(self, value):
        self._rec("setHasShadow_", value)
        self.shadow = value

    def setOpaque_(self, value):
        self._rec("setOpaque_", value)

    def setBackgroundColor_(self, value):
        self._rec("setBackgroundColor_", value)

    def contentView(self):
        return self.content


class FakePanelWindow(FakeWindow, NSPanel):
    def __init__(self, content):
        super().__init__(content)
        self.floating = False
        self.key_only_if_needed = False

    def setFloatingPanel_(self, value):
        self._rec("setFloatingPanel_", value)
        self.floating = value
        self.level_value = 3 if value else 0  # AppKit moves a floating panel to NSFloatingWindowLevel

    def setBecomesKeyOnlyIfNeeded_(self, value):
        self._rec("setBecomesKeyOnlyIfNeeded_", value)
        self.key_only_if_needed = value

    def respondsToSelector_(self, selector):
        return selector == b"_setPreventsActivation:" and not getattr(self, "no_private", False)

    def _setPreventsActivation_(self, value):
        self._rec("_setPreventsActivation_", value)


class FakeView(Recorder):
    def __init__(self, frame=None):
        super().__init__()
        self.frame_value = frame or Rect(0, 0, 0, 0)
        self.parent = None
        self.subviews: list = []
        self.autoresizing = None
        self.appearance = None

    @classmethod
    def alloc(cls):
        return cls()

    def initWithFrame_(self, frame):
        self.frame_value = frame
        return self

    def frame(self):
        return self.frame_value

    def setFrame_(self, frame):
        self._rec("setFrame_", frame.t())
        self.frame_value = frame

    def superview(self):
        return self.parent

    def setAutoresizingMask_(self, mask):
        self._rec("setAutoresizingMask_", mask)
        self.autoresizing = mask

    def setAppearance_(self, appearance):
        self._rec("setAppearance_", appearance)
        self.appearance = appearance

    def addSubview_positioned_relativeTo_(self, view, position, relative):
        self._rec("addSubview_positioned_relativeTo_", view, position, relative)
        view.parent = self
        self.subviews.append(view)

    def removeFromSuperview(self):
        self._rec("removeFromSuperview")
        if self.parent is not None:
            self.parent.subviews.remove(self)
            self.parent = None


class FakeGlassView(FakeView):
    """NSGlassEffectView: cornerRadius + contentView, no maskImage."""

    created = 0

    def __init__(self, frame=None):
        super().__init__(frame)
        FakeGlassView.created += 1
        self.corner_radius = None
        self.content_view = None

    def setCornerRadius_(self, radius):
        self._rec("setCornerRadius_", radius)
        self.corner_radius = radius

    def setContentView_(self, view):
        self._rec("setContentView_", view)
        self.content_view = view


class FakeEffectView(FakeView):
    """NSVisualEffectView: material/blending/state + maskImage, no cornerRadius."""

    created = 0

    def __init__(self, frame=None):
        super().__init__(frame)
        FakeEffectView.created += 1
        self.material = self.blending = self.state = None
        self.mask_image = "unset"

    def setMaterial_(self, value):
        self._rec("setMaterial_", value)
        self.material = value

    def setBlendingMode_(self, value):
        self._rec("setBlendingMode_", value)
        self.blending = value

    def setState_(self, value):
        self._rec("setState_", value)
        self.state = value

    def setMaskImage_(self, image):
        self._rec("setMaskImage_", image)
        self.mask_image = image


DRAWN: list[tuple] = []  # (rect tuple, xRadius, yRadius) filled by the mask's drawing handler
STRETCH = object()  # stands for AppKit.NSImageResizingModeStretch (0 on Intel, 1 on arm64)


class FakeImage:
    def __init__(self, size, flipped, handler):
        self.size, self.flipped, self.handler = size, flipped, handler
        self.cap_insets = self.resizing = None

    def setCapInsets_(self, insets):
        self.cap_insets = insets

    def setResizingMode_(self, mode):
        self.resizing = mode

    def draw(self):
        return self.handler(Rect(0, 0, *self.size))


class FakePath:
    def __init__(self, rect, xr, yr):
        self.args = (rect.t(), xr, yr)

    def fill(self):
        DRAWN.append(self.args)


def make_appkit():
    m = types.ModuleType("AppKit")
    m.NSPanel = NSPanel
    m.NSView = FakeView
    m.NSVisualEffectView = FakeEffectView
    m.NSColor = types.SimpleNamespace(clearColor=lambda: "CLEAR")
    m.NSMakeRect = Rect
    m.NSMakeSize = lambda w, h: (w, h)
    m.NSEdgeInsetsMake = lambda t, left, b, r: (t, left, b, r)
    m.NSImageResizingModeStretch = STRETCH
    m.NSImage = types.SimpleNamespace(imageWithSize_flipped_drawingHandler_=FakeImage)
    m.NSBezierPath = types.SimpleNamespace(
        bezierPathWithRoundedRect_xRadius_yRadius_=lambda rect, xr, yr: FakePath(rect, xr, yr)
    )
    m.NSAppearance = types.SimpleNamespace(appearanceNamed_=lambda name: ("appearance", name))
    m.NSAppearanceNameAqua = "Aqua"
    m.NSAppearanceNameDarkAqua = "DarkAqua"
    return m


class Mac:
    """One fake macOS: a frame view holding Qt's content view inside `window`."""

    def __init__(self, panel: bool = True, glass_class=FakeGlassView, wid: int = 0):
        self.frame_view = FakeView(Rect(0, 0, 200, 60))
        self.content = FakeView(Rect(0, 0, 200, 60))
        self.content.parent = self.frame_view
        self.frame_view.subviews.append(self.content)
        self.window = (FakePanelWindow if panel else FakeWindow)(self.content)
        self.glass_class = glass_class
        self.wid = wid
        self.pointers: list[int] = []
        self.window_lookup_fails = False

        objc = types.ModuleType("objc")

        class nosuchclass_error(Exception):
            pass

        def objc_object(c_void_p):
            self.pointers.append(c_void_p)
            if self.window_lookup_fails:
                raise RuntimeError("no such view")
            return types.SimpleNamespace(window=lambda: self.window)

        def lookUpClass(name):
            if name == "NSGlassEffectView" and self.glass_class is not None:
                return self.glass_class
            raise nosuchclass_error(name)

        objc.objc_object = objc_object
        objc.lookUpClass = lookUpClass
        objc.nosuchclass_error = nosuchclass_error
        self.objc = objc
        self.appkit = make_appkit()

    @contextlib.contextmanager
    def active(self):
        with patch.dict(sys.modules, {"AppKit": self.appkit, "objc": self.objc}), \
                patch.object(macos_glass, "platform_ok", return_value=True):
            yield self


# ---- a real widget for winId() -------------------------------------------------

app = QApplication.instance() or QApplication([])
pill = QWidget()
pill.setWindowFlags(
    Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
)
pill.setAttribute(Qt.WA_ShowWithoutActivating)
pill.setAttribute(Qt.WA_TranslucentBackground)
pill.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
pill.resize(200, 60)
WID = int(pill.winId())


# ---- 0. constants ----------------------------------------------------------------

print("--- 0. constants ---")

check(macos_panel.PILL_COLLECTION_BEHAVIOR == 337, "collectionBehavior is 337")
check(macos_panel.PILL_COLLECTION_BEHAVIOR == 1 | 16 | 64 | 256,
      "337 = canJoinAllSpaces|stationary|ignoresCycle|fullScreenAuxiliary")
check(not macos_panel.PILL_COLLECTION_BEHAVIOR & (1 << 1), "MoveToActiveSpace (Qt's bit) is not in it")
check(macos_panel._STATUS_WINDOW_LEVEL == 25, "NSStatusWindowLevel is 25")
check(macos_panel._STYLE_NONACTIVATING_PANEL == 1 << 7 == 128, "NonactivatingPanel style bit is 1 << 7")
check(macos_panel._MATERIAL_HUD_WINDOW == 13, "HUD window material is 13")


# ---- 1. off-mac ----------------------------------------------------------------

print("--- 1. off-mac: no-ops, no exceptions ---")

with patch.object(sys, "platform", "linux"):
    check(pin_floating(pill) is False, "pin_floating is False on Linux")
    check(PillGlass.install(pill, 6, 18.0, True) is None, "PillGlass.install is None on Linux")

with patch.object(sys, "platform", "darwin"), patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
    check(pin_floating(pill) is False, "pin_floating is False under the offscreen Qt platform")
    check(PillGlass.install(pill, 6, 18.0, True) is None, "PillGlass.install is None under offscreen")

with patch.object(sys, "platform", "darwin"), \
        patch.dict(os.environ, {"QT_QPA_PLATFORM": "cocoa"}), \
        patch.dict(sys.modules, {"AppKit": None, "objc": None}):
    check(pin_floating(pill) is False, "pin_floating is False without pyobjc")
    check(PillGlass.install(pill, 6, 18.0, True) is None, "PillGlass.install is None without pyobjc")


# ---- 2. pin_floating -----------------------------------------------------------

print("--- 2. pin_floating on a panel ---")

mac = Mac(panel=True)
original_mask = mac.window.mask
with mac.active():
    check(pin_floating(pill) is True, "pin_floating returns True")
    check(mac.pointers == [WID], "it looked the NSWindow up from the widget's winId()")
    w = mac.window
    check(w.level_value == 25, "level is 25 (NSStatusWindowLevel)")
    check(w.behavior == 337, "collectionBehavior is exactly 337 (assigned, not OR-ed)")
    check(not w.behavior & (1 << 1), "Qt's MoveToActiveSpace bit is gone")
    check(w.mask == original_mask | (1 << 7), "NonactivatingPanel is OR-ed into the style mask")
    check(w.mask & original_mask == original_mask, "no other style-mask bit was cleared")
    check(w.floating is True, "setFloatingPanel_(True)")
    check(w.key_only_if_needed is True, "setBecomesKeyOnlyIfNeeded_(True)")
    check(w.hides is False, "hidesOnDeactivate is off")
    check(w.can_hide is False, "canHide is off (Cmd+H keeps the pill)")
    check(w.shadow is False, "no window shadow")
    check(w.log[-1] == ("setLevel_", 25), "the level is set last (setFloatingPanel: would reset it)")
    check(w.calls("_setPreventsActivation_") == [("_setPreventsActivation_", True)],
          "the window server's prevents-activation tag is set via the private setter")

    # Idempotent: a second call leaves the style mask alone (setStyleMask: can rebuild the view tree).
    check(pin_floating(pill) is True, "second pin_floating still True")
    check(len(w.calls("setStyleMask_")) == 1, "the style mask is only written the first time")
    check(w.level_value == 25 and w.behavior == 337, "state unchanged after re-pinning")

    # Qt recreating / re-flagging the window resets these (see the module docstring); re-pin restores.
    w.level_value, w.behavior, w.shadow, w.hides, w.can_hide = 8, (1 << 8) | (1 << 1), True, True, True
    check(pin_floating(pill) is True, "re-pin after Qt reset the window")
    check(w.level_value == 25 and w.behavior == 337 and w.shadow is False and w.hides is False,
          "level, behavior, shadow and hidesOnDeactivate are back")

print("--- 2b. pin_floating on a plain NSWindow ---")

mac = Mac(panel=False)
original_mask = mac.window.mask
with mac.active():
    check(pin_floating(pill) is True, "pin_floating is True for a non-panel window")
    w = mac.window
    check(w.mask == original_mask, "the style mask is untouched for a non-panel")
    check(not w.calls("setStyleMask_"), "setStyleMask_ is never called for a non-panel")
    check(w.level_value == 25 and w.behavior == 337, "level and collectionBehavior still applied")
    check(w.shadow is False and w.hides is False, "shadow and hidesOnDeactivate still applied")

print("--- 2c. window already non-activating ---")

mac = Mac(panel=True)
mac.window.mask |= 1 << 7
with mac.active():
    check(pin_floating(pill) is True, "pin_floating True")
    check(not mac.window.calls("setStyleMask_"), "a mask that already has the bit is not rewritten")


# ---- 3. failures ---------------------------------------------------------------

print("--- 3. AppKit failures never raise ---")

for name in ("setLevel_", "setCollectionBehavior_", "setHidesOnDeactivate_", "setCanHide_",
             "setHasShadow_", "setStyleMask_", "setFloatingPanel_", "setBecomesKeyOnlyIfNeeded_",
             "isKindOfClass_"):
    mac = Mac(panel=True)
    mac.window.fail.add(name)
    with mac.active():
        try:
            result = pin_floating(pill)
            raised = False
        except Exception:  # noqa: BLE001
            result, raised = None, True
    check(not raised and result is False, f"{name} raising -> False, no exception")
    if name != "setLevel_":
        check(mac.window.level_value == 25, f"{name} raising: the other steps (level) still ran")

mac = Mac(panel=True)
mac.window = None
with mac.active():
    check(pin_floating(pill) is False, "no NSWindow yet -> False")

mac = Mac(panel=True)
mac.window_lookup_fails = True
with mac.active():
    check(pin_floating(pill) is False, "objc failing to wrap the winId -> False")


# ---- 4. PillGlass --------------------------------------------------------------

print("--- 4. PillGlass with NSGlassEffectView (macOS 26+) ---")

FakeGlassView.created = FakeEffectView.created = 0
mac = Mac(panel=True)
with mac.active():
    glass = PillGlass.install(pill, 6, 18.0, True)
    check(isinstance(glass, PillGlass), "install returns a PillGlass")
    view = mac.frame_view.subviews[-1]
    check(isinstance(view, FakeGlassView), "NSGlassEffectView is used when lookUpClass finds it")
    check(FakeEffectView.created == 0, "no NSVisualEffectView was created")
    check(mac.frame_view.subviews == [mac.content, view] or mac.frame_view.subviews == [view, mac.content],
          "the glass is a subview of the content view's superview")
    add = mac.frame_view.calls("addSubview_positioned_relativeTo_")[-1]
    check(add[1] is view and add[2] == -1 and add[3] is mac.content,
          "added positioned NSWindowBelow, relative to Qt's content view")
    check(view.corner_radius == 18.0, "cornerRadius is the requested radius")
    check(view.frame_value.t() == (6, 6, 188, 48), "frame is the content bounds inset by the margin")
    check(view.autoresizing == (2 | 16), "autoresizing is width|height sizable")
    check(("setOpaque_", False) in mac.window.log, "window is made non-opaque")
    check(("setBackgroundColor_", "CLEAR") in mac.window.log, "window background is clear")
    check(view.appearance == ("appearance", "DarkAqua"), "dark=True pins the DarkAqua appearance")
    check(view.content_view is not None, "the glass gets a (transparent, empty) content view")

    glass.set_dark(False)
    check(view.appearance == ("appearance", "Aqua"), "set_dark(False) pins Aqua")

    mac.content.frame_value = Rect(0, 0, 200, 52)  # compact 36 -> expanded 52 style resize
    glass.set_geometry(4, 26.0)
    check(view.frame_value.t() == (4, 4, 192, 44), "set_geometry re-insets the frame")
    check(view.corner_radius == 26.0, "set_geometry updates the corner radius")

    glass.remove()
    check(view not in mac.frame_view.subviews, "remove() takes the glass out of the window")
    n = len(view.log)
    glass.remove()
    glass.set_geometry(2, 10.0)
    glass.set_dark(True)
    check(len(view.log) == n, "remove() twice / calls after remove() do nothing")

print("--- 4b. fallback to NSVisualEffectView (no NSGlassEffectView before macOS 26) ---")

FakeGlassView.created = FakeEffectView.created = 0
mac = Mac(panel=True, glass_class=None)
with mac.active():
    glass = PillGlass.install(pill, 6, 18.0, False)
    view = mac.frame_view.subviews[-1]
    check(isinstance(view, FakeEffectView), "NSVisualEffectView is used when NSGlassEffectView is missing")
    check(FakeGlassView.created == 0, "no NSGlassEffectView was created")
    check(view.material == 13, "material is HUD window (13)")
    check(view.blending == 0, "blending is behind-window (0)")
    check(view.state == 1, "state is active (1) -- the panel is never key")
    check(view.frame_value.t() == (6, 6, 188, 48), "frame is the content bounds inset by the margin")
    check(view.autoresizing == 18, "autoresizing is width|height sizable")
    add = mac.frame_view.calls("addSubview_positioned_relativeTo_")[-1]
    check(add[2] == -1 and add[3] is mac.content, "added below Qt's content view")
    check(view.appearance == ("appearance", "Aqua"), "dark=False pins Aqua")
    check(not hasattr(view, "corner_radius"), "rounding goes through maskImage, not cornerRadius")

    mask = view.mask_image
    check(isinstance(mask, FakeImage), "maskImage is set")
    check(mask.size == (37.0, 37.0), "mask is a (2r+1)-point square")
    check(mask.cap_insets == (18.0, 18.0, 18.0, 18.0), "mask cap insets are the radius (9-slice)")
    check(mask.resizing is STRETCH, "mask resizing mode is the symbolic NSImageResizingModeStretch")
    DRAWN.clear()
    check(mask.draw() is True and DRAWN == [((0, 0, 37.0, 37.0), 18.0, 18.0)],
          "the mask draws a rounded rect of that radius")

    mac.content.frame_value = Rect(0, 0, 200, 52)
    glass.set_geometry(4, 26.0)
    check(view.frame_value.t() == (4, 4, 192, 44), "set_geometry re-insets the frame")
    check(view.mask_image is not mask and view.mask_image.size == (53.0, 53.0)
          and view.mask_image.cap_insets == (26.0, 26.0, 26.0, 26.0),
          "set_geometry rebuilds the mask for the new radius")
    kept = view.mask_image
    glass.set_geometry(4, 26.0)
    check(view.mask_image is kept, "same radius keeps the existing mask")
    glass.set_geometry(4, 0)
    check(view.mask_image is None, "radius 0 removes the mask")
    glass.set_dark(True)
    check(view.appearance == ("appearance", "DarkAqua"), "set_dark(True) pins DarkAqua")
    glass.remove()
    check(view not in mac.frame_view.subviews, "remove() takes the view out of the window")

print("--- 4c. SAGHI_NO_GLASS ---")

mac = Mac(panel=True)
with mac.active(), patch.dict(os.environ, {"SAGHI_NO_GLASS": "1"}):
    check(PillGlass.install(pill, 6, 18.0, True) is None, "SAGHI_NO_GLASS=1 -> None")
    check(mac.frame_view.subviews == [mac.content], "nothing was added to the window")
    check(not mac.window.log, "the window was not touched")
    check(mac.pointers == [], "the native window was not even looked up")
with mac.active(), patch.dict(os.environ, {"SAGHI_NO_GLASS": ""}):
    check(PillGlass.install(pill, 6, 18.0, True) is not None, "an empty SAGHI_NO_GLASS does not disable it")

print("--- 4d. failures never raise and never leave a view behind ---")

mac = Mac(panel=True)
mac.window.fail_once.add("setOpaque_")  # fails after the glass view was added
with mac.active():
    glass = PillGlass.install(pill, 6, 18.0, True)
    check(glass is not None and isinstance(mac.frame_view.subviews[-1], FakeEffectView),
          "a glass attempt that fails half way falls back to NSVisualEffectView")
    check(len([v for v in mac.frame_view.subviews if v is not mac.content]) == 1,
          "the half-installed glass view was removed again")

mac = Mac(panel=True)
mac.window.fail.add("setOpaque_")
with mac.active():
    check(PillGlass.install(pill, 6, 18.0, True) is None, "every attempt failing -> None")
    check(mac.frame_view.subviews == [mac.content], "no view is left in the window")


class BrokenGlass(FakeGlassView):
    def setCornerRadius_(self, radius):
        raise RuntimeError("no such selector")


mac = Mac(panel=True, glass_class=BrokenGlass)
with mac.active():
    glass = PillGlass.install(pill, 6, 18.0, True)
    check(glass is not None and isinstance(mac.frame_view.subviews[-1], FakeEffectView),
          "an NSGlassEffectView that rejects cornerRadius falls back to NSVisualEffectView")

mac = Mac(panel=True)
mac.window = None
with mac.active():
    check(PillGlass.install(pill, 6, 18.0, True) is None, "no NSWindow yet -> None")

mac = Mac(panel=True)
mac.window_lookup_fails = True
with mac.active():
    check(PillGlass.install(pill, 6, 18.0, True) is None, "objc failing to wrap the winId -> None")

mac = Mac(panel=True)
mac.content.parent = None  # no frame view to add the glass to
with mac.active():
    check(PillGlass.install(pill, 6, 18.0, True) is None, "content view without a superview -> None")

mac = Mac(panel=True)
mac.window.content = None
with mac.active():
    check(PillGlass.install(pill, 6, 18.0, True) is None, "window without a content view -> None")

print(f"\nALL PASSED ({_checks} checks)")

"""
Native macOS behaviour for the always-visible status pill: a small frameless Qt
window that has to stay on screen while the user types in ANOTHER app.

  pin_floating()   makes the pill's NSWindow behave like a status-bar item: it
                   floats above every normal window, shows on every Space and
                   next to full-screen apps, is not hidden when Saghi is
                   inactive (or hidden with Cmd+H), never joins Cmd+` cycling
                   and never activates the app. Autopaste (paste.py) targets
                   the frontmost app, so the pill appearing must never change
                   which app that is.
  PillGlass        real system glass behind the pill's Qt content: NSGlassEffectView
                   on macOS 26+, an NSVisualEffectView (HUD material) before that,
                   placed the same way macos_glass.install_glass() does it for the
                   main window (behind Qt's content view, which stays transparent).

Everything is best effort, like macos_glass.py: another platform, no pyobjc or any
AppKit error means False / None and the pill simply keeps Qt's own look (flat,
translucent). All of it must run on the main thread.

WHEN TO CALL pin_floating() -- what Qt's cocoa plugin (qtbase 6.11,
plugins/platforms/cocoa/qcocoawindow.mm) rewrites behind our back:

  * A Qt::Tool window is an NSPanel (QNSPanel): Qt::Tool == Popup | Dialog and
    `shouldBePanel` tests both bits. So the panel-only settings below do apply.
  * createNSWindow() -- every time the native window is created, i.e. the first
    show()/winId()/create(), and again whenever QWidget.setWindowFlags()/
    setWindowFlag()/setParent() recreates it (that is the QEvent.WinIdChange) --
    sets level = windowLevel(flags) (NSModalPanelWindowLevel with
    WindowStaysOnTopHint), collectionBehavior = FullScreenAuxiliary |
    MoveToActiveSpace, hidesOnDeactivate = Tool && !WA_MacAlwaysShowToolWindow and
    hasShadow = YES.
  * QCocoaWindow::setWindowFlags() -- QWindow.setFlags() on an existing platform
    window, leaving full screen -- resets level and hasShadow (unless
    Qt.NoDropShadowWindowHint) and rewrites the style mask, but keeps the
    NonactivatingPanel bit and skips collectionBehavior for Popup/Dialog types.
  * show()/hide() (QCocoaWindow::setVisible) only orderFront:/orderOut: -- they
    change none of these. WindowDoesNotAcceptFocus makes canBecomeKeyWindow NO,
    so Qt never makeKey's the pill.
  * With WA_MacAlwaysShowToolWindow Qt registers an activate/deactivate observer
    that drops Tool windows to NSNormalWindowLevel while the app is inactive --
    but it only touches windows whose level is Normal or Floating, so a window at
    NSStatusWindowLevel is left alone. That is one more reason to pin the level.

So: call pin_floating(w) after every show() and on QEvent.WinIdChange; it is
idempotent and cheap (it only touches the style mask the first time, because
setStyleMask: may rebuild the window's view hierarchy). Call it BEFORE
PillGlass.install(), and after a WinIdChange discard the old PillGlass
(remove()) and install a new one -- the glass lives in the old NSWindow.

macOS numbers here come from the AppKit headers (verified against pyobjc's
generated SDK metadata); each constant carries its Apple name.
"""

from __future__ import annotations

import logging
import os
from typing import Callable

from . import macos_glass

logger = logging.getLogger("saghi.ui.macos_panel")

_STATUS_WINDOW_LEVEL = 25  # NSStatusWindowLevel (kCGStatusWindowLevel): above the menu bar's 24

_CB_CAN_JOIN_ALL_SPACES = 1 << 0  # NSWindowCollectionBehaviorCanJoinAllSpaces
_CB_STATIONARY = 1 << 4  # NSWindowCollectionBehaviorStationary (Mission Control leaves it alone)
_CB_IGNORES_CYCLE = 1 << 6  # NSWindowCollectionBehaviorIgnoresCycle (not in Cmd+` cycling)
_CB_FULL_SCREEN_AUXILIARY = 1 << 8  # NSWindowCollectionBehaviorFullScreenAuxiliary
# Assigned, never OR-ed onto Qt's value: Qt sets MoveToActiveSpace (1 << 1) on panels,
# which contradicts CanJoinAllSpaces ("follow the active Space" vs "be on every Space").
PILL_COLLECTION_BEHAVIOR = (
    _CB_CAN_JOIN_ALL_SPACES | _CB_STATIONARY | _CB_IGNORES_CYCLE | _CB_FULL_SCREEN_AUXILIARY
)  # == 337

_STYLE_NONACTIVATING_PANEL = 1 << 7  # NSWindowStyleMaskNonactivatingPanel

_SIZABLE = 2 | 16  # NSViewWidthSizable | NSViewHeightSizable
_WINDOW_BELOW = -1  # NSWindowBelow
_MATERIAL_HUD_WINDOW = 13  # NSVisualEffectMaterialHUDWindow (Popover is 6, Menu is 5)
_BLENDING_BEHIND_WINDOW = 0  # NSVisualEffectBlendingModeBehindWindow
# NSVisualEffectStateActive: the panel is never key, so "follows window active state" would dim it.
_STATE_ACTIVE = 1


def _attempt(label: str, action: Callable[[], object]) -> bool:
    try:
        action()
        return True
    except Exception:  # noqa: BLE001 -- any AppKit/bridge failure means "not pinned"
        logger.warning("Could not %s", label, exc_info=True)
        return False


def pin_floating(widget) -> bool:
    """Pin the widget's NSWindow as a status-level, all-Spaces, non-activating panel.

    Returns True when every step succeeded, False on another platform (a no-op),
    without a native window yet, or when any AppKit call failed (the remaining
    steps still run, so one missing selector cannot leave the pill unpinned).
    """
    if not macos_glass.platform_ok():
        return False
    try:
        import AppKit

        win = macos_glass._ns_window(widget)
        if win is None:
            return False
    except Exception:  # noqa: BLE001
        logger.warning("Could not reach the pill's NSWindow", exc_info=True)
        return False

    results: list[bool] = []
    is_panel = False
    try:
        is_panel = bool(win.isKindOfClass_(AppKit.NSPanel))
    except Exception:  # noqa: BLE001
        logger.warning("Could not tell whether the pill is an NSPanel", exc_info=True)
        results.append(False)

    steps: list[tuple[str, Callable[[], object]]] = []
    if is_panel:
        def add_nonactivating() -> None:
            mask = int(win.styleMask())
            if not mask & _STYLE_NONACTIVATING_PANEL:  # setStyleMask: may rebuild the view tree
                win.setStyleMask_(mask | _STYLE_NONACTIVATING_PANEL)

        steps += [
            ("mark the pill non-activating", add_nonactivating),
            # Never raises (see _prevent_activation), so it never fails the pin.
            ("tag the pill as never activating Saghi", lambda: _prevent_activation(win)),
            ("make the pill a floating panel", lambda: win.setFloatingPanel_(True)),
            ("keep the pill from becoming key", lambda: win.setBecomesKeyOnlyIfNeeded_(True)),
        ]
    steps += [
        ("set the pill's Spaces behaviour", lambda: win.setCollectionBehavior_(PILL_COLLECTION_BEHAVIOR)),
        ("keep the pill when Saghi deactivates", lambda: win.setHidesOnDeactivate_(False)),
        ("keep the pill when Saghi is hidden", lambda: win.setCanHide_(False)),
        ("drop the pill's window shadow", lambda: win.setHasShadow_(False)),
        # Last: NSPanel setFloatingPanel: also sets the window level (floating/normal).
        ("raise the pill to the status level", lambda: win.setLevel_(_STATUS_WINDOW_LEVEL)),
    ]
    results += [_attempt(label, action) for label, action in steps]
    return all(results)


def _prevent_activation(win) -> None:
    """
    Adding the NonactivatingPanel bit after the panel exists (Qt never sets
    it at creation) updates AppKit's flag but, per reports on NSPanel
    internals, not the window server's "prevents activation" tag, which
    NSPanel only sets while it initialises. The private setter applies that
    tag now. Best effort: skipped when the selector is missing and never
    counted as a pinning failure.
    """
    try:
        if win.respondsToSelector_(b"_setPreventsActivation:"):
            win._setPreventsActivation_(True)
    except Exception:  # noqa: BLE001
        logger.debug("Could not set the pill's prevents-activation tag", exc_info=True)


def _inset_frame(content, margin: float):
    """The window's content rect (in its frame view's coordinates) inset by `margin`."""
    import AppKit

    frame = content.frame()
    return AppKit.NSMakeRect(
        frame.origin.x + margin,
        frame.origin.y + margin,
        max(0.0, frame.size.width - 2 * margin),
        max(0.0, frame.size.height - 2 * margin),
    )


def _rounded_mask(radius: float):
    """Resizable rounded-rect alpha mask for NSVisualEffectView.maskImage.

    A (2r+1)-point square with r-point cap insets stretches to any size with the
    corners intact -- the same 9-slice trick Electron uses for its frameless
    vibrancy windows. layer.cornerRadius is NOT used: a behind-window effect view
    is composited by the window server, and Apple documents maskImage (which is
    also applied to the window's shadow) as the way to shape it.
    """
    import AppKit

    r = float(radius)
    if r <= 0:
        return None
    side = 2 * r + 1

    def draw(rect) -> bool:
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, r, r).fill()
        return True

    image = AppKit.NSImage.imageWithSize_flipped_drawingHandler_(AppKit.NSMakeSize(side, side), False, draw)
    image.setCapInsets_(AppKit.NSEdgeInsetsMake(r, r, r, r))
    # Symbolic, not a literal: NSImageResizingModeStretch is 0 on Intel but 1 on arm64.
    image.setResizingMode_(AppKit.NSImageResizingModeStretch)
    return image


def _glass_class():
    import objc

    try:
        return objc.lookUpClass("NSGlassEffectView")  # macOS 26+; pyobjc may not export it
    except Exception:  # noqa: BLE001 -- objc.nosuchclass_error on older systems
        return None


def _make_glass_view(frame, radius: float):
    import AppKit

    cls = _glass_class()
    if cls is None:
        raise RuntimeError("NSGlassEffectView is not available")
    view = cls.alloc().initWithFrame_(frame)
    view.setCornerRadius_(float(radius))
    try:
        # The glass is only documented with a contentView; an empty one costs nothing.
        view.setContentView_(AppKit.NSView.alloc().initWithFrame_(frame))
    except Exception:  # noqa: BLE001
        logger.debug("NSGlassEffectView without a content view", exc_info=True)
    return view


def _make_effect_view(frame, radius: float):
    import AppKit

    view = AppKit.NSVisualEffectView.alloc().initWithFrame_(frame)
    view.setMaterial_(_MATERIAL_HUD_WINDOW)
    view.setBlendingMode_(_BLENDING_BEHIND_WINDOW)
    view.setState_(_STATE_ACTIVE)
    view.setMaskImage_(_rounded_mask(radius))
    return view


class PillGlass:
    """System glass behind the pill (see the module docstring); build with install()."""

    def __init__(self, window, view, is_glass: bool, margin: float, radius: float) -> None:
        self._window = window
        self._view = view
        self._is_glass = is_glass
        self._margin = margin
        self._radius = radius

    @classmethod
    def install(cls, widget, margin: int, radius: float, dark: bool) -> "PillGlass | None":
        """Put glass behind the widget's Qt content, `margin` points inside the window.

        Tries NSGlassEffectView, then NSVisualEffectView. None on another
        platform, with SAGHI_NO_GLASS set (like macos_glass.wants_glass), when the
        widget has no native window yet, or on any failure -- never raises.
        """
        if os.environ.get("SAGHI_NO_GLASS") or not macos_glass.platform_ok():
            return None
        try:
            import AppKit

            win = macos_glass._ns_window(widget)
            if win is None:
                return None
            content = win.contentView()
            frame_view = content.superview() if content is not None else None
            if frame_view is None:
                return None
            frame = _inset_frame(content, margin)

            makers = [(_make_effect_view, False)]
            if _glass_class() is not None:
                makers.insert(0, (_make_glass_view, True))
            for make, is_glass in makers:
                view = None
                try:
                    view = make(frame, radius)
                    view.setAutoresizingMask_(_SIZABLE)
                    # Behind Qt's own view, which paints only the pill's content.
                    frame_view.addSubview_positioned_relativeTo_(view, _WINDOW_BELOW, content)
                    win.setOpaque_(False)
                    win.setBackgroundColor_(AppKit.NSColor.clearColor())
                except Exception:  # noqa: BLE001
                    logger.warning("Could not install the pill glass (%s)", make.__name__, exc_info=True)
                    if view is not None:
                        _attempt("remove a half-installed glass view", view.removeFromSuperview)
                    continue
                pill = cls(win, view, is_glass, margin, radius)
                pill.set_dark(dark)
                return pill
            return None
        except Exception:  # noqa: BLE001
            logger.warning("Could not install the pill glass", exc_info=True)
            return None

    def set_geometry(self, margin: int, radius: float) -> None:
        """Re-fit after the pill resized (compact <-> expanded): new inset and corner radius."""
        if self._view is None:
            return
        try:
            content = self._window.contentView()
            if content is not None:
                self._view.setFrame_(_inset_frame(content, margin))
            if radius != self._radius:
                if self._is_glass:
                    self._view.setCornerRadius_(float(radius))
                else:
                    self._view.setMaskImage_(_rounded_mask(radius))
            self._margin = margin
            self._radius = radius
        except Exception:  # noqa: BLE001
            logger.warning("Could not resize the pill glass", exc_info=True)

    def set_dark(self, dark: bool) -> None:
        """Best-effort light/dark hint: pins the effect view's NSAppearance to Aqua or DarkAqua."""
        if self._view is None:
            return
        try:
            import AppKit

            name = AppKit.NSAppearanceNameDarkAqua if dark else AppKit.NSAppearanceNameAqua
            self._view.setAppearance_(AppKit.NSAppearance.appearanceNamed_(name))
        except Exception:  # noqa: BLE001
            logger.debug("Could not set the pill glass appearance", exc_info=True)

    def remove(self) -> None:
        """Take the glass out of the window; safe to call twice or after the window is gone."""
        view, self._view = self._view, None
        if view is not None:
            _attempt("remove the pill glass", view.removeFromSuperview)

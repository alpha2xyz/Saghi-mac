"""
Native macOS window chrome for the main window, the way current macOS apps
(Finder, System Settings) look:

  merge_titlebar()  the title bar becomes transparent and the content runs
                    up underneath it (NSWindowStyleMaskFullSizeContentView),
                    so the traffic-light buttons sit on the window itself.
  install_glass()   a real NSVisualEffectView ("Sidebar" material -- the
                    macOS Liquid Glass sidebar, updated by the OS itself for
                    light/dark and focus) behind the Qt content. Qt then
                    leaves the sidebar unpainted so the glass shows through.

Both go through pyobjc's AppKit bridge, which the app already ships for
paste.py. Everything is best effort: each function returns False (and
changes nothing visible) on another platform, without pyobjc, or on any
AppKit error -- the window then keeps its normal title bar and a solid
sidebar. Settings has a switch to turn the glass off (next launch), and
SAGHI_NO_GLASS=1 turns it off too.

ORDER MATTERS for the glass: `wants_glass()` + WA_TranslucentBackground
must be decided before the native window exists (Qt picks an alpha-capable
surface at creation); `install_glass()` runs after, on the created window.
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger("saghi.ui.macos_glass")

_FULL_SIZE_CONTENT_VIEW = 1 << 15  # NSWindowStyleMaskFullSizeContentView
_TITLE_HIDDEN = 1  # NSWindowTitleHidden
_MATERIAL_SIDEBAR = 7  # NSVisualEffectMaterialSidebar
_BLENDING_BEHIND_WINDOW = 0  # NSVisualEffectBlendingModeBehindWindow
_STATE_FOLLOWS_WINDOW = 0  # NSVisualEffectStateFollowsWindowActiveState
_WINDOW_BELOW = -1  # NSWindowBelow
_SIZABLE = 2 | 16  # NSViewWidthSizable | NSViewHeightSizable

# Title bar height the merged content has to keep clear of the traffic lights.
TITLEBAR_INSET = 28


def platform_ok() -> bool:
    if sys.platform != "darwin":
        return False
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return False
    try:
        import AppKit  # noqa: F401
        import objc  # noqa: F401
    except ImportError:
        return False
    return True


def wants_glass(enabled_in_settings: bool) -> bool:
    """Decide (before the native window exists) whether to try the real glass."""
    return enabled_in_settings and not os.environ.get("SAGHI_NO_GLASS") and platform_ok()


def _ns_window(widget):
    import objc

    view = objc.objc_object(c_void_p=int(widget.winId()))
    return view.window()


def merge_titlebar(widget) -> bool:
    if not platform_ok():
        return False
    try:
        win = _ns_window(widget)
        if win is None:
            return False
        win.setTitlebarAppearsTransparent_(True)
        win.setTitleVisibility_(_TITLE_HIDDEN)
        win.setStyleMask_(win.styleMask() | _FULL_SIZE_CONTENT_VIEW)
        return True
    except Exception:  # noqa: BLE001 -- any AppKit/bridge failure means "keep the normal title bar"
        logger.warning("Could not merge the title bar", exc_info=True)
        return False


def install_glass(widget) -> bool:
    if not platform_ok():
        return False
    try:
        import AppKit

        win = _ns_window(widget)
        if win is None:
            return False
        content = win.contentView()
        frame_view = content.superview()
        if frame_view is None:
            return False
        effect = AppKit.NSVisualEffectView.alloc().initWithFrame_(frame_view.bounds())
        effect.setMaterial_(_MATERIAL_SIDEBAR)
        effect.setBlendingMode_(_BLENDING_BEHIND_WINDOW)
        effect.setState_(_STATE_FOLLOWS_WINDOW)
        effect.setAutoresizingMask_(_SIZABLE)
        # Behind Qt's own view: where Qt paints nothing (the sidebar), the
        # glass shows; everywhere else Qt's solid content covers it.
        frame_view.addSubview_positioned_relativeTo_(effect, _WINDOW_BELOW, content)
        win.setOpaque_(False)
        win.setBackgroundColor_(AppKit.NSColor.clearColor())
        win.invalidateShadow()
        return True
    except Exception:  # noqa: BLE001
        logger.warning("Could not install the sidebar glass", exc_info=True)
        return False

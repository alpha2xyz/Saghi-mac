"""
Clipboard + auto-paste for live dictation (README_AR.md: "لصق تلقائي في
التطبيق النشط، مع نسخ احتياطي إلى الحافظة" -- auto-paste into the active
app, with a clipboard copy as backup).

`paste_text()` ALWAYS puts the text on the system clipboard first (via
AppKit's `NSPasteboard`, not Qt's `QClipboard` -- this module has zero Qt
dependency by design, same convention as recorder.py/hotkey.py/settings.py,
so it works from any caller including the unit tests, which run this module
without ever constructing a `QApplication`). If `autopaste` is requested, it
then synthesizes a Cmd+V keystroke into whichever app is currently
frontmost, via Quartz's low-level `CGEventCreateKeyboardEvent`/
`CGEventPost` (kVK_ANSI_V=9 with the Command modifier flag, key-down then
key-up).

TCC note: posting a synthetic keyboard event system-wide requires the
Accessibility permission grant for the process that's actually running this
code (this needs a manual check on a real Mac). If that grant is missing,
`CGEventPost` does NOT raise -- the call just silently has no effect on the
target app. `paste_text()` therefore can only report whether the CGEvent
calls themselves completed without raising (`PasteResult.pasted`), not
whether the paste was actually delivered/applied anywhere -- that is not
observable from inside this process. The clipboard copy (`clipboard_set`)
is the one thing this module CAN verify, and per the documented behavior
contract it is also the user's real fallback path when autopaste is
silently blocked by a missing Accessibility grant.

Restoring whatever was on the clipboard before this call is explicitly OUT
of scope -- the original Saghi app keeps the dictated text on the clipboard
as a documented feature, not a bug to fix.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger("saghi.paste")

# kVK_ANSI_V, from Carbon's HIToolbox/Events.h virtual keycode table --
# the physical "V" key position, independent of the active keyboard layout
# (Cmd+V is a position-based shortcut on macOS, not a character-based one).
_KVK_ANSI_V = 0x09

# Small delays: one between setting the clipboard and posting the paste
# keystroke (give the pasteboard server a moment to settle before something
# reads it back), and one between the synthetic key-down and key-up (a
# same-instant down+up pair is more likely to be dropped/coalesced by the
# receiving app than a real keypress would be).
_CLIPBOARD_SETTLE_DELAY_S = 0.05
_KEY_DOWN_UP_DELAY_S = 0.01


@dataclass
class PasteResult:
    clipboard_set: bool
    # True only if the CGEvent post calls completed without raising -- NOT
    # proof the frontmost app actually received/applied the paste (see
    # module docstring: that requires the Accessibility grant and is not
    # independently observable from this process).
    pasted: bool


def accessibility_trusted(prompt: bool = False) -> bool:
    """
    Whether macOS lets this process post synthetic key events (Accessibility).

    With prompt=True and no permission yet, macOS shows its standard dialog
    ("Saghi would like to control this computer") and adds Saghi to
    System Settings > Privacy & Security > Accessibility, so the user only
    has to switch it on. Without this permission Cmd+V is silently dropped.
    Returns True when the check is unavailable (non-macOS, tests).
    """
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )
    except Exception:
        return True
    try:
        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: bool(prompt)}))
    except Exception:
        logger.exception("Accessibility check failed")
        return True


def set_clipboard(text: str) -> bool:
    """Put `text` on the system clipboard via AppKit's NSPasteboard. Returns whether it succeeded."""
    try:
        import AppKit

        pb = AppKit.NSPasteboard.generalPasteboard()
        pb.clearContents()
        ok = pb.setString_forType_(text, AppKit.NSPasteboardTypeString)
        return bool(ok)
    except Exception:
        logger.exception("Failed to set clipboard via NSPasteboard")
        return False


def read_clipboard() -> str:
    """Read the current clipboard string back via NSPasteboard. Empty string on any failure. Test/debug helper."""
    try:
        import AppKit

        pb = AppKit.NSPasteboard.generalPasteboard()
        value = pb.stringForType_(AppKit.NSPasteboardTypeString)
        return value or ""
    except Exception:
        logger.exception("Failed to read clipboard via NSPasteboard")
        return ""


def _post_cmd_v() -> bool:
    """Synthesize Cmd+V into whatever app is frontmost. Returns True iff the CGEvent calls completed without raising."""
    try:
        import Quartz

        flags = Quartz.kCGEventFlagMaskCommand

        down = Quartz.CGEventCreateKeyboardEvent(None, _KVK_ANSI_V, True)
        Quartz.CGEventSetFlags(down, flags)
        up = Quartz.CGEventCreateKeyboardEvent(None, _KVK_ANSI_V, False)
        Quartz.CGEventSetFlags(up, flags)

        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        time.sleep(_KEY_DOWN_UP_DELAY_S)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
        return True
    except Exception:
        logger.exception("Failed to post synthetic Cmd+V event")
        return False


def paste_text(text: str, autopaste: bool = True) -> PasteResult:
    """
    Always sets the clipboard first. If `autopaste` and the clipboard set
    succeeded, also synthesizes Cmd+V into the frontmost app. See module
    docstring for what `PasteResult.pasted` does and does not prove.
    """
    clipboard_set = set_clipboard(text)

    pasted = False
    if autopaste and clipboard_set:
        time.sleep(_CLIPBOARD_SETTLE_DELAY_S)
        pasted = _post_cmd_v()

    return PasteResult(clipboard_set=clipboard_set, pasted=pasted)

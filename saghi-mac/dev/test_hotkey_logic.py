#!/usr/bin/env python3
"""
Drives saghi.hotkey.HotkeyStateMachine directly with synthetic
pynput.keyboard.Key values -- no real Listener, no real keyboard, fully
deterministic. This tests the pure state machine (see hotkey.py's module
docstring for why it's separated from HotkeyListener): full-combo detection
including left/right key variants, macOS key-repeat debounce, Esc-cancel
and its swallowed release, and release-order permutations.

Run:
    PYTHONPATH=. <venv>/bin/python dev/test_hotkey_logic.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pynput.keyboard import Key  # noqa: E402

from saghi.hotkey import HotkeyStateMachine  # noqa: E402

_checks = 0


def check(condition: bool, message: str) -> None:
    global _checks
    assert condition, f"FAILED: {message}"
    _checks += 1
    print(f"ok: {message}")


class Recorder:
    """Collects which callbacks fired, in order."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def make_sm(self, combo: str = "ctrl+cmd") -> HotkeyStateMachine:
        return HotkeyStateMachine(
            combo=combo,
            on_hold_started=lambda: self.events.append("hold_started"),
            on_hold_ended=lambda: self.events.append("hold_ended"),
            on_cancelled=lambda: self.events.append("cancelled"),
        )


# ---- 1. basic full-combo detection (ctrl+cmd) -----------------------------

rec = Recorder()
sm = rec.make_sm("ctrl+cmd")
sm.handle_press(Key.ctrl)
check(rec.events == [], "no event after only one of two combo keys pressed")
sm.handle_press(Key.cmd)
check(rec.events == ["hold_started"], "hold_started fires once both combo keys are down")
check(sm.is_holding is True, "is_holding is True while both keys are held")

sm.handle_release(Key.ctrl)
check(rec.events == ["hold_started", "hold_ended"], "hold_ended fires when the first key of the combo releases")
check(sm.is_holding is False, "is_holding is False after hold_ended")

# ---- 2. left/right key variants are normalized ----------------------------

rec = Recorder()
sm = rec.make_sm("ctrl+cmd")
sm.handle_press(Key.ctrl_l)
sm.handle_press(Key.cmd_r)
check(rec.events == ["hold_started"], "left/right variants (ctrl_l + cmd_r) count as the combo")
sm.handle_release(Key.cmd_r)
sm.handle_release(Key.ctrl_l)
check(rec.events == ["hold_started", "hold_ended"], "left/right variant releases end the hold cleanly")

# generic + specific variant of the SAME modifier should also satisfy the combo
rec = Recorder()
sm = rec.make_sm("ctrl+cmd")
sm.handle_press(Key.ctrl_r)
sm.handle_press(Key.cmd)
check(rec.events == ["hold_started"], "ctrl_r + generic cmd also satisfies the combo")

# ---- 3. macOS key-repeat debounce: re-press of an already-held key -------

rec = Recorder()
sm = rec.make_sm("ctrl+cmd")
sm.handle_press(Key.ctrl)
sm.handle_press(Key.cmd)
sm.handle_press(Key.cmd)  # OS key-repeat firing on_press again for an already-held key
sm.handle_press(Key.ctrl)  # same for the other key
check(rec.events == ["hold_started"], f"repeated on_press for already-held keys does not re-fire hold_started (got {rec.events})")

# ---- 4. release-order permutations ----------------------------------------

for order in [("ctrl", "cmd"), ("cmd", "ctrl")]:
    rec = Recorder()
    sm = rec.make_sm("ctrl+cmd")
    sm.handle_press(Key.ctrl)
    sm.handle_press(Key.cmd)
    key_map = {"ctrl": Key.ctrl, "cmd": Key.cmd}
    sm.handle_release(key_map[order[0]])
    check(rec.events == ["hold_started", "hold_ended"], f"release order {order}: hold_ended fires on first release")
    sm.handle_release(key_map[order[1]])
    check(rec.events == ["hold_started", "hold_ended"], f"release order {order}: second release is a no-op (already ended)")

# ---- 5. Esc-cancel while holding, and swallowed subsequent release --------

rec = Recorder()
sm = rec.make_sm("ctrl+cmd")
sm.handle_press(Key.ctrl)
sm.handle_press(Key.cmd)
sm.handle_press(Key.esc)
check(rec.events == ["hold_started", "cancelled"], "Esc while holding fires cancelled")
check(sm.is_holding is False, "is_holding is False immediately after cancel")

sm.handle_release(Key.esc)  # swallowed -- no state change
sm.handle_release(Key.ctrl)
sm.handle_release(Key.cmd)
check(
    rec.events == ["hold_started", "cancelled"],
    f"releasing the combo keys after a cancel does NOT fire hold_ended (got {rec.events})",
)

# ---- 6. Esc debounce: repeated Esc press during the same hold fires cancelled once

rec = Recorder()
sm = rec.make_sm("ctrl+cmd")
sm.handle_press(Key.ctrl)
sm.handle_press(Key.cmd)
sm.handle_press(Key.esc)
sm.handle_press(Key.esc)  # key-repeat
sm.handle_press(Key.esc)  # key-repeat again
check(rec.events == ["hold_started", "cancelled"], f"repeated Esc presses during one hold fire cancelled only once (got {rec.events})")

# Esc pressed while NOT holding must do nothing.
rec = Recorder()
sm = rec.make_sm("ctrl+cmd")
sm.handle_press(Key.esc)
check(rec.events == [], "Esc pressed while not holding the combo does nothing")

# ---- 7. after a cancelled hold, a fresh press cycle works normally --------

rec = Recorder()
sm = rec.make_sm("ctrl+cmd")
sm.handle_press(Key.ctrl)
sm.handle_press(Key.cmd)
sm.handle_press(Key.esc)
sm.handle_release(Key.ctrl)
sm.handle_release(Key.cmd)
check(rec.events == ["hold_started", "cancelled"], "sanity: still just started+cancelled before the next cycle")
sm.handle_press(Key.ctrl)
sm.handle_press(Key.cmd)
check(rec.events == ["hold_started", "cancelled", "hold_started"], "a new press cycle after a cancel fires hold_started again")
sm.handle_release(Key.ctrl)
check(
    rec.events == ["hold_started", "cancelled", "hold_started", "hold_ended"],
    "and that new cycle ends normally with hold_ended (not swallowed)",
)

# ---- 8. after a normal (non-cancelled) hold_ended, a new cycle works too --

rec = Recorder()
sm = rec.make_sm("ctrl+cmd")
sm.handle_press(Key.ctrl)
sm.handle_press(Key.cmd)
sm.handle_release(Key.cmd)
sm.handle_press(Key.cmd)
check(rec.events == ["hold_started", "hold_ended", "hold_started"], "re-pressing after a normal release starts a fresh hold")

# ---- 9. the alt+cmd combo works the same way -------------------------------

rec = Recorder()
sm = rec.make_sm("alt+cmd")
sm.handle_press(Key.alt_l)
sm.handle_press(Key.cmd_l)
check(rec.events == ["hold_started"], "alt+cmd combo (left variants) detected")
sm.handle_release(Key.alt_l)
check(rec.events == ["hold_started", "hold_ended"], "alt+cmd combo ends on releasing alt")

# ctrl+cmd combo keys must NOT satisfy the alt+cmd combo.
rec = Recorder()
sm = rec.make_sm("alt+cmd")
sm.handle_press(Key.ctrl)
sm.handle_press(Key.cmd)
check(rec.events == [], "ctrl+cmd keys do not satisfy an alt+cmd-configured state machine")

# ---- 10. keys outside the combo, pressed before the hold, are ignored ----

rec = Recorder()
sm = rec.make_sm("ctrl+cmd")
sm.handle_press(Key.space)
sm.handle_press(Key.ctrl)
sm.handle_press(Key.shift)  # an extra modifier outside ctrl+cmd
sm.handle_press(Key.cmd)
check(rec.events == ["hold_started"], f"irrelevant keys (space/shift) never affect the state machine (got {rec.events})")
sm.handle_release(Key.space)
check(sm.is_holding is True, "releasing an irrelevant key does not end the hold")

# ---- 11. set_combo() resets state cleanly ----------------------------------

rec = Recorder()
sm = rec.make_sm("ctrl+cmd")
sm.handle_press(Key.ctrl)
sm.handle_press(Key.cmd)
check(sm.is_holding is True, "sanity: holding before combo switch")
sm.set_combo("alt+cmd")
check(sm.is_holding is False, "set_combo() resets is_holding")
check(sm.combo == "alt+cmd", "set_combo() updates the reported combo")
sm.handle_press(Key.alt)
sm.handle_press(Key.cmd)
check(rec.events[-1] == "hold_started", "the new combo works immediately after set_combo()")

# ---- 12. invalid combo name rejected ---------------------------------------

try:
    HotkeyStateMachine(combo="fn+cmd")
    check(False, "constructing with an unsupported combo name should raise")
except ValueError:
    check(True, "constructing with an unsupported combo name raises ValueError")

# ---- 13. the newer presets (shift+cmd, ctrl+alt, ctrl+shift) --------------

for combo, first, second in (
    ("shift+cmd", Key.shift_r, Key.cmd_l),
    ("ctrl+alt", Key.alt_l, Key.ctrl_r),
    ("ctrl+shift", Key.shift_l, Key.ctrl),
):
    rec = Recorder()
    sm = rec.make_sm(combo)
    sm.handle_press(first)
    check(rec.events == [], f"{combo}: no event after only one key")
    sm.handle_press(second)
    check(rec.events == ["hold_started"], f"{combo}: hold_started once both keys are down")
    sm.handle_release(first)
    check(rec.events == ["hold_started", "hold_ended"], f"{combo}: hold_ended when one key lifts")

# ---- 14. another key pressed mid-hold cancels (it was a shortcut) ---------

rec = Recorder()
sm = rec.make_sm("ctrl+cmd")
sm.handle_press(Key.ctrl)
sm.handle_press(Key.cmd)
sm.handle_press(Key.space)  # Ctrl+Cmd+Space = macOS emoji picker
check(rec.events == ["hold_started", "cancelled"], f"a non-modifier key mid-hold cancels (got {rec.events})")
check(sm.is_holding is False, "is_holding drops to False after the shortcut-cancel")
sm.handle_press(Key.space)  # key repeat
check(rec.events == ["hold_started", "cancelled"], "a repeated extra key does not cancel twice")
sm.handle_release(Key.space)
sm.handle_release(Key.cmd)
sm.handle_release(Key.ctrl)
check(rec.events == ["hold_started", "cancelled"], "the combo's release after a shortcut-cancel is swallowed")
sm.handle_press(Key.ctrl)
sm.handle_press(Key.cmd)
check(rec.events[-1] == "hold_started", "a fresh hold works normally after a shortcut-cancel")

print(f"\nALL PASSED ({_checks} checks)")

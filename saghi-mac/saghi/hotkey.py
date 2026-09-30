"""
Global press-and-HOLD hotkey detection (the Mac
presets are the two-modifier combos in `COMBOS` below -- Ctrl+Cmd, Alt+Cmd,
Shift+Cmd, Ctrl+Alt, Ctrl+Shift -- per `settings.VALID_HOTKEYS`; fn is not
reliably hookable, so it is never offered in `settings_page.py`).

Two pieces, deliberately separated:

  1. `HotkeyStateMachine` -- pure Python, ZERO pynput/Qt/thread dependency.
     Consumes normalized press/release events and decides when the whole
     combo transitions held/not-held, when Esc cancels a hold, and swallows
     macOS's key-repeat noise. This is what `dev/test_hotkey_logic.py`
     drives directly with synthetic `pynput.keyboard.Key` values -- no real
     listener, no real keyboard, fully deterministic.

  2. `HotkeyListener` -- the real thing: wraps `pynput.keyboard.Listener`
     and feeds its press/release callbacks into a `HotkeyStateMachine`.

Semantics (combo = a set of modifier "families", e.g. {"ctrl", "cmd"}):

  - ALL keys of the combo down (in any order, any left/right variant)  ->
    `hold_started` fires once.
  - ANY one of them lifts -> `hold_ended` fires once (unless the hold was
    cancelled -- see below), and the state resets so a fresh press cycle
    can fire `hold_started` again.
  - `Esc` pressed while holding -> `cancelled` fires once, and the eventual
    release of the modifier keys is swallowed (no `hold_ended` for a
    cancelled hold). A repeated/auto-repeated Esc press during the same
    hold does NOT fire `cancelled` a second time.
  - Any other (non-modifier) key pressed while holding cancels the same
    way as Esc: the user is typing a shortcut that shares our modifiers
    (Ctrl+Cmd+Space, Shift+Cmd+4, ...), not dictating.
  - Debounce: macOS's own key-repeat re-fires `on_press` for a key that's
    already held (both plain keys and, per the task's explicit warning,
    can happen for modifiers too on some setups). Set-membership updates
    are naturally idempotent (adding an already-present element is a
    no-op), so this needs no separate repeat-suppression logic beyond the
    "did the required set membership actually change" checks already in
    `handle_press`/`handle_release`.

THREADING (see `HotkeyListener`'s docstring for detail): `pynput.keyboard
.Listener` invokes its callbacks on its own background thread, NOT the Qt
main thread. `HotkeyListener`'s `on_hold_started`/`on_hold_ended`/
`on_cancelled` constructor callbacks therefore also run on that thread.
This module has zero Qt dependency by design (same convention as
recorder.py/settings.py/engine.py) -- the caller (`ui/dictation.py`'s
`DictationController`, a `QObject`) is responsible for marshaling these
calls onto the Qt main thread, which it does by having its callbacks do
nothing but `Signal.emit()` -- Qt's own signal/slot machinery auto-queues
delivery to whatever thread the connected slot's receiver object lives on
(the same pattern Phase 4's `FileJobWorker` QThread already relies on for
its `progress`/`finished_ok`/`failed` signals), so no manual lock/queue is
needed here.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, FrozenSet, Optional

logger = logging.getLogger("saghi.hotkey")

# Mirrors settings_page.py's _HOTKEY_PRESETS -- duplicated as a literal
# (not imported) since settings.py/settings_page.py are the source of truth
# for what a user can *pick*, while this module is the source of truth for
# what each preset *means* in terms of pynput key families. Keeping the
# combo *definitions* here (not in settings.py) avoids giving settings.py a
# pynput dependency it otherwise has no reason to need.
COMBOS: Dict[str, FrozenSet[str]] = {
    "ctrl+cmd": frozenset({"ctrl", "cmd"}),
    "alt+cmd": frozenset({"alt", "cmd"}),
    "shift+cmd": frozenset({"shift", "cmd"}),
    "ctrl+alt": frozenset({"ctrl", "alt"}),
    "ctrl+shift": frozenset({"ctrl", "shift"}),
}

DEFAULT_COMBO = "ctrl+cmd"


def _build_normalize_map() -> dict:
    """
    Maps every left/right/generic pynput Key variant of cmd/ctrl/alt to one
    normalized family name. Built lazily (inside a function, not at module
    import time) so this module can be imported -- and HotkeyStateMachine
    unit-tested -- even in an environment where pynput itself is not
    installed, as long as the caller never constructs a real
    HotkeyListener. `dev/test_hotkey_logic.py` imports pynput directly (to
    build synthetic Key values), so this is a belt-and-suspenders design
    choice, not something that path currently relies on.
    """
    from pynput import keyboard

    return {
        keyboard.Key.cmd: "cmd",
        keyboard.Key.cmd_l: "cmd",
        keyboard.Key.cmd_r: "cmd",
        keyboard.Key.ctrl: "ctrl",
        keyboard.Key.ctrl_l: "ctrl",
        keyboard.Key.ctrl_r: "ctrl",
        keyboard.Key.alt: "alt",
        keyboard.Key.alt_l: "alt",
        keyboard.Key.alt_r: "alt",
        keyboard.Key.shift: "shift",
        keyboard.Key.shift_l: "shift",
        keyboard.Key.shift_r: "shift",
    }


def _is_esc(key) -> bool:
    from pynput import keyboard

    return key == keyboard.Key.esc


class HotkeyStateMachine:
    """
    Pure combo-hold state machine -- see module docstring for the full
    semantics. Feed it normalized pynput `Key` values via `handle_press()`/
    `handle_release()`; it calls the three optional callbacks
    synchronously, on whatever thread called handle_press/handle_release.
    """

    def __init__(
        self,
        combo: str = DEFAULT_COMBO,
        on_hold_started: Optional[Callable[[], None]] = None,
        on_hold_ended: Optional[Callable[[], None]] = None,
        on_cancelled: Optional[Callable[[], None]] = None,
    ) -> None:
        self.on_hold_started = on_hold_started
        self.on_hold_ended = on_hold_ended
        self.on_cancelled = on_cancelled
        self._normalize = _build_normalize_map()
        self._held: set = set()
        self._holding = False
        self._cancelled_this_hold = False
        self.set_combo(combo)

    def set_combo(self, combo: str) -> None:
        if combo not in COMBOS:
            raise ValueError(f"Unknown hotkey combo: {combo!r} (expected one of {sorted(COMBOS)})")
        self._combo_name = combo
        self._required = COMBOS[combo]
        # Changing the combo mid-hold would leave stale state; the
        # documented call pattern (ui/dictation.py) only calls set_combo()
        # when no hold is in progress, but reset defensively regardless.
        #
        # NOT LOCKED against a concurrent handle_press()/handle_release()
        # call from the pynput listener thread -- ui/dictation.py's
        # DictationController._on_settings_changed() calls this on the Qt
        # main thread (in response to a settings-page edit) while
        # handle_press/handle_release run on the listener thread, so this
        # is a genuine unsynchronized cross-thread field mutation. Left
        # undocumented-as-a-non-issue would be wrong; the actual risk
        # assessment: CPython's GIL makes each individual attribute
        # assignment/set-mutation atomic, so the worst realistic outcome is
        # a stale `_holding`/`_held` read producing one spurious
        # `hold_ended` (or a missed one) right at the moment the combo is
        # changed -- and DictationController's own `_busy`/
        # `_recording_active` guards absorb a stray extra callback without
        # misbehaving. Practically very hard to hit in the first place
        # (changing the hotkey combo requires the Settings page to be
        # focused, which is not the same moment as holding the hotkey to
        # dictate). Deliberately NOT adding a lock for this -- documenting
        # the race and its bounded blast radius instead, per an advisor
        # review that (correctly) flagged the omission as inconsistent
        # with how carefully every other cross-thread seam in this phase
        # is called out.
        self._held.clear()
        self._holding = False
        self._cancelled_this_hold = False

    @property
    def combo(self) -> str:
        return self._combo_name

    @property
    def is_holding(self) -> bool:
        return self._holding

    def handle_press(self, key) -> None:
        if _is_esc(key):
            if self._holding and not self._cancelled_this_hold:
                self._cancelled_this_hold = True
                # is_holding drops to False immediately -- a cancelled hold
                # is over as far as callers are concerned, even though the
                # physical combo keys may still be held down for a moment
                # longer. This is what makes the subsequent release a
                # pure no-op below (the "self._holding" guard there is
                # already False), rather than needing a second flag check
                # at release time.
                self._holding = False
                logger.debug("Hotkey hold cancelled via Esc")
                if self.on_cancelled:
                    self.on_cancelled()
            return

        name = self._normalize.get(key)
        if name is None:
            # Any other key pressed while the combo is held means the user
            # is typing a keyboard shortcut that happens to share our
            # modifiers (Ctrl+Cmd+Space opens the emoji picker, Shift+Cmd+4
            # takes a screenshot, ...), not dictating -- cancel the hold
            # exactly like Esc does, so no stray recording gets transcribed.
            if self._holding and not self._cancelled_this_hold:
                self._cancelled_this_hold = True
                self._holding = False
                logger.debug("Hotkey hold cancelled: another key was pressed")
                if self.on_cancelled:
                    self.on_cancelled()
            return

        was_all_present = self._required.issubset(self._held)
        self._held.add(name)
        now_all_present = self._required.issubset(self._held)

        if now_all_present and not was_all_present and not self._holding:
            self._holding = True
            self._cancelled_this_hold = False
            logger.debug("Hotkey combo %s: hold started", self._combo_name)
            if self.on_hold_started:
                self.on_hold_started()

    def handle_release(self, key) -> None:
        if _is_esc(key):
            return  # swallow Esc's own release -- see module docstring

        name = self._normalize.get(key)
        if name is None:
            return

        self._held.discard(name)

        # If this hold was cancelled, self._holding is already False (set
        # at cancel time, see handle_press) -- so this whole block is
        # simply skipped, which IS the "swallow the subsequent release"
        # behavior: no hold_ended fires for a cancelled hold's key
        # releases, with no extra flag check needed here.
        if self._holding and not self._required.issubset(self._held):
            self._holding = False
            logger.debug("Hotkey combo %s: hold ended", self._combo_name)
            if self.on_hold_ended:
                self.on_hold_ended()


class HotkeyListener:
    """
    Real global-hotkey listener: `pynput.keyboard.Listener` feeding a
    `HotkeyStateMachine`. `start()`/`stop()` are idempotent-safe (calling
    either twice is a no-op the second time).

    THREADING: `on_hold_started`/`on_hold_ended`/`on_cancelled` are invoked
    on pynput's own listener thread -- see module docstring. Callers that
    need to touch Qt/GUI state must marshal via a Qt signal, not call
    directly into widget code from these callbacks.
    """

    def __init__(
        self,
        combo: str = DEFAULT_COMBO,
        on_hold_started: Optional[Callable[[], None]] = None,
        on_hold_ended: Optional[Callable[[], None]] = None,
        on_cancelled: Optional[Callable[[], None]] = None,
    ) -> None:
        self._state = HotkeyStateMachine(
            combo=combo,
            on_hold_started=on_hold_started,
            on_hold_ended=on_hold_ended,
            on_cancelled=on_cancelled,
        )
        self._listener = None
        self._lock = threading.Lock()

    @property
    def combo(self) -> str:
        return self._state.combo

    def set_combo(self, combo: str) -> None:
        """Change the hotkey combo. Safe to call whether or not the listener is running."""
        self._state.set_combo(combo)

    def start(self) -> None:
        from pynput import keyboard

        with self._lock:
            if self._listener is not None:
                return
            listener = keyboard.Listener(
                on_press=self._state.handle_press,
                on_release=self._state.handle_release,
            )
            listener.start()
            self._listener = listener
            logger.info("Hotkey listener started (combo=%s)", self._state.combo)

    def stop(self) -> None:
        with self._lock:
            listener = self._listener
            self._listener = None
        if listener is not None:
            listener.stop()
            listener.join(timeout=2.0)
            logger.info("Hotkey listener stopped")

    @property
    def is_running(self) -> bool:
        return self._listener is not None

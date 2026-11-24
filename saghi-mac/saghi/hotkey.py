"""
Global press-and-HOLD hotkey detection. The shortcut is any 1 or 2 keys
(see `hotkey_spec.py` for the string format): a single key, two modifiers, or
a modifier plus a key. `COMBOS` below lists the original two-modifier presets,
kept as known-good examples. fn is not reliably hookable.

Two pieces, deliberately separated:

  1. `HotkeyStateMachine` -- pure Python, ZERO pynput/Qt/thread dependency.
     Consumes normalized press/release events and decides when the whole
     combo transitions held/not-held, when Esc cancels a hold, and swallows
     macOS's key-repeat noise. This is what `dev/test_hotkey_logic.py`
     drives directly with synthetic `pynput.keyboard.Key` values -- no real
     listener, no real keyboard, fully deterministic.

  2. `HotkeyListener` -- the real thing: wraps `pynput.keyboard.Listener`
     and feeds its press/release callbacks into a `HotkeyStateMachine`.

Semantics (combo = 1 or 2 tokens, e.g. ("ctrl", "cmd") or ("alt", "vk:0")):

  - ALL keys of the combo down -> `hold_started` fires once. A modifier family
    ("ctrl") matches either side, a side-specific token ("alt_r") only that
    side. When the combo has an ordinary key, that key must go down LAST
    (so typing "a" and then touching Option never starts a dictation).
  - ANY one of them lifts -> `hold_ended` fires once (unless the hold was
    cancelled -- see below), and the state resets so a fresh press cycle
    can fire `hold_started` again.
  - `Esc` pressed while holding -> `cancelled` fires once, and the eventual
    release of the modifier keys is swallowed (no `hold_ended` for a
    cancelled hold). A repeated/auto-repeated Esc press during the same
    hold does NOT fire `cancelled` a second time.
  - Any other (non-combo, non-modifier) key pressed while holding cancels the same
    way as Esc: the user is typing a shortcut that shares our modifiers
    (Ctrl+Cmd+Space, Shift+Cmd+4, ...), not dictating.
  - `on_escape` fires on EVERY Esc press (held combo or not), once per
    physical press (key-repeat of a held Esc is swallowed). The controller
    uses it to discard a recording that is latched (tap-to-toggle) and so
    has no held combo. While holding, `cancelled` fires as well (same
    press); a consumer that treats both as "discard" is idempotent.
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
`on_cancelled`/`on_escape` constructor callbacks therefore also run on that thread.
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
import sys
import threading
from typing import Callable, Dict, FrozenSet, Optional, Tuple

from .hotkey_spec import (
    DEFAULT_HOTKEY,
    ESC_VK,
    MODIFIER_VK_TO_TOKEN,
    family_of,
    parse_hotkey,
    vk_of,
    vk_token,
)

logger = logging.getLogger("saghi.hotkey")

# The original two-modifier presets, kept as known-good examples. Any valid
# shortcut string (hotkey_spec.parse_hotkey) works, not only these.
COMBOS: Dict[str, FrozenSet[str]] = {
    "ctrl+cmd": frozenset({"ctrl", "cmd"}),
    "alt+cmd": frozenset({"alt", "cmd"}),
    "shift+cmd": frozenset({"shift", "cmd"}),
    "ctrl+alt": frozenset({"ctrl", "alt"}),
    "ctrl+shift": frozenset({"ctrl", "shift"}),
}

DEFAULT_COMBO = DEFAULT_HOTKEY

# CGEventType values of a key going down / up (Quartz kCGEventKeyDown/Up).
_KEY_DOWN = 10
_KEY_UP = 11


# macOS virtual key codes of pynput's named keys, looked up by NAME so the
# mapping is the same on every platform (on Linux, Key.<name>.value.vk is an
# X keysym, not a macOS code; on macOS these equal pynput's own values).
_NAMED_KEY_VK: Dict[str, int] = {
    "ctrl": 59, "ctrl_l": 59, "ctrl_r": 62,
    "alt": 58, "alt_l": 58, "alt_r": 61, "alt_gr": 61,
    "cmd": 55, "cmd_l": 55, "cmd_r": 54,
    "shift": 56, "shift_l": 56, "shift_r": 60,
    "esc": 53, "space": 49, "tab": 48, "enter": 36, "backspace": 51, "delete": 117,
    "caps_lock": 57, "up": 126, "down": 125, "left": 123, "right": 124,
    "home": 115, "end": 119, "page_up": 116, "page_down": 121,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100,
    "f9": 101, "f10": 109, "f11": 103, "f12": 111, "f13": 105, "f14": 107, "f15": 113,
    "f16": 106, "f17": 64, "f18": 79, "f19": 80, "f20": 90,
}


def _vk_of_key(key) -> Optional[int]:
    """The macOS virtual key code of a pynput key: named keys by name, others by KeyCode.vk."""
    name = getattr(key, "name", None)
    if isinstance(name, str) and name in _NAMED_KEY_VK:
        return _NAMED_KEY_VK[name]
    vk = getattr(key, "vk", None)
    if vk is None:
        vk = getattr(getattr(key, "value", None), "vk", None)
    return vk if isinstance(vk, int) else None


class HotkeyStateMachine:
    """
    Pure combo-hold state machine -- see module docstring for the full
    semantics. Feed it pynput keys via `handle_press()`/`handle_release()`;
    it calls the optional callbacks synchronously, on whatever thread called
    handle_press/handle_release. Keys are identified by virtual key code, so
    this class needs no pynput import.
    """

    def __init__(
        self,
        combo: str = DEFAULT_COMBO,
        on_hold_started: Optional[Callable[[], None]] = None,
        on_hold_ended: Optional[Callable[[], None]] = None,
        on_cancelled: Optional[Callable[[], None]] = None,
        on_escape: Optional[Callable[[], None]] = None,
    ) -> None:
        self.on_hold_started = on_hold_started
        self.on_hold_ended = on_hold_ended
        self.on_cancelled = on_cancelled
        self.on_escape = on_escape
        self._esc_down = False
        self._held: set = set()  # side-specific modifier tokens + "vk:<n>" tokens
        self._swallowed: set = set()  # vks whose key-down we dropped (see should_suppress)
        self._holding = False
        self._cancelled_this_hold = False
        self._paused = False
        self._required: Tuple[str, ...] = ()
        self._key_token: Optional[str] = None
        self._key_vk: Optional[int] = None
        self.set_combo(combo)

    def set_combo(self, combo: str) -> None:
        tokens = parse_hotkey(combo)
        if tokens is None:
            raise ValueError(f"Invalid hotkey combo: {combo!r}")
        self._combo_name = "+".join(tokens)
        self._required = tokens
        self._key_token = next((t for t in tokens if family_of(t) is None), None)
        self._key_vk = vk_of(self._key_token) if self._key_token else None
        # Changing the combo mid-hold would leave stale state; the
        # documented call pattern (ui/dictation.py) only calls set_combo()
        # when no hold is in progress, but reset defensively regardless.
        #
        # NOT LOCKED against a concurrent handle_press()/handle_release()
        # call from the pynput listener thread -- ui/dictation.py's
        # DictationController._on_settings_changed() calls this on the Qt
        # main thread while handle_press/handle_release run on the listener
        # thread. CPython's GIL makes each attribute assignment/set mutation
        # atomic, so the worst realistic outcome is one spurious (or missed)
        # `hold_ended` right at the moment the combo is changed, which
        # DictationController's own `_busy`/`_recording_active` guards
        # absorb. Changing the shortcut needs the Settings page, which is
        # not the moment the shortcut is being held. `_swallowed` is kept on
        # purpose so the key-up of a key whose down we dropped is dropped too.
        self._held.clear()
        self._holding = False
        self._cancelled_this_hold = False
        self._esc_down = False

    @property
    def combo(self) -> str:
        return self._combo_name

    @property
    def is_holding(self) -> bool:
        return self._holding

    def set_paused(self, paused: bool) -> None:
        """
        While paused every event is ignored (no callbacks, nothing suppressed).
        The settings page pauses the shortcut while it records a new one.
        """
        self._paused = bool(paused)
        self._held.clear()
        self._holding = False
        self._cancelled_this_hold = False
        self._esc_down = False

    @property
    def needs_intercept(self) -> bool:
        """True when the combo has an ordinary key whose events must be dropped."""
        return self._key_token is not None

    def _modifier_down(self, token: str) -> bool:
        fam = family_of(token)
        if token == fam:
            return f"{fam}_l" in self._held or f"{fam}_r" in self._held
        return token in self._held

    def _satisfied(self) -> bool:
        return all(self._modifier_down(t) if family_of(t) else t in self._held for t in self._required)

    def _modifiers_down(self) -> bool:
        """Every modifier of the combo is down (vacuously true for a single key)."""
        return all(self._modifier_down(t) for t in self._required if family_of(t))

    def should_suppress(self, vk: int, is_down: bool) -> bool:
        """
        Should this key event be dropped before it reaches the focused app?
        Only the combo's ordinary key (if it has one) is ever dropped, and only
        while the rest of the combo (its modifier, if any) is down. A dropped
        key-down is remembered so its key-repeats and its key-up are dropped
        too, even if the modifier was released first. Never Esc; modifier-only
        combos drop nothing.
        """
        if vk == ESC_VK or self._paused:
            return False
        if self._key_vk is None or vk != self._key_vk:
            self._swallowed.discard(vk)
            return False
        if is_down:
            if vk in self._swallowed:
                return True  # key-repeat of a key we are swallowing
            if self._modifiers_down():
                self._swallowed.add(vk)
                return True
            return False
        if vk in self._swallowed:
            self._swallowed.discard(vk)
            return True
        return False

    def _cancel_hold(self, why: str) -> None:
        if self._holding and not self._cancelled_this_hold:
            self._cancelled_this_hold = True
            # is_holding drops to False immediately -- a cancelled hold is
            # over as far as callers are concerned, even though the physical
            # keys may still be down. That makes the later release a no-op.
            self._holding = False
            logger.debug("Hotkey hold cancelled: %s", why)
            if self.on_cancelled:
                self.on_cancelled()

    @staticmethod
    def _token_of(key) -> Tuple[Optional[str], bool]:
        """(token, is_modifier). token None = a key we cannot identify."""
        vk = _vk_of_key(key)
        if vk is None:
            return None, False
        mod = MODIFIER_VK_TO_TOKEN.get(vk)
        if mod is not None:
            return mod, True
        return vk_token(vk), False

    def handle_press(self, key) -> None:
        if self._paused:
            return
        if _vk_of_key(key) == ESC_VK:
            if self._esc_down:
                return  # key-repeat of an Esc that is still down
            self._esc_down = True
            self._cancel_hold("Esc")
            if self.on_escape:
                self.on_escape()
            return

        token, is_mod = self._token_of(key)
        if token is None or (not is_mod and token not in self._required):
            # Any other key pressed while the combo is held means the user
            # is typing a keyboard shortcut that happens to share our
            # modifiers (Ctrl+Cmd+Space opens the emoji picker, Shift+Cmd+4
            # takes a screenshot, ...), not dictating -- cancel the hold
            # exactly like Esc does, so no stray recording gets transcribed.
            self._cancel_hold("another key was pressed")
            return

        was_all_present = self._satisfied()
        self._held.add(token)
        now_all_present = self._satisfied()

        if now_all_present and not was_all_present and not self._holding:
            # With an ordinary key in the combo it must be the LAST one down.
            if self._key_token is not None and token != self._key_token:
                return
            self._holding = True
            self._cancelled_this_hold = False
            logger.debug("Hotkey combo %s: hold started", self._combo_name)
            if self.on_hold_started:
                self.on_hold_started()

    def handle_release(self, key) -> None:
        if self._paused:
            return
        if _vk_of_key(key) == ESC_VK:
            self._esc_down = False
            return  # swallow Esc's own release -- see module docstring

        token, _is_mod = self._token_of(key)
        if token is None:
            return

        self._held.discard(token)

        # If this hold was cancelled, self._holding is already False (set
        # at cancel time) -- so this block is skipped, which IS the "swallow
        # the subsequent release" behaviour: no hold_ended for a cancelled hold.
        if self._holding and not self._satisfied():
            self._holding = False
            logger.debug("Hotkey combo %s: hold ended", self._combo_name)
            if self.on_hold_ended:
                self.on_hold_ended()


def make_intercept(state: HotkeyStateMachine, get_vk: Callable[[object], int]):
    """
    The `darwin_intercept` callback for pynput's Listener: `(event_type, event)`
    -> the event (pass it on) or None (drop it system wide). Tiny and
    exception-safe: any problem passes the event through. `get_vk` reads the
    virtual key code out of a CGEvent (injected so tests need no Quartz).
    """

    def _intercept(event_type, event):
        try:
            if event_type == _KEY_DOWN:
                is_down = True
            elif event_type == _KEY_UP:
                is_down = False
            else:
                return event
            if state.should_suppress(int(get_vk(event)), is_down):
                return None
        except Exception:  # noqa: BLE001 -- never break the user's typing
            pass
        return event

    return _intercept


def _listener_class(keyboard):
    """
    pynput's Listener, except on macOS: there its thread reads the keyboard
    layout (TISCopyCurrentKeyboardInputSource / TSMGetInputSourceProperty)
    when it starts, and recent macOS kills the app (SIGTRAP in
    dispatch_assert_queue) when that runs off the main thread. Seen when the
    owner picked the key left of 1 (vk:10): the listener restarted from
    set_combo and Saghi quit. So read the layout here, on the caller's (main)
    thread, and hand the listener that value.
    """
    if sys.platform != "darwin":
        return keyboard.Listener
    try:
        from pynput._util.darwin import keycode_context

        with keycode_context() as ctx:
            context = ctx
    except Exception:  # noqa: BLE001
        logger.warning("Could not read the keyboard layout on the main thread", exc_info=True)
        return keyboard.Listener

    base = keyboard.Listener

    class _MainThreadLayoutListener(base):
        def _run(self):
            self._context = context
            try:
                super(base, self)._run()  # skip base._run's off-thread layout read
            finally:
                self._context = None

    return _MainThreadLayoutListener


class HotkeyListener:
    """
    Real global-hotkey listener: `pynput.keyboard.Listener` feeding a
    `HotkeyStateMachine`. `start()`/`stop()` are idempotent-safe (calling
    either twice is a no-op the second time).

    The `darwin_intercept` option (which turns pynput's event tap from
    listen-only into an active one) is passed ONLY when the combo has an
    ordinary key to drop, so modifier-only shortcuts keep the lighter
    listen-only tap. If the combo changes between the two kinds while
    running, the listener restarts.

    THREADING: `on_hold_started`/`on_hold_ended`/`on_cancelled`/`on_escape` are invoked
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
        on_escape: Optional[Callable[[], None]] = None,
    ) -> None:
        self._state = HotkeyStateMachine(
            combo=combo,
            on_hold_started=on_hold_started,
            on_hold_ended=on_hold_ended,
            on_cancelled=on_cancelled,
            on_escape=on_escape,
        )
        self._listener = None
        self._with_intercept = False
        self._lock = threading.Lock()

    @property
    def combo(self) -> str:
        return self._state.combo

    def set_combo(self, combo: str) -> None:
        """Change the hotkey combo. Safe to call whether or not the listener is running."""
        self._state.set_combo(combo)
        with self._lock:
            restart = self._listener is not None and self._with_intercept != self._state.needs_intercept
        if restart:
            self.stop()
            self.start()

    def set_paused(self, paused: bool) -> None:
        """Ignore the shortcut while True (used while the settings page records a new one)."""
        self._state.set_paused(paused)

    def start(self) -> None:
        from pynput import keyboard

        with self._lock:
            if self._listener is not None:
                return
            kwargs = {}
            self._with_intercept = self._state.needs_intercept
            if self._with_intercept:
                try:
                    from Quartz import CGEventGetIntegerValueField, kCGKeyboardEventKeycode

                    kwargs["darwin_intercept"] = make_intercept(
                        self._state, lambda ev: CGEventGetIntegerValueField(ev, kCGKeyboardEventKeycode)
                    )
                except Exception:  # noqa: BLE001
                    # Without Quartz the key still works as a shortcut, it just
                    # also reaches the focused app.
                    logger.warning("Quartz unavailable: the shortcut key will not be suppressed")
                    self._with_intercept = False
            listener = _listener_class(keyboard)(
                on_press=self._state.handle_press,
                on_release=self._state.handle_release,
                **kwargs,
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

#!/usr/bin/env python3
"""
Custom dictation shortcuts: parsing, matching, key suppression, labels and the
shortcut recorder widget (fed with synthetic Qt key events). No real keyboard,
no listener, no model.

Run:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=. SAGHI_DATA_DIR=$(mktemp -d) <venv>/bin/python dev/test_hotkey_custom.py
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SAGHI_DATA_DIR", tempfile.mkdtemp(prefix="saghi-hotkey-custom-"))

from pynput.keyboard import Key, KeyCode  # noqa: E402
from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from saghi import settings  # noqa: E402
from saghi.hotkey import HotkeyStateMachine, make_intercept  # noqa: E402
from saghi.hotkey_labels import hotkey_label  # noqa: E402
from saghi.hotkey_spec import is_bare_typing_key, normalize_hotkey, parse_hotkey  # noqa: E402
from saghi.ui import strings  # noqa: E402
from saghi.ui.settings_page import SettingsPage, ShortcutRecorder  # noqa: E402

app = QApplication.instance() or QApplication([])
_checks = 0


def check(cond: bool, msg: str) -> None:
    global _checks
    assert cond, f"FAILED: {msg}"
    _checks += 1
    print(f"ok: {msg}")


# ---- 1. parsing / validation ---------------------------------------------------
print("--- parsing ---")
for preset in ("ctrl+cmd", "alt+cmd", "shift+cmd", "ctrl+alt", "ctrl+shift"):
    check(parse_hotkey(preset) == tuple(preset.split("+")), f"preset {preset} is valid")
check(parse_hotkey("vk:10") == ("vk:10",), "vk:10 (single key) is valid")
check(parse_hotkey("cmd+vk:49") == ("cmd", "vk:49"), "cmd+vk:49 is valid")
check(parse_hotkey("vk:49+cmd") == ("vk:49", "cmd"), "key first, modifier second is valid")
check(parse_hotkey("alt_r") == ("alt_r",), "alt_r (side-specific) is valid")
check(parse_hotkey("ctrl_l+alt_r") == ("ctrl_l", "alt_r"), "two side-specific modifiers are valid")
for bad in ("ctrl+alt+cmd", "ctrl+alt+vk:10", "vk:53", "cmd+vk:53", "", "+", "garbage", "vk:", "vk:x", "vk:-1",
            "vk:999", "vk:055", "vk:55", "vk:63", "ctrl+ctrl", "ctrl+ctrl_l", "vk:10+vk:11", None, 5, "fn"):
    check(parse_hotkey(bad) is None, f"{bad!r} is rejected")
check(normalize_hotkey("garbage") == "ctrl+cmd", "garbage normalizes to the default")
check(normalize_hotkey(" Cmd + VK:49 ") == "cmd+vk:49", "case and spaces are normalized")
check(settings._coerce({"hotkey": "vk:10"}).hotkey == "vk:10", "settings keeps a custom shortcut")
check(settings._coerce({"hotkey": "vk:53"}).hotkey == "ctrl+cmd", "settings drops an Esc shortcut")
check(settings._coerce({"hotkey": "a+b+c"}).hotkey == "ctrl+cmd", "settings drops a 3-key shortcut")
check(settings._coerce({}).hotkey == "ctrl+cmd", "the default stays ctrl+cmd")
check(is_bare_typing_key("vk:49") and is_bare_typing_key("vk:0") and is_bare_typing_key("vk:10"), "space/letter/section are bare typing keys")
check(not is_bare_typing_key("vk:96") and not is_bare_typing_key("cmd+vk:49") and not is_bare_typing_key("alt_r"), "F5, cmd+space, alt_r are not")
try:
    HotkeyStateMachine("vk:53")
    check(False, "state machine rejects Esc")
except ValueError:
    check(True, "state machine rejects an invalid combo with ValueError")


# ---- 2. matching ------------------------------------------------------------------
print("--- matching ---")


def vkey(n: int):
    return KeyCode.from_vk(n)


def machine(combo: str):
    ev: list = []
    sm = HotkeyStateMachine(
        combo,
        on_hold_started=lambda: ev.append("start"),
        on_hold_ended=lambda: ev.append("end"),
        on_cancelled=lambda: ev.append("cancel"),
        on_escape=lambda: ev.append("esc"),
    )
    return sm, ev


SECTION, SPACE, F5, A = vkey(10), vkey(49), vkey(96), vkey(0)

sm, ev = machine("vk:10")
sm.handle_press(SECTION)
sm.handle_press(SECTION)  # key-repeat
check(ev == ["start"], "single key: start once, repeat swallowed")
sm.handle_release(SECTION)
check(ev == ["start", "end"], "single key: release ends the hold")
sm.handle_press(SECTION)
sm.handle_release(SECTION)
check(ev == ["start", "end", "start", "end"], "single key: a second cycle works")

sm, ev = machine("vk:96")
sm.handle_press(Key.f5)
sm.handle_release(Key.f5)
check(ev == ["start", "end"], "single key given as a pynput named key (F5) works")
sm.handle_press(A)
sm.handle_release(A)
check(ev == ["start", "end"], "another key while nothing is held does nothing")

sm, ev = machine("alt+vk:0")
sm.handle_press(Key.alt_l)
check(ev == [], "modifier+key: the modifier alone does nothing")
sm.handle_press(A)
check(ev == ["start"], "modifier+key: key while the modifier is down starts")
sm.handle_release(Key.alt_l)
check(ev == ["start", "end"], "modifier+key: releasing the modifier ends the hold")
sm.handle_release(A)
sm.handle_press(A)
sm.handle_press(Key.alt_r)
check(ev == ["start", "end"], "modifier+key: the key must go down LAST (typing then touching the modifier is ignored)")
sm.handle_release(A)
sm.handle_release(Key.alt_r)
ev.clear()
sm.handle_press(Key.alt_r)
sm.handle_press(A)
sm.handle_release(A)
check(ev == ["start", "end"], "modifier+key: releasing the key ends the hold")

sm, ev = machine("ctrl+alt")
sm.handle_press(Key.alt_r)
sm.handle_press(Key.ctrl_l)
sm.handle_release(Key.alt_r)
check(ev == ["start", "end"], "two modifiers: either side, any order")

sm, ev = machine("alt_r")
sm.handle_press(Key.alt_l)
sm.handle_release(Key.alt_l)
check(ev == [], "side-specific: LEFT Option does not trigger alt_r")
sm.handle_press(Key.alt_r)
sm.handle_release(Key.alt_r)
check(ev == ["start", "end"], "side-specific: right Option triggers alt_r")
sm.handle_press(Key.alt_gr)
sm.handle_release(Key.alt_gr)
check(ev[-2:] == ["start", "end"], "side-specific: alt_gr is the same physical key as right Option")

sm, ev = machine("alt_l")
sm.handle_press(Key.alt_r)
check(ev == [], "side-specific: right Option does not trigger alt_l")
sm.handle_release(Key.alt_r)
sm.handle_press(Key.alt)
check(ev == ["start"], "side-specific: pynput's generic alt is the left key")
sm.handle_release(Key.alt)

sm, ev = machine("alt")
sm.handle_press(Key.alt_r)
sm.handle_release(Key.alt_r)
sm.handle_press(Key.alt_l)
sm.handle_release(Key.alt_l)
check(ev == ["start", "end", "start", "end"], "family token alt: either Option key triggers")

print("--- cancel ---")
sm, ev = machine("vk:10")
sm.handle_press(SECTION)
sm.handle_press(A)
check(ev == ["start", "cancel"], "a foreign key while holding cancels")
sm.handle_press(SPACE)
sm.handle_release(SECTION)
check(ev == ["start", "cancel"], "a second foreign key is not a second cancel; the release is swallowed")
sm, ev = machine("alt+vk:0")
sm.handle_press(Key.alt_l)
sm.handle_press(A)
sm.handle_press(SPACE)
check(ev == ["start", "cancel"], "modifier+key: a foreign key while holding cancels")
sm, ev = machine("vk:10")
sm.handle_press(SECTION)
sm.handle_press(Key.esc)
check(ev == ["start", "cancel", "esc"], "Esc while holding cancels and fires on_escape")
sm.handle_release(Key.esc)
sm.handle_release(SECTION)
check(ev == ["start", "cancel", "esc"], "no hold_ended after the cancel")
sm, ev = machine("ctrl+cmd")
sm.handle_press(Key.ctrl_l)
sm.handle_press(Key.cmd)
sm.handle_press(Key.shift)
check(ev == ["start"], "an extra modifier while holding is not a cancel (as before)")

print("--- paused ---")
sm, ev = machine("vk:10")
sm.set_paused(True)
sm.handle_press(SECTION)
sm.handle_release(SECTION)
sm.handle_press(Key.esc)
check(ev == [] and not sm.should_suppress(10, True), "paused: no events, nothing suppressed")
sm.set_paused(False)
sm.handle_press(SECTION)
check(ev == ["start"], "resumed: works again")


# ---- 3. suppression ------------------------------------------------------------------
print("--- suppression ---")
sm, ev = machine("vk:10")
check(sm.should_suppress(10, True) and sm.should_suppress(10, True), "single key: down (and repeat) suppressed")
check(sm.should_suppress(10, False), "single key: up suppressed")
check(not sm.should_suppress(10, False), "single key: a stray second up passes")
check(not sm.should_suppress(11, True) and not sm.should_suppress(49, False), "other keys pass")
check(not sm.should_suppress(53, True) and not sm.should_suppress(53, False), "Esc never suppressed")

sm, ev = machine("cmd+vk:49")
check(not sm.should_suppress(49, True), "modifier+key: key alone passes (types normally)")
check(not sm.should_suppress(49, False), "modifier+key: its up passes too")
sm.handle_press(Key.cmd_l)
check(sm.should_suppress(49, True), "modifier+key: key with the modifier down is suppressed")
sm.handle_release(Key.cmd_l)
check(sm.should_suppress(49, True), "modifier+key: key-repeat after the modifier lifted is still swallowed")
check(sm.should_suppress(49, False), "modifier+key: the matching up is swallowed even after the modifier lifted")
check(not sm.should_suppress(49, False), "modifier+key: nothing left to swallow")
sm.handle_press(Key.cmd_r)
check(sm.should_suppress(49, True), "modifier+key: either Command key counts")
sm.should_suppress(49, False)
sm.handle_release(Key.cmd_r)
sm.handle_press(Key.alt_l)
check(not sm.should_suppress(49, True), "modifier+key: a different modifier does not count")

sm, ev = machine("alt_r+vk:0")
sm.handle_press(Key.alt_l)
check(not sm.should_suppress(0, True), "side-specific modifier+key: the wrong side does not suppress")
sm.handle_press(Key.alt_r)
check(sm.should_suppress(0, True), "side-specific modifier+key: the right side suppresses")

for combo in ("ctrl+cmd", "alt_r", "ctrl+alt"):
    sm, ev = machine(combo)
    check(not sm.needs_intercept, f"{combo}: modifier-only needs no intercept")
    sm.handle_press(Key.ctrl_l)
    sm.handle_press(Key.alt_r)
    sm.handle_press(Key.cmd)
    check(not any(sm.should_suppress(v, d) for v in (0, 10, 36, 49, 55, 58, 59, 61, 96) for d in (True, False)), f"{combo}: nothing is suppressed")
check(HotkeyStateMachine("vk:10").needs_intercept and HotkeyStateMachine("cmd+vk:49").needs_intercept, "key combos need the intercept")

sm, ev = machine("vk:10")
sm.should_suppress(10, True)
sm.set_combo("vk:11")
check(not sm.should_suppress(10, True), "after the combo changes the old key passes again")

print("--- intercept callback ---")
sm, ev = machine("vk:10")
vk_of_event = lambda e: e["vk"]  # noqa: E731
cb = make_intercept(sm, vk_of_event)
down, up = {"vk": 10}, {"vk": 10}
check(cb(10, down) is None, "intercept: combo key down is dropped (None)")
check(cb(11, up) is None, "intercept: combo key up is dropped")
other = {"vk": 0}
check(cb(10, other) is other and cb(11, other) is other, "intercept: other keys pass the event through")
check(cb(12, down) is down, "intercept: a modifier-change event passes")
check(cb(10, {"bad": 1}) == {"bad": 1}, "intercept: an exception passes the event through")
esc = {"vk": 53}
check(cb(10, esc) is esc, "intercept: Esc passes")


# ---- 4. labels ---------------------------------------------------------------------------
print("--- labels ---")
check(hotkey_label("ctrl+alt") == "⌃⌥" and hotkey_label("ctrl+cmd") == "⌃⌘", "presets: symbols")
check(hotkey_label("vk:10") == "§", "section sign")
check(hotkey_label("vk:96") == "F5" and hotkey_label("vk:122") == "F1" and hotkey_label("vk:90") == "F20", "function keys")
check(hotkey_label("alt+vk:0") == "⌥A", "option + A")
check(hotkey_label("cmd+vk:49") == "⌘ + Space", "command + Space")
check(hotkey_label("alt_r") == "Right ⌥" and hotkey_label("shift_l") == "Left ⇧", "side-specific modifiers")
check(hotkey_label("ctrl_r+cmd") == "Right ⌃ + ⌘", "side-specific plus family")
check(hotkey_label("vk:123") == "←" and hotkey_label("vk:126") == "↑", "arrows")
check(hotkey_label("vk:48") == "Tab" and hotkey_label("vk:36") == "Return" and hotkey_label("vk:50") == "`", "tab, return, backtick")
check(hotkey_label("vk:200") == "مفتاح 200", "unknown key code")
check(hotkey_label("garbage") == "", "invalid string: empty label")
check(strings.hotkey_symbols("vk:10") == "§" and strings.hotkey_symbols("ctrl+alt") == "⌃⌥", "strings.hotkey_symbols uses the labels")
check("§" in strings.hotkey_hint("vk:10") and strings.hotkey_hint("ctrl+cmd") == f"اضغط {strings.HOTKEY_LABELS['ctrl+cmd']} مطولًا للإملاء", "tray hint for custom and preset")


# ---- 5. recorder widget ----------------------------------------------------------------------
print("--- recorder ---")
CMD_L, CMD_R, SHIFT_L, SHIFT_R, ALT_L, ALT_R, CTRL_L, CTRL_R = 55, 54, 56, 60, 58, 61, 59, 62
QT_KEY = {55: Qt.Key.Key_Control, 54: Qt.Key.Key_Control, 56: Qt.Key.Key_Shift, 60: Qt.Key.Key_Shift,
          58: Qt.Key.Key_Alt, 61: Qt.Key.Key_Alt, 59: Qt.Key.Key_Meta, 62: Qt.Key.Key_Meta, 53: Qt.Key.Key_Escape,
          49: Qt.Key.Key_Space, 0: Qt.Key.Key_A, 10: Qt.Key.Key_section, 96: Qt.Key.Key_F5}


def press(w, vk, auto=False):
    app.sendEvent(w, QKeyEvent(QEvent.Type.KeyPress, int(QT_KEY.get(vk, Qt.Key.Key_unknown)),
                               Qt.KeyboardModifier.NoModifier, 0, vk, 0, "", auto, 1))


def release(w, vk):
    app.sendEvent(w, QKeyEvent(QEvent.Type.KeyRelease, int(QT_KEY.get(vk, Qt.Key.Key_unknown)),
                               Qt.KeyboardModifier.NoModifier, 0, vk, 0, "", False, 1))


def fresh(initial="ctrl+cmd"):
    w = ShortcutRecorder(initial)
    w.show()
    out = {"changed": [], "rec": [], "msg": []}
    w.hotkey_changed.connect(out["changed"].append)
    w.recording_changed.connect(out["rec"].append)
    w.message_changed.connect(out["msg"].append)
    w.start_recording()
    return w, out


w = ShortcutRecorder("ctrl+cmd")
check(w.text() == strings.isolate_ltr("⌃⌘"), "idle: the button shows the current label")
w.start_recording()
check(w.is_recording and w.text() == strings.SETTINGS_HOTKEY_PROMPT == "اضغط الاختصار…", "recording: the prompt is shown")
w.cancel_recording()

w, out = fresh()
check(out["rec"] == [True], "recording_changed(True) on start")
press(w, 49)
check(out["changed"] == ["vk:49"] and not w.is_recording and out["rec"] == [True, False], "a lone Space is recorded at once")
check(w.hotkey == "vk:49" and "Space" in w.text(), "the button shows the new label")

w, out = fresh()
press(w, 10)
check(out["changed"] == ["vk:10"], "section key -> vk:10")
w, out = fresh()
press(w, 96)
check(out["changed"] == ["vk:96"], "F5 -> vk:96")
w, out = fresh()
press(w, CMD_L)
press(w, 49)
check(out["changed"] == ["cmd+vk:49"], "Command held + Space -> cmd+vk:49")
w, out = fresh()
press(w, ALT_R)
press(w, 0)
check(out["changed"] == ["alt+vk:0"], "Option held + A -> alt+vk:0 (family)")
w, out = fresh()
press(w, ALT_R)
release(w, ALT_R)
check(out["changed"] == ["alt_r"], "right Option alone -> alt_r")
w, out = fresh()
press(w, ALT_L)
release(w, ALT_L)
check(out["changed"] == ["alt_l"], "left Option alone -> alt_l")
w, out = fresh()
press(w, CTRL_L)
press(w, ALT_L)
release(w, CTRL_L)
check(out["changed"] == [], "two modifiers: not finished while one is still down")
release(w, ALT_L)
check(out["changed"] == ["ctrl+alt"], "two modifiers -> ctrl+alt after all are released")
w, out = fresh()
press(w, CMD_R)
press(w, SHIFT_L)
release(w, SHIFT_L)
release(w, CMD_R)
check(out["changed"] == ["shift+cmd"], "two modifiers are stored in the canonical order (shift+cmd)")
w, out = fresh()
press(w, ALT_L)
press(w, ALT_R)
release(w, ALT_L)
release(w, ALT_R)
check(out["changed"] == [] and w.is_recording, "both Option keys: not a shortcut, keeps recording")

w, out = fresh()
press(w, CTRL_L)
press(w, ALT_L)
press(w, CMD_L)
check(strings.SETTINGS_HOTKEY_MAX_TWO in out["msg"] and w.is_recording, "3 modifiers: the max-two message, still recording")
release(w, CTRL_L)
release(w, ALT_L)
release(w, CMD_L)
check(out["changed"] == [] and w.is_recording, "3 modifiers released: nothing saved, still recording")
press(w, CMD_L)
release(w, CMD_L)
check(out["changed"] == ["cmd_l"], "after the message a fresh attempt works")
w, out = fresh()
press(w, CTRL_L)
press(w, ALT_L)
press(w, 49)
check(out["changed"] == [] and w.is_recording and strings.SETTINGS_HOTKEY_MAX_TWO in out["msg"], "2 modifiers + a key: max-two message, still recording")
release(w, 49)
release(w, ALT_L)
release(w, CTRL_L)
check(out["changed"] == [] and w.is_recording, "that attempt is dropped")

w, out = fresh("alt+cmd")
press(w, 53)
check(out["changed"] == [] and not w.is_recording and w.hotkey == "alt+cmd" and out["rec"] == [True, False], "Esc cancels and keeps the old shortcut")
release(w, 53)
w, out = fresh()
press(w, CMD_L)
press(w, 53)
check(out["changed"] == [] and not w.is_recording, "Esc with a modifier held also cancels")
w, out = fresh()
press(w, 49, auto=True)
check(out["changed"] == [] and w.is_recording, "auto-repeat events are ignored")
w, out = fresh()
press(w, 57)
release(w, 57)
press(w, 63)
release(w, 63)
check(out["changed"] == [] and w.is_recording, "caps lock and fn are ignored")
w, out = fresh()
w.clearFocus()
w.cancel_recording()
check(not w.is_recording and out["changed"] == [], "cancel_recording stops without changing anything")
w2 = ShortcutRecorder("ctrl+cmd")
press(w2, 49)
check(w2.hotkey == "ctrl+cmd", "key events are ignored when not recording")


# ---- 6. settings page ----------------------------------------------------------------------------
print("--- settings page ---")
data_dir = tempfile.mkdtemp(prefix="saghi-hotkey-page-")
os.environ["SAGHI_DATA_DIR"] = data_dir
sm = settings.SettingsManager()
page = SettingsPage(sm)
page.show()
captures: list = []
page.hotkey_capture_changed.connect(captures.append)
rec = page.hotkey_recorder
check(rec.hotkey == "ctrl+cmd" and page.hotkey_reset_btn.isHidden(), "page starts on the default; reset link hidden")
rec.start_recording()
check(captures == [True], "page tells the app that capture started")
press(rec, 10)
check(sm.current.hotkey == "vk:10" and captures == [True, False], "a recorded shortcut is saved and capture ends")
check(not page.hotkey_note.isHidden() and page.hotkey_note.text() == strings.SETTINGS_HOTKEY_BARE_WARNING,
      "a bare typing key shows the warning line")
check(not page.hotkey_reset_btn.isHidden(), "reset link shows when not default")
rec.start_recording()
press(rec, 96)
check(sm.current.hotkey == "vk:96" and page.hotkey_note.isHidden(), "F5 -> no warning")
rec.start_recording()
press(rec, CTRL_L)
press(rec, ALT_L)
press(rec, CMD_L)
check(page.hotkey_note.text() == strings.SETTINGS_HOTKEY_MAX_TWO, "the max-two message shows on the page")
press(rec, 53)
check(sm.current.hotkey == "vk:96" and page.hotkey_note.isHidden(), "Esc keeps the old shortcut and clears the message")
page.hotkey_reset_btn.click()
check(sm.current.hotkey == "ctrl+cmd" and rec.hotkey == "ctrl+cmd" and page.hotkey_reset_btn.isHidden(), "reset restores ctrl+cmd")
check(sm.current.hotkey == settings.SettingsManager().current.hotkey, "the shortcut is on disk")

print(f"\nALL PASSED ({_checks} checks)")

#!/usr/bin/env python3
"""
Tests for the single status source and its two faces -- no model, no
microphone, no hotkey listener, no real data dir:

  1. DictationController status: ready -> recording -> ready (too-short tap),
     -> error (bit-exact silence) -> ready (error auto-hide), loading (engine
     status bridge), disabled (dictation toggle), and the full flow with a
     fake engine (recording -> processing -> ready). `status_changed` fires
     exactly once per change, never for a repeat.
  2. Pill <-> settings: settings are pushed into the pill only when they
     changed, a drag / the pill's own menu are written back to settings,
     the pill's open request is re-emitted, start()/stop() show/hide it.
  3. SaghiTray: one icon per status (only recording is a colour icon, the
     rest are template masks), the animation timer runs only while
     processing/loading, status line + tooltip + hint, the "always show the
     pill" toggle bound to settings, and the old constructor (no controller,
     no settings) still works.

The pill (ui/indicator.py) and SF Symbols (ui/sf_symbols.py) are written in
parallel with this feature; where the real module does not have the needed
API yet, a small stand-in is used instead (the run says which).

Run:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=. <venv>/bin/python dev/test_status_ui.py

Plain assert-based, no pytest -- same style as the other dev/ tests.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

SCRATCH = Path(tempfile.mkdtemp(prefix="saghi-test-status-ui-"))
os.environ["SAGHI_DATA_DIR"] = str(SCRATCH / "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal  # noqa: E402
from PySide6.QtGui import QColor, QIcon, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

_checks = 0


def check(condition: bool, message: str) -> None:
    global _checks
    assert condition, f"FAILED: {message}"
    _checks += 1
    print(f"ok: {message}")


app = QApplication.instance() or QApplication(sys.argv)


def wait_ms(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_until(predicate, timeout_ms: int = 3000) -> bool:
    step = 10
    waited = 0
    while not predicate() and waited < timeout_ms:
        wait_ms(step)
        waited += step
    return predicate()


# ---- stand-ins for the modules written in parallel -----------------------------

def _stub_icon(name, size=16, color=None, *, weight="medium", variable=None, mask=False) -> QIcon:
    """Distinct pixmap per (name, variable, colour); black glyph alpha 255 when mask."""
    pix = QPixmap(size * 2, size * 2)
    if mask:
        # Template glyph: black, the shape carried by the alpha channel only.
        pix.fill(QColor(0, 0, 0, 255))
        tint = (hash((name, variable)) % 200) + 20
        image = pix.toImage()
        for x in range(image.width()):
            image.setPixelColor(x, x % image.height(), QColor(0, 0, 0, tint))
        pix = QPixmap.fromImage(image)
    else:
        pix.fill(QColor(color) if color else QColor("#888888"))
    icon = QIcon(pix)
    icon.setIsMask(mask)
    return icon


try:
    from saghi.ui import sf_symbols  # noqa: E402

    _sf_real = all(hasattr(sf_symbols, n) for n in ("icon", "pixmap"))
except ImportError:
    _sf_real = False
if not _sf_real:
    stub = types.ModuleType("saghi.ui.sf_symbols")
    stub.icon = _stub_icon
    stub.pixmap = lambda *a, **k: _stub_icon(*a, **k).pixmap(32, 32)
    sys.modules["saghi.ui.sf_symbols"] = stub
    import saghi.ui as _ui_pkg  # noqa: E402

    _ui_pkg.sf_symbols = stub
print(f"sf_symbols: {'real module' if _sf_real else 'stand-in'}")

from saghi import history, paths  # noqa: E402
from saghi.engine import TranscribeResult  # noqa: E402
from saghi.settings import SettingsManager  # noqa: E402
from saghi.ui import dictation as dictation_module  # noqa: E402
from saghi.ui import indicator as indicator_module  # noqa: E402
from saghi.ui import strings  # noqa: E402
from saghi.ui.dictation import DictationController  # noqa: E402
from saghi.ui.engine_status import EngineStatusBridge  # noqa: E402
from saghi.ui.tray import SaghiTray  # noqa: E402

_PILL_API = (
    "state_changed", "open_requested", "anchor_moved", "mode_change_requested",
    "set_idle_mode", "set_idle_status", "set_anchor", "set_glass_enabled",
)


class _StubIndicator(QObject):
    """Just enough of the new IndicatorWindow for the controller (no painting)."""

    state_changed = Signal(str)
    open_requested = Signal()
    anchor_moved = Signal(int, int)
    mode_change_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._state = "hidden"
        self._visible = False
        self._error_timer = QTimer(self)
        self._error_timer.setSingleShot(True)
        self._error_timer.timeout.connect(self.hide_indicator)

    @property
    def state(self) -> str:
        return self._state

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.state_changed.emit(state)

    def set_style(self, style) -> None: ...
    def set_color(self, color) -> None: ...
    def set_idle_mode(self, mode) -> None: ...
    def set_idle_status(self, status) -> None: ...
    def set_anchor(self, point) -> None: ...
    def set_glass_enabled(self, enabled) -> None: ...

    def show_recording(self, level_fn) -> None:
        self._error_timer.stop()
        self._visible = True
        self._set_state("recording")

    def show_processing(self, text=None) -> None:
        self._error_timer.stop()
        self._visible = True
        self._set_state("processing")

    def show_error(self, message, auto_hide_ms=2500) -> None:
        self._visible = True
        self._set_state("error")
        self._error_timer.start(auto_hide_ms)

    def hide_indicator(self) -> None:
        self._error_timer.stop()
        self._set_state("hidden")

    def hide(self) -> None:
        self._visible = False

    def isVisible(self) -> bool:  # noqa: N802 -- Qt naming
        return self._visible


_real_pill = indicator_module.IndicatorWindow
_pill_is_real = all(hasattr(_real_pill, name) for name in _PILL_API)
print(f"IndicatorWindow: {'real class' if _pill_is_real else 'stand-in (new API not there yet)'}")
_PillBase = _real_pill if _pill_is_real else _StubIndicator


class _SpyPill(_PillBase):
    """Records what the controller pushes into the pill, then does the real thing."""

    calls: list = []

    def set_idle_mode(self, mode) -> None:
        _SpyPill.calls.append(("mode", mode))
        super().set_idle_mode(mode)

    def set_idle_status(self, status) -> None:
        _SpyPill.calls.append(("status", status))
        super().set_idle_status(status)

    def set_anchor(self, point) -> None:
        _SpyPill.calls.append(("anchor", point))
        super().set_anchor(point)

    def set_glass_enabled(self, enabled) -> None:
        _SpyPill.calls.append(("glass", enabled))
        super().set_glass_enabled(enabled)

    def hide_indicator(self) -> None:
        _SpyPill.calls.append(("hide_indicator",))
        super().hide_indicator()

    def hide(self) -> None:
        _SpyPill.calls.append(("hide",))
        super().hide()


dictation_module.IndicatorWindow = _SpyPill


class _FakeRecorder:
    def __init__(self, audio: np.ndarray) -> None:
        self._audio = audio
        self.cancel_called = False

    def start(self, device=None) -> None: ...

    def stop(self) -> np.ndarray:
        return self._audio

    def cancel(self) -> None:
        self.cancel_called = True

    def level(self):
        return np.zeros(0, dtype=np.float32), 0.0


class _FakeHotkey:
    """Replaces the pynput-backed listener: nothing may start a real one here."""

    def __init__(self, combo: str) -> None:
        self.combo = combo
        self.started = 0
        self.stopped = 0

    def set_combo(self, combo: str) -> None:
        self.combo = combo

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1


class _FakeEngine:
    """Duck-types SaghiEngine's state and transcribe_array; loads on first use like the real one."""

    def __init__(self) -> None:
        self.is_loaded = False
        self.device = ""
        self.stack_path = ""
        self.calls = 0

    def transcribe_array(self, audio, sr, language="ar", cleanup_level="light"):
        self.calls += 1
        self.is_loaded = True
        self.device = "cpu"
        return TranscribeResult(
            raw_text="خام", text="نص", language=language,
            duration_s=len(audio) / float(sr or 1), inference_s=0.01,
        )


class _Window(QWidget):
    """Main-window stand-in that counts how it is brought forward."""

    def __init__(self) -> None:
        super().__init__()
        self.shown = 0
        self.raised = 0
        self.activated = 0

    def show(self) -> None:
        self.shown += 1

    def raise_(self) -> None:
        self.raised += 1

    def activateWindow(self) -> None:  # noqa: N802 -- Qt naming
        self.activated += 1


# ---- 1. status source ------------------------------------------------------------

print("\n--- 1. DictationController status ---")

paths.ensure_dirs()
engine = _FakeEngine()
engine_status = EngineStatusBridge(engine)
settings_manager = SettingsManager()
settings_manager.update(sound_feedback=False, autopaste=False, save_recordings=False, openrouter_enabled=False)

_SpyPill.calls.clear()
controller = DictationController(engine, engine_status, settings_manager)
controller._hotkey = _FakeHotkey(settings_manager.current.hotkey)
pill = controller.indicator

emitted: list = []
controller.status_changed.connect(lambda state, message: emitted.append((state, message)))

check(controller.status == "ready" and controller.status_message == "", "a fresh controller is ready")

# Silence guard rails only need a fake recorder; the flow itself is the real one.
short = _FakeRecorder(np.zeros(int(16000 * 0.1), dtype=np.float32))
controller.recorder = short
controller._on_hold_started()
check(controller.status == "recording", "hold_started -> recording")
check(emitted == [("recording", "")], "recording is emitted once")
controller._on_hold_ended()
check(controller.status == "ready", "a too-short recording goes straight back to ready")
check(emitted == [("recording", ""), ("ready", "")], "recording then ready, one emission each")
check(("status", "ready") in _SpyPill.calls, "the pill was told its idle face is ready")

emitted.clear()
controller.recorder = _FakeRecorder(np.zeros(16000, dtype=np.float32))
controller._on_hold_started()
orig_show_error = pill.show_error
pill.show_error = lambda message, auto_hide_ms=2500: orig_show_error(message, auto_hide_ms=40)
controller._on_hold_ended()
check(controller.status == "error", "bit-exact silence -> error")
check(controller.status_message == strings.INDICATOR_ERROR_MIC, "the error carries the pill's error text")
check(strings.status_line(controller.status, controller.status_message) == strings.INDICATOR_ERROR_MIC,
      "status_line shows that message for the error")
check(emitted == [("recording", ""), ("error", strings.INDICATOR_ERROR_MIC)], "recording then error emitted")
controller._show_error(strings.INDICATOR_ERROR_GENERIC)
check(controller.status_message == strings.INDICATOR_ERROR_GENERIC,
      "a new error message while the pill already shows an error is picked up")
check(emitted[-1] == ("error", strings.INDICATOR_ERROR_GENERIC), "...and emitted (the message is part of the status)")
check(wait_until(lambda: controller.status == "ready"), "error goes back to ready after the auto-hide")
check(controller.status_message == "", "the message is cleared with the error")
check(emitted[-1] == ("ready", ""), "ready is emitted after the error")
check(len(emitted) == 4, "recording, error, error (new message), ready -- four emissions, no repeats")
pill.show_error = orig_show_error

print("\n--- engine loading / disabled ---")

emitted.clear()
_SpyPill.calls.clear()
engine_status.mark_loading()
check(controller.status == "loading", "engine status bridge 'loading' while idle -> loading")
check(emitted == [("loading", "")], "loading is emitted once")
check(_SpyPill.calls == [("status", "loading")], "the pill's idle face follows to loading")
engine_status.mark_loading()
check(emitted == [("loading", "")], "a repeated loading signal emits nothing more")

controller.set_enabled(False)
check(controller.status == "disabled", "toggling dictation off wins over loading -> disabled")
check(controller._hotkey.stopped == 1, "the hotkey listener was stopped")
controller.set_enabled(False)
check(emitted == [("loading", ""), ("disabled", "")], "disabling twice emits disabled once")
engine_status._state = "ready"
engine_status._emit()
check(controller.status == "disabled", "an engine 'ready' while disabled stays disabled")
controller.set_enabled(True)
check(controller.status == "ready", "toggling dictation back on -> ready")
check(controller._hotkey.started == 1, "the hotkey listener was restarted")
check(emitted[-1] == ("ready", ""), "ready emitted after re-enabling")

# Back to a cold, loading engine for the full-flow check below.
engine_status._state = "cold"
emitted.clear()
engine_status.mark_loading()
check(controller.status == "loading", "cold engine marked loading again -> loading")

print("\n--- full flow with a fake engine: recording -> processing -> ready ---")

emitted.clear()
controller.recorder = _FakeRecorder(np.ones(16000, dtype=np.float32) * 0.01)
with patch("saghi.paste.paste_text") as mock_paste:
    controller._on_hold_started()
    check(controller.status == "recording", "recording outranks the loading engine")
    controller._on_hold_ended()
    check(controller.status == "processing", "hold_ended with audio -> processing")
    worker = controller._worker
    check(worker is not None, "a transcription worker was started")
    check(wait_until(lambda: controller.status == "ready", 10_000), "transcription finished -> ready")
    check(mock_paste.called, "the text went to paste (flow unchanged)")
check(engine.calls == 1 and engine.is_loaded, "the fake engine ran once")
check(emitted == [("recording", ""), ("processing", ""), ("ready", "")],
      "recording, processing, ready -- and no 'loading' flicker once the model is loaded")
check(engine_status.state == "loading", "(the bridge itself has not polled yet)")
emitted.clear()
engine_status._poll()
check(engine_status.state == "ready" and emitted == [], "the bridge polling to ready changes nothing for a ready controller")
check(history.count() >= 1, "the dictation was saved to history (flow unchanged)")
worker.wait(2000)

# ---- 2. pill <-> settings ---------------------------------------------------------

print("\n--- 2. pill <-> settings ---")

_SpyPill.calls.clear()
controller2_settings = SettingsManager()
controller2_settings.update(floating_pill_mode="active", floating_pill_pos=[300, 400], glass_effect=False)
controller2 = DictationController(engine, engine_status, controller2_settings)
controller2._hotkey = _FakeHotkey("ctrl+cmd")
check(("mode", "active") in _SpyPill.calls, "construction pushes the pill mode")
check(("anchor", (300, 400)) in _SpyPill.calls, "construction pushes the saved position as a tuple")
check(("glass", False) in _SpyPill.calls, "construction pushes the glass setting")
controller2.stop()
controller2.deleteLater()
controller2.indicator.deleteLater()
_SpyPill.calls.clear()

_SpyPill.calls.clear()
pill.anchor_moved.emit(120, 340)
check(settings_manager.current.floating_pill_pos == [120, 340], "a drag writes floating_pill_pos")
check(not any(c[0] == "anchor" for c in _SpyPill.calls), "...without moving the pill again (no fighting the drag)")

settings_manager.update(floating_pill_pos=[5, 6])
check(("anchor", (5, 6)) in _SpyPill.calls, "a changed position in settings moves the pill")
_SpyPill.calls.clear()
settings_manager.update(floating_pill_pos=None)
check(("anchor", None) in _SpyPill.calls, "clearing the position (reset) sends None to the pill")
_SpyPill.calls.clear()
settings_manager.update(autopaste=False, cleanup_level="medium")
check(_SpyPill.calls == [], "an unrelated setting touches nothing on the pill")

pill.mode_change_requested.emit("active")
check(settings_manager.current.floating_pill_mode == "active", "the pill's menu writes floating_pill_mode")
check(("mode", "active") in _SpyPill.calls, "...and the change is pushed back to the pill")
pill.mode_change_requested.emit("nonsense")
check(settings_manager.current.floating_pill_mode == "active", "an invalid mode from the pill is ignored")
settings_manager.update(floating_pill_mode="always")
check(("mode", "always") in _SpyPill.calls, "a settings-page change of the mode reaches the pill")
settings_manager.update(glass_effect=False)
check(("glass", False) in _SpyPill.calls, "glass_effect reaches the pill")

opened: list = []
controller.open_app_requested.connect(lambda: opened.append(1))
pill.open_requested.emit()
check(opened == [1], "the pill's open request is re-emitted as open_app_requested")

_SpyPill.calls.clear()
controller.start()
check(controller._hotkey.started == 2, "start() starts the hotkey listener")
check(("hide_indicator",) in _SpyPill.calls, "start() puts the pill at rest (shows it, in 'always' mode)")
_SpyPill.calls.clear()
controller.stop()
last_rest = max(i for i, c in enumerate(_SpyPill.calls) if c == ("hide_indicator",))
check(_SpyPill.calls[-1] == ("hide",) and len(_SpyPill.calls) - 1 > last_rest,
      "stop() hides the pill window completely, after hide_indicator")
check(not pill.isVisible(), "nothing is left on screen after stop()")

# ---- 3. tray -----------------------------------------------------------------------

print("\n--- 3. SaghiTray ---")

settings_manager.update(floating_pill_mode="always")
window = _Window()
quit_calls: list = []
tray = SaghiTray(
    window, engine_status, on_quit=lambda: quit_calls.append(1),
    dictation_controller=controller, settings_manager=settings_manager,
)
controller.open_app_requested.connect(tray.show_main_window)

check(tray.state == controller.status == "ready", "the tray starts on the controller's status")
ready_key = tray.icon().cacheKey()
check(ready_key == tray._frames["ready"][0].cacheKey(), "ready shows the mic frame")
check(tray.icon().isMask(), "the ready icon is a template mask")
check(not tray._animation_timer.isActive(), "no animation while ready")

menu = tray.contextMenu()
actions = menu.actions()
texts = ["-" if a.isSeparator() else a.text() for a in actions]
check(
    texts == [
        strings.status_line("ready"),
        strings.hotkey_hint(settings_manager.current.hotkey),
        "-",
        strings.TRAY_OPEN,
        strings.format_tray_engine_line(engine_status.state, engine_status.device),
        "-",
        strings.TRAY_SHOW_PILL,
        strings.TRAY_DICTATION_TOGGLE,
        strings.TRAY_CHECK_UPDATES,
        "-",
        strings.TRAY_QUIT,
    ],
    "menu order: status, hint, --, open, engine line, --, show pill, dictation, updates, --, quit",
)
check(not actions[0].isEnabled() and not actions[1].isEnabled() and not tray._status_action.isEnabled(),
      "status line, hint and engine line are disabled (display only)")
check(not tray._updates_action.isVisible(), "the updates item is hidden until connect_updates")
check(tray.toolTip() == strings.APP_TITLE + " — " + strings.status_line("ready"), "tooltip = app title + status line")

# One icon per status, only recording is a colour icon.
keys = {}
images = {}
for state in ("ready", "recording", "processing", "loading", "error", "disabled"):
    tray.set_status(state)
    icon = tray.icon()
    keys[state] = icon.cacheKey()
    images[state] = icon.pixmap(18, 18).toImage()
    check(tray._state_action.text() == strings.status_line(state), f"{state}: status line text")
    check(tray.toolTip() == strings.APP_TITLE + " — " + strings.status_line(state), f"{state}: tooltip")
    check(tray.icon().isMask() == (state != "recording"), f"{state}: icon is {'not ' if state == 'recording' else ''}a template mask")
    check(tray._animation_timer.isActive() == (state in ("processing", "loading")), f"{state}: animation timer {'running' if state in ('processing', 'loading') else 'stopped'}")
check(len(set(keys.values())) == 6, "every status has its own icon")
check(all(images[a] != images[b] for a in images for b in images if a < b), "and the rendered images all differ")

# The animation really ticks.
tray.set_status("processing")
first_frames = {tray.icon().cacheKey()}
for _ in range(len(tray._frames["processing"]) - 1):
    tray._on_animation_tick()
    first_frames.add(tray.icon().cacheKey())
check(len(first_frames) == 4, "processing cycles through 4 different waveform frames")
tray._on_animation_tick()
check(tray.icon().cacheKey() == tray._frames["processing"][0].cacheKey(), "...and wraps around")
seen = {tray.icon().cacheKey()}
for _ in range(10):
    wait_ms(60)
    seen.add(tray.icon().cacheKey())
check(len(seen) >= 3, "the real timer advances the frames by itself")
check(tray._animation_timer.interval() in range(120, 220), "the timer runs at roughly 6 fps")

tray.set_status("loading")
check(len(tray._frames["loading"]) == 2, "loading pulses between two frames")
bright, dim = tray._frames["loading"]
check(bright.isMask() and dim.isMask(), "both hourglass frames are template masks")
alpha_bright = bright.pixmap(18, 18).toImage()
alpha_dim = dim.pixmap(18, 18).toImage()
max_bright = max(alpha_bright.pixelColor(x, y).alpha() for x in range(alpha_bright.width()) for y in range(alpha_bright.height()))
max_dim = max(alpha_dim.pixelColor(x, y).alpha() for x in range(alpha_dim.width()) for y in range(alpha_dim.height()))
check(0 < max_dim < max_bright, "the dim hourglass frame has lower alpha than the bright one")

tray.set_status("ready")
check(not tray._animation_timer.isActive(), "going back to ready stops the timer")
check(tray.icon().cacheKey() == ready_key, "...and restores the mic icon")
settled = tray.icon().cacheKey()
wait_ms(400)
check(tray.icon().cacheKey() == settled, "the icon no longer changes once ready")

tray.set_status("error", "رسالة مخصصة")
check(tray._state_action.text() == "رسالة مخصصة", "an error message replaces the generic error line")
check("رسالة مخصصة" in tray.toolTip(), "...and shows in the tooltip")
tray.set_status("error", "")
check(tray._state_action.text() == strings.status_line("error"), "an error without a message shows the generic line")
tray.set_status("bogus")
check(tray.state == "ready", "an unknown status falls back to ready")

print("\n--- tray follows the controller ---")

controller.recorder = _FakeRecorder(np.zeros(int(16000 * 0.1), dtype=np.float32))
controller._on_hold_started()
check(tray.state == "recording" and not tray.icon().isMask(), "controller recording -> red (colour) tray icon")
check(tray._state_action.text() == strings.status_line("recording"), "...with the recording status line")
controller._on_hold_ended()
check(tray.state == "ready", "controller back to ready -> tray ready")
controller.set_enabled(False)
check(tray.state == "disabled" and tray._state_action.text() == strings.status_line("disabled"), "controller disabled -> mic.slash + 'الإملاء متوقف'")
controller.set_enabled(True)
check(tray.state == "ready", "controller re-enabled -> tray ready")

print("\n--- dictation toggle, engine line, updates, open ---")

tray._dictation_action.setChecked(False)
check(controller.enabled is False and tray.state == "disabled", "unchecking 'تفعيل الإملاء' disables dictation and the tray follows")
tray._dictation_action.setChecked(True)
check(controller.enabled is True and tray.state == "ready", "checking it enables dictation again")

engine.is_loaded = False
engine_status._state = "cold"
engine_status.mark_loading()
check(tray._status_action.text() == strings.format_tray_engine_line("loading", ""), "the engine line still follows the engine status bridge")
check(tray.state == "loading", "...and the status line follows it too (through the controller)")
engine_status._state = "ready"
engine_status._emit()
check(tray.state == "ready", "engine ready -> tray ready")

window.shown = window.raised = window.activated = 0
tray._show_main_window()
check((window.shown, window.raised, window.activated) == (1, 1, 1), "_show_main_window brings the window forward")
tray.show_main_window()
check((window.shown, window.raised, window.activated) == (2, 2, 2), "show_main_window does the same (public)")
pill.open_requested.emit()
check(window.shown == 3, "the pill's open request reaches the main window through the tray")
from PySide6.QtWidgets import QSystemTrayIcon  # noqa: E402

tray._on_activated(QSystemTrayIcon.ActivationReason.Trigger)
check(window.shown == 4, "a click on the tray icon still opens the window")
tray._on_activated(QSystemTrayIcon.ActivationReason.Context)
check(window.shown == 4, "a context-menu activation does not")


class _FakeUpdates(QObject):
    update_found = Signal(object)
    opened = 0

    def open_dialog(self) -> None:
        _FakeUpdates.opened += 1


fake_updates = _FakeUpdates()
tray.connect_updates(fake_updates)
check(tray._updates_action.isVisible(), "connect_updates shows the updates item")
tray._updates_action.trigger()
check(_FakeUpdates.opened == 1, "...and it opens the update dialog")

print("\n--- 'always show the pill' toggle <-> settings ---")

settings_manager.update(floating_pill_mode="always")
check(tray._pill_action.isCheckable() and tray._pill_action.isChecked(), "checked while the mode is 'always'")
tray._pill_action.trigger()
check(settings_manager.current.floating_pill_mode == "active", "unchecking it writes floating_pill_mode='active'")
check(("mode", "active") in _SpyPill.calls, "...which the controller pushed into the pill")
tray._pill_action.trigger()
check(settings_manager.current.floating_pill_mode == "always", "checking it writes 'always'")
settings_manager.update(floating_pill_mode="active")
check(not tray._pill_action.isChecked(), "a settings change to 'active' unchecks the menu item")
settings_manager.update(floating_pill_mode="always")
check(tray._pill_action.isChecked(), "and back to 'always' checks it")
pill.mode_change_requested.emit("active")
check(not tray._pill_action.isChecked(), "the pill's own 'hide when idle' item unchecks it too")
settings_manager.update(floating_pill_mode="always")

settings_manager.update(hotkey="alt+cmd")
check(actions[1].text() == strings.hotkey_hint("alt+cmd"), "the hint line follows a changed hotkey")
check(controller._hotkey.combo == "alt+cmd", "(and the controller followed it too)")

print("\n--- the old constructor still works ---")

bare_window = _Window()
bare = SaghiTray(bare_window, engine_status, on_quit=lambda: None)
check(bare.state == "ready", "no controller: starts ready")
check(bare.icon().cacheKey() == bare._frames["ready"][0].cacheKey() and bare.icon().isMask(), "...with the mic icon")
check(bare._status_action.text() == strings.format_tray_engine_line(engine_status.state, engine_status.device),
      "the engine line is there")
bare_texts = ["-" if a.isSeparator() else a.text() for a in bare.contextMenu().actions()]
check(strings.status_line("ready") in bare_texts, "the status line is shown")
check(not any(t.startswith("اضغط") for t in bare_texts), "no hotkey hint without settings")
check(not bare._pill_action.isEnabled(), "the pill toggle is inert without settings")
check(not bare._dictation_action.isEnabled(), "the dictation toggle is inert without a controller")
bare.set_status("recording")
check(bare.state == "recording" and not bare.icon().isMask(), "set_status works on its own")
bare.set_status("ready")
check(not bare._animation_timer.isActive(), "and stops its timer")

print(f"\nAll {_checks} checks passed.")

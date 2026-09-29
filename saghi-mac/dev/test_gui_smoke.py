#!/usr/bin/env python3
"""
Phase 4 GUI smoke + settings + history tests. Offscreen, no model load, no
real inference -- fast (a few seconds). Covers Phase 4 task checks 1-3:

  1. Import/instantiate smoke test: app builds, all pages construct, RTL
     direction set, no exceptions.
  2. Settings round-trip: change values programmatically -> saved JSON
     matches -> reload -> UI (a fresh SettingsPage) reflects them.
  3. History page: seed a scratch db with Arabic entries (incl. one with
     English words mixed in) -> search finds/filters -> copy button puts
     text on the clipboard.

Also covers FileJobPage's signal-handling slots directly (resume notice,
finished/failed UI updates) with zero inference and zero real worker
threads -- these are exercised by calling the private `_on_progress` /
`_on_finished` / `_on_failed` slots with synthetic arguments, the same way
Qt itself would call them once a real FileJobWorker's signals fire. This
is deliberately separate from dev/test_gui_filejob.py's one real
end-to-end run: it's here to catch a wiring bug in the slots themselves
(e.g. the resume-notice condition, or a typo in which widget a signal
updates) without spending any of the one-real-inference-run budget on it.

Run:
    QT_QPA_PLATFORM=offscreen SAGHI_DATA_DIR=<scratch dir> \
        PYTHONPATH=. <venv>/bin/python dev/test_gui_smoke.py

Plain assert-based, no pytest -- matches dev/test_cleanup.py and
dev/test_segmentation.py's existing style in this project.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

assert os.environ.get("SAGHI_DATA_DIR"), "Run with SAGHI_DATA_DIR set to a scratch directory (never the real data dir)"

# Phase 6: SettingsPage now reads/writes the OpenRouter API key via the real
# macOS Keychain (saghi/openrouter.py). Isolate this test's Keychain traffic
# from the real "Saghi" service the same way SAGHI_DATA_DIR isolates its
# settings.json/history.db -- SAGHI_DATA_DIR does NOT cover the Keychain,
# which is a separate store, so this env var is set independently.
os.environ.setdefault("SAGHI_KEYCHAIN_SERVICE", "Saghi-test-gui-smoke")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from unittest.mock import patch  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from saghi import history, openrouter, settings  # noqa: E402
from saghi.ui import strings  # noqa: E402
from saghi.ui.app import build  # noqa: E402
from saghi.ui.settings_page import SettingsPage  # noqa: E402

_checks = 0


def check(condition: bool, message: str) -> None:
    global _checks
    assert condition, f"FAILED: {message}"
    _checks += 1
    print(f"ok: {message}")


# ---- 1. import/instantiate smoke test --------------------------------------

print("--- Test 1: import/instantiate smoke test ---")

ctx = build([])

check(ctx.app.layoutDirection() == Qt.LayoutDirection.RightToLeft, "QApplication layoutDirection is RightToLeft")
check(ctx.main_window.windowTitle() == "صاغي", "main window title is صاغي")
check(ctx.main_window.history_page is not None, "history page constructed")
check(ctx.main_window.filejob_page is not None, "file job page constructed")
check(ctx.main_window.settings_page is not None, "settings page constructed")
check(ctx.engine_status.state == "cold", "engine status starts cold (model not loaded at startup)")
check(ctx.engine.is_loaded is False, "engine.is_loaded is False at startup (never loads eagerly)")
check(ctx.main_window.nav.count() == 3, "sidebar has exactly 3 nav entries")

# Switching nav pages must not raise.
for row in range(3):
    ctx.main_window.nav.setCurrentRow(row)
    check(ctx.main_window.pages.currentIndex() == row, f"switching to nav row {row} updates the stacked page")

ctx.quit_fn()
print("Test 1 passed.\n")


# ---- 2. settings round-trip --------------------------------------------------

print("--- Test 2: settings round-trip ---")

sm = settings.SettingsManager()
new_values = dict(
    hotkey="alt+cmd",
    microphone="Test Microphone",
    cleanup_level="medium",
    autopaste=False,
    save_recordings=True,
    waveform_style="pulse",
    waveform_color="#FF5733",
    language="en",
    launch_at_login=True,
    openrouter_enabled=True,
    openrouter_model="google/gemini-3.1-flash-lite",
    openrouter_instructions="حوّل كلامي إلى بريد مهني مختصر، ولا تضف معلومات",
)
sm.update(**new_values)

# saved JSON matches
saved_path = settings.settings_path()
check(saved_path.exists(), "settings.json was written")
with open(saved_path, "r", encoding="utf-8") as f:
    on_disk = json.load(f)
for key, value in new_values.items():
    check(on_disk[key] == value, f"settings.json field {key!r} == {value!r}")

# reload -> a fresh SettingsManager sees the same values
sm2 = settings.SettingsManager()
for key, value in new_values.items():
    check(getattr(sm2.current, key) == value, f"reloaded Settings.{key} == {value!r}")

# UI reflects: a fresh SettingsPage built from the reloaded manager shows the saved values
page = SettingsPage(sm2)
check(page.hotkey_combo.currentData() == "alt+cmd", "SettingsPage hotkey combo reflects saved value")
# "Test Microphone" isn't a real enumerated device (sounddevice isn't
# installed in this dev venv by design -- see settings_page.py's module
# docstring), so the combo can only offer "الافتراضي"; falling back to it
# rather than silently disappearing the saved value is the correct
# behavior being checked here.
check(page.mic_combo.currentData() is None, "SettingsPage mic combo falls back to default when the saved device isn't in the enumerated list")
check(page.cleanup_combo.currentData() == "medium", "SettingsPage cleanup combo reflects saved value")
check(page.language_combo.currentData() == "en", "SettingsPage language combo reflects saved value")
check(page.autopaste_check.isChecked() is False, "SettingsPage autopaste checkbox reflects saved value")
check(page.save_rec_check.isChecked() is True, "SettingsPage save-recordings checkbox reflects saved value")
check(page.launch_check.isChecked() is True, "SettingsPage launch-at-login checkbox reflects saved value")
check(page.waveform_style_combo.currentData() == "pulse", "SettingsPage waveform style combo reflects saved value")
check(page._color_value == "#FF5733", "SettingsPage waveform color reflects saved value")
check(page.openrouter_enabled_check.isChecked() is True, "SettingsPage OpenRouter-enabled checkbox reflects saved value")
check(
    page.openrouter_model_edit.text() == "google/gemini-3.1-flash-lite",
    "SettingsPage OpenRouter model field reflects saved value",
)
check(
    page.openrouter_instructions_edit.toPlainText() == "حوّل كلامي إلى بريد مهني مختصر، ولا تضف معلومات",
    "SettingsPage OpenRouter instructions field reflects saved value",
)

# Missing/corrupt file falls back to defaults without raising.
saved_path.write_text("{not valid json", encoding="utf-8")
sm3 = settings.SettingsManager()
check(sm3.current.hotkey == "ctrl+cmd", "corrupt settings.json falls back to the default hotkey")
check(sm3.current.cleanup_level == "light", "corrupt settings.json falls back to the default cleanup level")
check(sm3.current.openrouter_enabled is False, "corrupt settings.json falls back to the default openrouter_enabled")
check(sm3.current.openrouter_model == "", "corrupt settings.json falls back to the default openrouter_model")

# Phase 6 regression check: an OLD settings.json from before this phase
# (missing openrouter_enabled/openrouter_model/openrouter_instructions
# entirely) must still load cleanly with sane defaults for the new fields,
# while every pre-existing field it DOES have is preserved exactly --
# this is the concrete "old settings.json files still load" check the
# task asks for, not just the already-covered "corrupt file" case above.
old_format_settings = {
    "hotkey": "alt+cmd",
    "microphone": None,
    "cleanup_level": "medium",
    "autopaste": False,
    "save_recordings": True,
    "waveform_style": "dots",
    "waveform_color": "#123456",
    "language": "en",
    "launch_at_login": True,
    # no openrouter_* keys at all -- simulates a pre-Phase-6 file
}
saved_path.write_text(json.dumps(old_format_settings, ensure_ascii=False), encoding="utf-8")
sm4 = settings.SettingsManager()
check(sm4.current.hotkey == "alt+cmd", "old-format settings.json: pre-existing field 'hotkey' preserved")
check(sm4.current.cleanup_level == "medium", "old-format settings.json: pre-existing field 'cleanup_level' preserved")
check(sm4.current.waveform_color == "#123456", "old-format settings.json: pre-existing field 'waveform_color' preserved")
check(sm4.current.openrouter_enabled is False, "old-format settings.json: missing openrouter_enabled defaults to False, no crash")
check(sm4.current.openrouter_model == "", "old-format settings.json: missing openrouter_model defaults to '', no crash")
check(sm4.current.openrouter_instructions == "", "old-format settings.json: missing openrouter_instructions defaults to '', no crash")
# A page can still be built from this reloaded manager without raising.
page4 = SettingsPage(sm4)
check(page4.openrouter_enabled_check.isChecked() is False, "SettingsPage built from an old-format file shows the openrouter checkbox unchecked")

print("Test 2 passed.\n")


# ---- 3. history page: search + copy -----------------------------------------

print("--- Test 3: history page search + copy ---")

history.add_entry(
    source="cli", language="ar", cleanup_level="light", duration_s=3.5, inference_s=1.2,
    raw_text="ففي الحالة دي المسألة دي يعني um more safe",
    text="ففي الحالة دي المسألة دي يعني more safe",
    audio_filename="sample1.wav",
)
history.add_entry(
    source="filejob", language="ar", cleanup_level="light", duration_s=340.0, inference_s=88.2,
    raw_text="هذا اجتماع طويل يتحدث عن خطة التسويق",
    text="هذا اجتماع طويل يتحدث عن خطة التسويق",
    audio_filename="quarterly-review.wav",
)
history.add_entry(
    source="api", language="ar", cleanup_level="medium", duration_s=5.6, inference_s=1.9,
    raw_text="جدولة اجتماع مع فريق Zoom يوم الأحد",
    text="جدولة اجتماع مع فريق Zoom يوم الأحد",
    audio_filename="voice-note.wav",
)

app = QApplication.instance() or QApplication(sys.argv)
from saghi.ui.history_page import HistoryPage  # noqa: E402

hp = HistoryPage()
check(hp.results_list.count() == 3, "history page lists all 3 seeded entries")

hp.search_box.setText("Zoom")
check(hp.results_list.count() == 1, "search for 'Zoom' (mixed English inside Arabic text) filters to 1 entry")

hp.search_box.setText("التسويق")
check(hp.results_list.count() == 1, "search for an Arabic term filters to 1 entry")
check("التسويق" in hp.detail_view.toPlainText(), "detail view shows the matched entry's text")

hp.search_box.setText("")
check(hp.results_list.count() == 3, "clearing the search shows all entries again")

hp.results_list.setCurrentRow(0)
hp.raw_toggle_btn.setChecked(True)
check(hp.detail_view.toPlainText() != "", "raw-text toggle shows non-empty raw text")
hp.raw_toggle_btn.setChecked(False)

hp._copy_text()
clipboard_text = QApplication.clipboard().text()
check(clipboard_text == hp.detail_view.toPlainText(), "copy button puts the shown text on the clipboard")
check(len(clipboard_text) > 0, "clipboard text is non-empty after copy")

print("Test 3 passed.\n")


# ---- 4. FileJobPage slot wiring (resume notice, finished/failed) -- zero inference ----

print("\n--- Test 4: FileJobPage slot wiring (no inference, no real worker) ---")

from saghi.filejobs import FileJobResult  # noqa: E402
from saghi.ui.filejob_page import FileJobPage  # noqa: E402

fj = FileJobPage(engine=None)  # no real engine needed -- these slots never touch it
# QWidget.isVisible() reflects real on-screen visibility, which requires the
# widget to actually be shown at least once -- a freshly-constructed,
# never-.show()'d widget reports isVisible()==False regardless of any
# child's setVisible(True) call. .show() works fine under the offscreen
# platform (same as dev/grab_screens.py).
fj.show()
app.processEvents()

# _on_progress: first progress event of a run with done==0 must NOT show the resume notice.
check(fj.resume_label.isVisible() is False, "resume label starts hidden")
fj._on_progress(0, 3, 0.0, 0.0, None)
check(fj.resume_label.isVisible() is False, "first progress event with done=0 does not show the resume notice")

# A fresh run (simulated by resetting the tracking flag, exactly as _on_start_clicked does)
# whose FIRST progress event reports done>0 must show the resume notice immediately.
fj._first_progress_seen = False
fj._on_progress(1, 3, 33.3, 0.0, None)
check(fj.resume_label.isVisible() is True, "first progress event with done>0 shows the resume notice (سيتم الاستكمال من آخر جزء محفوظ)")
check(fj.chunk_label.text() == strings.filejob_chunk_progress(1, 3), "chunk label reflects the progress event")

# A LATER progress event with done>0 (not the first of the run) must NOT re-trigger the notice logic
# incorrectly hiding it -- but also must not be what shows it in the first place.
fj._on_progress(2, 3, 66.6, 45.0, 20.0)
check(fj.resume_label.isVisible() is True, "resume notice stays visible through the rest of the run")

# _on_finished: normal completion.
stub_dir = Path(REPO_ROOT) / "dev" / "_smoke_stub_jobdir"
normal_result = FileJobResult(
    job_id="stub", job_dir=stub_dir, text="النص النهائي", raw_text="النص الخام",
    segments=[], total_duration_s=23.0, total_chunks=3, resumed=False,
    resumed_from_chunk=0, history_id=1, cancelled=False,
)
fj._on_finished(normal_result)
check(fj.result_view.toPlainText() == "النص النهائي", "_on_finished shows the result text")
check(fj.open_folder_btn.isEnabled() is True, "_on_finished enables the open-folder button")
check(fj.copy_result_btn.isEnabled() is True, "_on_finished enables the copy button")
check(fj.start_btn.isEnabled() is False, "_on_finished with no file picked leaves start disabled (no picked_path)")

# _on_finished: cancelled completion shows the cancellation notice.
cancelled_result = FileJobResult(
    job_id="stub", job_dir=stub_dir, text="نص جزئي", raw_text="خام جزئي",
    segments=[], total_duration_s=23.0, total_chunks=3, resumed=False,
    resumed_from_chunk=0, history_id=None, cancelled=True,
)
fj.resume_label.setVisible(True)
fj._on_finished(cancelled_result)
check(strings.FILEJOB_CANCELLED_NOTICE in fj.result_view.toPlainText(), "_on_finished shows the cancellation notice when result.cancelled is True")
check("نص جزئي" in fj.result_view.toPlainText(), "_on_finished still shows the partial text alongside the cancellation notice")
check(fj.resume_label.isVisible() is False, "_on_finished hides the resume notice once the run ends")

# _on_failed
fj._on_failed("network unreachable")
check(fj.result_view.toPlainText() == strings.FILEJOB_ERROR_PREFIX + "network unreachable", "_on_failed shows the error prefix + message")
check(fj.cancel_btn.isEnabled() is False, "_on_failed leaves cancel disabled (run is over)")

print("Test 4 passed.\n")


# ---- 5. Engine status bridge -> mainwindow chip + tray line (zero inference) ----

print("\n--- Test 5: engine status bridge drives the header chip and tray line ---")

from PySide6.QtGui import QIcon  # noqa: E402

from saghi.ui.engine_status import EngineStatusBridge  # noqa: E402
from saghi.ui.mainwindow import MainWindow  # noqa: E402
from saghi.ui.tray import _ICON_PATH, SaghiTray  # noqa: E402


class _StubEngine:
    """Duck-typed stand-in -- EngineStatusBridge only ever reads .is_loaded/.device/.stack_path."""

    def __init__(self) -> None:
        self.is_loaded = False
        self.device = None
        self.stack_path = None


check(not QIcon(str(_ICON_PATH)).isNull(), "the copied tray icon asset loads as a real (non-null) QIcon")

stub_engine = _StubEngine()
bridge = EngineStatusBridge(stub_engine)
check(bridge.state == "cold", "bridge starts cold when engine.is_loaded is False")

sm5 = settings.SettingsManager()
win5 = MainWindow(sm5, stub_engine, bridge)
tray5 = SaghiTray(win5, bridge, on_quit=lambda: None)

check(win5.status_chip.text() == strings.ENGINE_COLD, "header chip shows بارد before anything loads")
check(
    tray5._status_action.text() == strings.format_tray_engine_line("cold", ""),
    "tray status line shows المحرك: بارد before anything loads",
)

# mark_loading() -- what FileJobPage calls right before starting a worker.
# Signal delivery is synchronous here (same-thread direct connection, no
# event loop needed) so no app.processEvents() call is required.
bridge.mark_loading()
check(bridge.state == "loading", "bridge.mark_loading() transitions state to loading")
check(win5.status_chip.text() == strings.ENGINE_LOADING, "header chip updates to جارٍ التحميل")
check(
    tray5._status_action.text() == strings.format_tray_engine_line("loading", ""),
    "tray status line updates to المحرك: جارٍ التحميل",
)

# The 1s QTimer poll -- called directly (deterministic) rather than waiting
# on the real timer interval inside a test.
stub_engine.is_loaded = True
stub_engine.device = "cpu"
stub_engine.stack_path = "fallback"
bridge._poll()
check(bridge.state == "ready", "bridge._poll() transitions state to ready once engine.is_loaded is True")
check(
    win5.status_chip.text() == strings.format_engine_status("ready", "cpu"),
    "header chip updates to the CPU جاهز form (device isolated per the mixed-text rule)",
)
check(
    tray5._status_action.text() == strings.format_tray_engine_line("ready", "cpu"),
    "tray status line updates to المحرك: CPU جاهز",
)
check("CPU" in tray5._status_action.text(), "the isolated device name is still findable as a plain substring (LRI/PDI are invisible formatting chars)")

print("Test 5 passed.\n")


# ---- 6. OpenRouter settings controls (Phase 6) --------------------------------

print("--- Test 6: OpenRouter settings controls ---")

sm6 = settings.SettingsManager()
page6 = SettingsPage(sm6)

# Save/delete key buttons against the REAL macOS Keychain -- isolated to
# this test's own service (SAGHI_KEYCHAIN_SERVICE set at module top), so
# this genuinely exercises the same openrouter.save_api_key/delete_api_key
# calls the real app makes, without ever touching a real user's stored key.
check(openrouter.load_api_key() is None, "no key stored yet under the test Keychain service (clean starting state)")
check(
    page6.openrouter_key_edit.placeholderText() == strings.SETTINGS_OPENROUTER_KEY_EMPTY_PLACEHOLDER,
    "key field shows the 'not saved' placeholder before any key exists",
)

page6.openrouter_key_edit.setText("sk-or-gui-test-key")
page6._on_save_key()
check(openrouter.load_api_key() == "sk-or-gui-test-key", "Save button writes the typed key to the real Keychain")
check(page6.openrouter_key_edit.text() == "", "key field is cleared right after saving (never keeps the real key visible)")
check(
    page6.openrouter_key_edit.placeholderText() == strings.SETTINGS_OPENROUTER_KEY_SAVED_PLACEHOLDER,
    "key field placeholder switches to the '(محفوظ)' saved-state text after saving",
)

page6._on_delete_key()
check(openrouter.load_api_key() is None, "Delete button removes the key from the real Keychain")
check(
    page6.openrouter_key_edit.placeholderText() == strings.SETTINGS_OPENROUTER_KEY_EMPTY_PLACEHOLDER,
    "key field placeholder reverts to 'not saved' after deleting",
)

# A SECOND SettingsPage built while a key IS saved must show the saved-state
# placeholder from construction, not just after a save click in the same
# session (this is the "loads existing-key-present state from Keychain as a
# placeholder" requirement).
openrouter.save_api_key("sk-or-gui-test-key-2")
page6b = SettingsPage(sm6)
check(
    page6b.openrouter_key_edit.placeholderText() == strings.SETTINGS_OPENROUTER_KEY_SAVED_PLACEHOLDER,
    "a freshly-constructed SettingsPage shows the saved-state placeholder when a key already exists",
)
check(page6b.openrouter_key_edit.text() == "", "a freshly-constructed SettingsPage never pre-fills the real key into the field")
openrouter.delete_api_key()  # clean up before the next check re-tests the empty state

# Model id field: editingFinished saves immediately (mirrors the codebase's
# other combo/checkbox controls' immediate-save semantics).
page6.openrouter_model_edit.setText("openrouter/test-model")
page6.openrouter_model_edit.editingFinished.emit()
check(sm6.current.openrouter_model == "openrouter/test-model", "model field save-on-editingFinished writes through SettingsManager")

# Instructions field: focus-out saves immediately (see _FocusOutPlainTextEdit).
page6.openrouter_instructions_edit.setPlainText("تعليمات تجريبية")
page6.openrouter_instructions_edit.focus_lost.emit()
check(sm6.current.openrouter_instructions == "تعليمات تجريبية", "instructions field save-on-focus-out writes through SettingsManager")

# Enabled checkbox: immediate save, same as every other checkbox on this page.
page6.openrouter_enabled_check.setChecked(True)
check(sm6.current.openrouter_enabled is True, "enabled checkbox save-on-toggle writes through SettingsManager")

# Test-connection button: runs on a worker thread (never blocks the UI on
# network), and the network call itself is MOCKED here -- per the task's
# explicit instruction not to hit the real network even in this GUI test.
# Patching saghi.openrouter.test_connection (the module attribute
# settings_page.py's _TestConnectionWorker looks up at call time, since
# settings_page.py does `from .. import openrouter` and calls
# `openrouter.test_connection(...)`, not `from ..openrouter import
# test_connection`) redirects the worker without touching any real network
# code path.
qapp = QApplication.instance()

with patch("saghi.openrouter.test_connection") as mock_test_connection:
    mock_test_connection.return_value = openrouter.RephraseResult(ok=True, text="OK", error=None)

    check(page6.openrouter_test_btn.isEnabled() is True, "test-connection button starts enabled")
    page6._on_test_connection()
    check(page6.openrouter_test_btn.isEnabled() is False, "test-connection button disables itself immediately on click (never re-clickable mid-request)")
    check(
        page6.openrouter_test_result_label.text() == strings.SETTINGS_OPENROUTER_TEST_RUNNING,
        "result label shows the 'running' text immediately, before the worker thread finishes",
    )

    deadline = time.time() + 5.0
    while page6.openrouter_test_btn.isEnabled() is False and time.time() < deadline:
        qapp.processEvents()

    check(mock_test_connection.called, "the worker thread actually called the (mocked) openrouter.test_connection")
    check(page6.openrouter_test_btn.isEnabled() is True, "test-connection button re-enables once the worker finishes")
    check(
        page6.openrouter_test_result_label.text() == strings.SETTINGS_OPENROUTER_TEST_SUCCESS,
        "result label shows the success text for a mocked ok=True result",
    )

with patch("saghi.openrouter.test_connection") as mock_test_connection:
    mock_test_connection.return_value = openrouter.RephraseResult(ok=False, text="ping", error="HTTP 401: invalid key")

    page6._on_test_connection()
    deadline = time.time() + 5.0
    while page6.openrouter_test_btn.isEnabled() is False and time.time() < deadline:
        qapp.processEvents()

    check(
        page6.openrouter_test_result_label.text() == strings.openrouter_test_failure("HTTP 401: invalid key"),
        f"result label shows the formatted failure text for a mocked ok=False result (got {page6.openrouter_test_result_label.text()!r})",
    )

print("Test 6 passed.\n")

print(f"ALL PASSED ({_checks} checks) -- including FileJobPage slot wiring, the engine-status bridge, and OpenRouter settings controls")

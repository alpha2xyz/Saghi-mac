"""
Settings page: live-editing controls for every saghi.settings.Settings
field. Every control writes through SettingsManager.update() immediately
on change -- no separate "Save" button, matching settings.py's atomic-
write-on-every-change design.

Two fields are deliberately placeholders in this Phase 4 build, per the
task's explicit scope (Phase 5 owns real audio/hotkeys):

  * hotkey     -- real global-hotkey *capture* needs pynput. This page only
    offers a fixed preset combo (ctrl+cmd / alt+cmd) -- README_AR.md's own
    documented default is Ctrl+Cmd, and "fn" is not reliably hookable
    cross-app on macOS, so it is intentionally not offered.
  * microphone -- real device enumeration needs `sounddevice`, which this
    phase deliberately does NOT add as a dependency. If `sounddevice`
    happens to already be importable in the running environment, its
    input-device list is shown; otherwise a single "الافتراضي" (system
    default) entry is offered.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from .. import openrouter
from ..settings import SettingsManager
from . import strings

_HOTKEY_PRESETS = ["ctrl+cmd", "alt+cmd"]


class _FocusOutPlainTextEdit(QPlainTextEdit):
    """
    Plain QPlainTextEdit has no editingFinished signal (QLineEdit's
    immediate-save trigger for the model-id field), and saving on every
    textChanged keystroke would mean a disk write per character for the
    free-form instructions box -- this small subclass emits `focus_lost`
    on focus-out instead, giving the instructions field the same
    "immediate save, not per-keystroke" semantics as every other control
    on this page. Same "small custom QWidget subclass" pattern
    filejob_page.py already uses for `_DropCard`.
    """

    focus_lost = Signal()

    def focusOutEvent(self, event) -> None:  # noqa: N802 -- Qt override
        super().focusOutEvent(event)
        self.focus_lost.emit()


class _TestConnectionWorker(QThread):
    """
    Runs openrouter.test_connection() on a background thread -- never block
    the UI on network I/O. Mirrors dictation.py's `_TranscribeWorker` /
    filejob_page.py's `FileJobWorker` in shape: one call, one result signal.
    """

    finished_result = Signal(object)  # openrouter.RephraseResult

    def __init__(self, model: str, parent=None) -> None:
        super().__init__(parent)
        self._model = model

    def run(self) -> None:  # noqa: N802 -- QThread override
        result = openrouter.test_connection(self._model)
        self.finished_result.emit(result)


def _list_microphones() -> List[Tuple[Optional[str], str]]:
    """[(device_name_or_None, display_label), ...]; first entry is always the system-default placeholder."""
    options: List[Tuple[Optional[str], str]] = [(None, strings.MIC_DEFAULT)]
    try:
        import sounddevice as sd

        for dev in sd.query_devices():
            if dev.get("max_input_channels", 0) > 0:
                name = dev.get("name")
                if name:
                    options.append((name, name))
    except ImportError:
        pass
    except Exception:
        # Device enumeration can fail for many platform-specific reasons
        # (no audio backend, permissions) -- never let it crash the
        # settings page, just fall back to the default-only list.
        pass
    return options


class SettingsPage(QWidget):
    def __init__(self, settings_manager: SettingsManager, parent=None):
        super().__init__(parent)
        self._sm = settings_manager
        s = settings_manager.current

        form = QFormLayout(self)
        form.setContentsMargins(20, 20, 20, 20)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)
        # Cap field width so combos/checkboxes read as a compact settings
        # form instead of stretching across the whole (much wider) window
        # -- the one small QSS/layout touch the task allows, nothing more.
        _FIELD_MAX_WIDTH = 260

        # Hotkey (placeholder preset combo -- see module docstring)
        self.hotkey_combo = QComboBox()
        self.hotkey_combo.setMaximumWidth(_FIELD_MAX_WIDTH)
        for value in _HOTKEY_PRESETS:
            self.hotkey_combo.addItem(strings.HOTKEY_LABELS[value], value)
        self._set_combo_by_data(self.hotkey_combo, s.hotkey, fallback_index=0)
        self.hotkey_combo.currentIndexChanged.connect(self._on_hotkey_changed)
        form.addRow(strings.SETTINGS_HOTKEY_LABEL, self.hotkey_combo)

        # Microphone (placeholder -- see module docstring)
        self._mic_options = _list_microphones()
        self.mic_combo = QComboBox()
        self.mic_combo.setMaximumWidth(_FIELD_MAX_WIDTH)
        for value, label in self._mic_options:
            self.mic_combo.addItem(label, value)
        mic_idx = next((i for i, (v, _l) in enumerate(self._mic_options) if v == s.microphone), 0)
        self.mic_combo.setCurrentIndex(mic_idx)
        self.mic_combo.currentIndexChanged.connect(self._on_mic_changed)
        form.addRow(strings.SETTINGS_MIC_LABEL, self.mic_combo)

        # Cleanup level
        self.cleanup_combo = QComboBox()
        self.cleanup_combo.setMaximumWidth(_FIELD_MAX_WIDTH)
        for level in ("none", "light", "medium"):
            self.cleanup_combo.addItem(strings.CLEANUP_LEVEL_LABELS[level], level)
        self._set_combo_by_data(self.cleanup_combo, s.cleanup_level, fallback_index=1)
        self.cleanup_combo.currentIndexChanged.connect(self._on_cleanup_changed)
        form.addRow(strings.SETTINGS_CLEANUP_LABEL, self.cleanup_combo)

        # Transcription language
        self.language_combo = QComboBox()
        self.language_combo.setMaximumWidth(_FIELD_MAX_WIDTH)
        for lang in ("ar", "en"):
            self.language_combo.addItem(strings.LANGUAGE_LABELS[lang], lang)
        self._set_combo_by_data(self.language_combo, s.language, fallback_index=0)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        form.addRow(strings.SETTINGS_LANGUAGE_LABEL, self.language_combo)

        # Autopaste
        self.autopaste_check = QCheckBox()
        self.autopaste_check.setChecked(s.autopaste)
        self.autopaste_check.toggled.connect(lambda v: self._sm.update(autopaste=v))
        form.addRow(strings.SETTINGS_AUTOPASTE_LABEL, self.autopaste_check)

        # Save recordings
        self.save_rec_check = QCheckBox()
        self.save_rec_check.setChecked(s.save_recordings)
        self.save_rec_check.toggled.connect(lambda v: self._sm.update(save_recordings=v))
        form.addRow(strings.SETTINGS_SAVE_RECORDINGS_LABEL, self.save_rec_check)

        # Launch at login
        self.launch_check = QCheckBox()
        self.launch_check.setChecked(s.launch_at_login)
        self.launch_check.toggled.connect(lambda v: self._sm.update(launch_at_login=v))
        form.addRow(strings.SETTINGS_LAUNCH_AT_LOGIN_LABEL, self.launch_check)

        # Waveform style
        self.waveform_style_combo = QComboBox()
        self.waveform_style_combo.setMaximumWidth(_FIELD_MAX_WIDTH)
        for style in ("bars", "line", "dots", "pulse"):
            self.waveform_style_combo.addItem(strings.WAVEFORM_STYLE_LABELS[style], style)
        self._set_combo_by_data(self.waveform_style_combo, s.waveform_style, fallback_index=0)
        self.waveform_style_combo.currentIndexChanged.connect(self._on_waveform_style_changed)
        form.addRow(strings.SETTINGS_WAVEFORM_STYLE_LABEL, self.waveform_style_combo)

        # Waveform color
        color_row = QHBoxLayout()
        self.color_swatch = QLabel()
        self.color_swatch.setFixedSize(22, 22)
        self._color_value = s.waveform_color
        self._apply_swatch_color(self._color_value)
        color_row.addWidget(self.color_swatch)
        self.color_btn = QPushButton(strings.SETTINGS_WAVEFORM_COLOR_LABEL)
        self.color_btn.clicked.connect(self._on_pick_color)
        color_row.addWidget(self.color_btn)
        color_row.addStretch()
        color_widget = QWidget()
        color_widget.setLayout(color_row)
        form.addRow(strings.SETTINGS_WAVEFORM_COLOR_LABEL, color_widget)

        # ---- OpenRouter rephrase (Phase 6) ---------------------------------

        self.openrouter_enabled_check = QCheckBox()
        self.openrouter_enabled_check.setChecked(s.openrouter_enabled)
        self.openrouter_enabled_check.toggled.connect(lambda v: self._sm.update(openrouter_enabled=v))
        form.addRow(strings.SETTINGS_OPENROUTER_ENABLED_LABEL, self.openrouter_enabled_check)

        # Model id and the API key are both Latin-script identifiers, not
        # Arabic prose -- left-to-right layout direction reads naturally for
        # typing/pasting them, unlike the free-form (often Arabic)
        # instructions box below, which stays at the page's RTL default.
        self.openrouter_model_edit = QLineEdit(s.openrouter_model)
        self.openrouter_model_edit.setMaximumWidth(_FIELD_MAX_WIDTH)
        self.openrouter_model_edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.openrouter_model_edit.setPlaceholderText(strings.SETTINGS_OPENROUTER_MODEL_PLACEHOLDER)
        self.openrouter_model_edit.editingFinished.connect(self._on_openrouter_model_changed)
        form.addRow(strings.SETTINGS_OPENROUTER_MODEL_LABEL, self.openrouter_model_edit)

        key_row = QHBoxLayout()
        self.openrouter_key_edit = QLineEdit()
        self.openrouter_key_edit.setMaximumWidth(_FIELD_MAX_WIDTH)
        self.openrouter_key_edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.openrouter_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._refresh_key_placeholder()
        key_row.addWidget(self.openrouter_key_edit)
        self.openrouter_key_save_btn = QPushButton(strings.SETTINGS_OPENROUTER_SAVE_BUTTON)
        self.openrouter_key_save_btn.clicked.connect(self._on_save_key)
        key_row.addWidget(self.openrouter_key_save_btn)
        self.openrouter_key_delete_btn = QPushButton(strings.SETTINGS_OPENROUTER_DELETE_BUTTON)
        self.openrouter_key_delete_btn.clicked.connect(self._on_delete_key)
        key_row.addWidget(self.openrouter_key_delete_btn)
        key_row.addStretch()
        key_widget = QWidget()
        key_widget.setLayout(key_row)
        form.addRow(strings.SETTINGS_OPENROUTER_KEY_LABEL, key_widget)

        self.openrouter_instructions_edit = _FocusOutPlainTextEdit()
        self.openrouter_instructions_edit.setPlainText(s.openrouter_instructions)
        self.openrouter_instructions_edit.setMaximumHeight(80)
        self.openrouter_instructions_edit.focus_lost.connect(self._on_openrouter_instructions_changed)
        form.addRow(strings.SETTINGS_OPENROUTER_INSTRUCTIONS_LABEL, self.openrouter_instructions_edit)

        test_row = QHBoxLayout()
        self.openrouter_test_btn = QPushButton(strings.SETTINGS_OPENROUTER_TEST_BUTTON)
        self.openrouter_test_btn.clicked.connect(self._on_test_connection)
        test_row.addWidget(self.openrouter_test_btn)
        self.openrouter_test_result_label = QLabel("")
        test_row.addWidget(self.openrouter_test_result_label)
        test_row.addStretch()
        test_widget = QWidget()
        test_widget.setLayout(test_row)
        form.addRow("", test_widget)

        self._openrouter_test_worker: Optional[_TestConnectionWorker] = None

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def _set_combo_by_data(combo: QComboBox, value, fallback_index: int) -> None:
        idx = combo.findData(value)
        combo.setCurrentIndex(idx if idx >= 0 else fallback_index)

    def _apply_swatch_color(self, hex_color: str) -> None:
        self.color_swatch.setStyleSheet(
            f"background-color: {hex_color}; border: 1px solid #888; border-radius: 4px;"
        )

    # ---- change handlers ----------------------------------------------------

    def _on_hotkey_changed(self, index: int) -> None:
        self._sm.update(hotkey=self.hotkey_combo.itemData(index))

    def _on_mic_changed(self, index: int) -> None:
        self._sm.update(microphone=self.mic_combo.itemData(index))

    def _on_cleanup_changed(self, index: int) -> None:
        self._sm.update(cleanup_level=self.cleanup_combo.itemData(index))

    def _on_language_changed(self, index: int) -> None:
        self._sm.update(language=self.language_combo.itemData(index))

    def _on_waveform_style_changed(self, index: int) -> None:
        self._sm.update(waveform_style=self.waveform_style_combo.itemData(index))

    def _on_pick_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._color_value), self, strings.SETTINGS_WAVEFORM_COLOR_LABEL)
        if color.isValid():
            hex_color = color.name()
            self._color_value = hex_color
            self._apply_swatch_color(hex_color)
            self._sm.update(waveform_color=hex_color)

    # ---- OpenRouter rephrase (Phase 6) -------------------------------------

    def _on_openrouter_model_changed(self) -> None:
        self._sm.update(openrouter_model=self.openrouter_model_edit.text().strip())

    def _on_openrouter_instructions_changed(self) -> None:
        self._sm.update(openrouter_instructions=self.openrouter_instructions_edit.toPlainText())

    def _refresh_key_placeholder(self) -> None:
        has_key = openrouter.load_api_key() is not None
        self.openrouter_key_edit.setPlaceholderText(
            strings.SETTINGS_OPENROUTER_KEY_SAVED_PLACEHOLDER
            if has_key
            else strings.SETTINGS_OPENROUTER_KEY_EMPTY_PLACEHOLDER
        )

    def _on_save_key(self) -> None:
        key = self.openrouter_key_edit.text().strip()
        if not key:
            return
        openrouter.save_api_key(key)
        self.openrouter_key_edit.clear()  # never keep the real key visible/in the widget after saving
        self._refresh_key_placeholder()
        self.openrouter_test_result_label.setText("")

    def _on_delete_key(self) -> None:
        openrouter.delete_api_key()
        self.openrouter_key_edit.clear()
        self._refresh_key_placeholder()
        self.openrouter_test_result_label.setText("")

    def _on_test_connection(self) -> None:
        model = self.openrouter_model_edit.text().strip()
        self.openrouter_test_btn.setEnabled(False)
        self.openrouter_test_result_label.setText(strings.SETTINGS_OPENROUTER_TEST_RUNNING)
        worker = _TestConnectionWorker(model, parent=self)
        worker.finished_result.connect(self._on_test_connection_result)
        worker.finished.connect(self._on_test_worker_thread_finished)
        self._openrouter_test_worker = worker
        worker.start()

    def _on_test_connection_result(self, result) -> None:
        self.openrouter_test_btn.setEnabled(True)
        if result.ok:
            self.openrouter_test_result_label.setText(strings.SETTINGS_OPENROUTER_TEST_SUCCESS)
        else:
            self.openrouter_test_result_label.setText(strings.openrouter_test_failure(result.error or ""))

    def _on_test_worker_thread_finished(self) -> None:
        # QThread housekeeping only, same pattern as dictation.py's
        # _on_worker_thread_finished / filejob_page.py's worker teardown --
        # UI state is already fully handled by _on_test_connection_result.
        self._openrouter_test_worker = None

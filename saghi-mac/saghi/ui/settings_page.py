"""
Settings page: live-editing controls for every saghi.settings.Settings
field. Every control writes through SettingsManager.update() immediately
on change -- no separate "Save" button, matching settings.py's atomic-
write-on-every-change design.

Laid out like macOS System Settings: section titles over rounded cards
(widgets.SettingsCard), switches instead of checkboxes.

Notes on two fields:

  * hotkey     -- a fixed list of two-modifier presets
    (settings.VALID_HOTKEYS), not free key capture; README_AR.md's
    documented default is Ctrl+Cmd, and "fn" is not reliably hookable
    cross-app on macOS, so it is intentionally not offered.
  * microphone -- real device enumeration needs `sounddevice`, which this
    phase deliberately does NOT add as a dependency. If `sounddevice`
    happens to already be importable in the running environment, its
    input-device list is shown; otherwise a single "الافتراضي" (system
    default) entry is offered.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QComboBox,
    QFrame,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, history, login_item, openrouter
from ..settings import (
    VALID_HOTKEYS,
    VALID_PILL_MODES,
    VALID_RETENTION_DAYS,
    VALID_UI_FONTS,
    SettingsManager,
)
from . import strings, theme, widgets

logger = logging.getLogger("saghi.ui.settings_page")

_HOTKEY_PRESETS = list(VALID_HOTKEYS)
_FIELD_WIDTH = 220


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
    """
    Grouped like macOS System Settings: a section title over a rounded card
    of rows (name + one-line hint on one side, the control on the other).
    """

    # Asks the app to open the update dialog (ui/update_dialog.py) -- the
    # app owns that flow because an update may need to quit/relaunch.
    check_updates_requested = Signal()
    # Emitted after "clear all history" so the History page can refresh.
    history_cleared = Signal()

    def __init__(self, settings_manager: SettingsManager, parent=None):
        super().__init__(parent)
        self._sm = settings_manager
        s = settings_manager.current

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        body = QWidget()
        body.setObjectName("scrollBody")
        scroll.setWidget(body)
        column = QVBoxLayout(body)
        column.setContentsMargins(28, 4, 28, 28)
        column.setSpacing(22)

        # ---- الإملاء --------------------------------------------------------
        dictation = widgets.SettingsCard()

        self.hotkey_combo = QComboBox()
        self.hotkey_combo.setMinimumWidth(_FIELD_WIDTH)
        for value in _HOTKEY_PRESETS:
            self.hotkey_combo.addItem(strings.HOTKEY_LABELS[value], value)
        self._set_combo_by_data(self.hotkey_combo, s.hotkey, fallback_index=0)
        self.hotkey_combo.currentIndexChanged.connect(self._on_hotkey_changed)
        dictation.add_row(strings.SETTINGS_HOTKEY_LABEL, self.hotkey_combo, strings.SETTINGS_HOTKEY_HINT)

        self.language_combo = QComboBox()
        self.language_combo.setMinimumWidth(_FIELD_WIDTH)
        for lang in ("ar", "en"):
            self.language_combo.addItem(strings.LANGUAGE_LABELS[lang], lang)
        self._set_combo_by_data(self.language_combo, s.language, fallback_index=0)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        dictation.add_row(strings.SETTINGS_LANGUAGE_LABEL, self.language_combo, strings.SETTINGS_LANGUAGE_HINT)

        self.cleanup_combo = QComboBox()
        self.cleanup_combo.setMinimumWidth(_FIELD_WIDTH)
        for level in ("none", "light", "medium"):
            self.cleanup_combo.addItem(strings.CLEANUP_LEVEL_LABELS[level], level)
        self._set_combo_by_data(self.cleanup_combo, s.cleanup_level, fallback_index=1)
        self.cleanup_combo.currentIndexChanged.connect(self._on_cleanup_changed)
        dictation.add_row(strings.SETTINGS_CLEANUP_LABEL, self.cleanup_combo, strings.SETTINGS_CLEANUP_HINT)

        self.autopaste_check = widgets.ToggleSwitch()
        self.autopaste_check.setChecked(s.autopaste)
        self.autopaste_check.toggled.connect(lambda v: self._sm.update(autopaste=v))
        dictation.add_row(strings.SETTINGS_AUTOPASTE_LABEL, self.autopaste_check, strings.SETTINGS_AUTOPASTE_HINT)

        self.sound_check = widgets.ToggleSwitch()
        self.sound_check.setChecked(s.sound_feedback)
        self.sound_check.toggled.connect(lambda v: self._sm.update(sound_feedback=v))
        dictation.add_row(strings.SETTINGS_SOUND_LABEL, self.sound_check, strings.SETTINGS_SOUND_HINT)

        column.addWidget(widgets.section(strings.SETTINGS_SECTION_DICTATION, dictation))

        # ---- الميكروفون ------------------------------------------------------
        mic = widgets.SettingsCard()
        self._mic_options = _list_microphones()
        self.mic_combo = QComboBox()
        self.mic_combo.setMinimumWidth(_FIELD_WIDTH)
        self.mic_combo.setMaximumWidth(_FIELD_WIDTH + 80)
        for value, label in self._mic_options:
            self.mic_combo.addItem(label, value)
        mic_idx = next((i for i, (v, _l) in enumerate(self._mic_options) if v == s.microphone), 0)
        self.mic_combo.setCurrentIndex(mic_idx)
        self.mic_combo.currentIndexChanged.connect(self._on_mic_changed)
        mic.add_row(strings.SETTINGS_MIC_LABEL, self.mic_combo, strings.SETTINGS_MIC_HINT)

        self.save_rec_check = widgets.ToggleSwitch()
        self.save_rec_check.setChecked(s.save_recordings)
        self.save_rec_check.toggled.connect(lambda v: self._sm.update(save_recordings=v))
        mic.add_row(strings.SETTINGS_SAVE_RECORDINGS_LABEL, self.save_rec_check, strings.SETTINGS_SAVE_RECORDINGS_HINT)
        column.addWidget(widgets.section(strings.SETTINGS_SECTION_MIC, mic))

        # ---- مؤشر التسجيل ---------------------------------------------------
        indicator = widgets.SettingsCard()
        self.waveform_style_combo = QComboBox()
        self.waveform_style_combo.setMinimumWidth(_FIELD_WIDTH)
        for style in ("bars", "line", "dots", "pulse"):
            self.waveform_style_combo.addItem(strings.WAVEFORM_STYLE_LABELS[style], style)
        self._set_combo_by_data(self.waveform_style_combo, s.waveform_style, fallback_index=0)
        self.waveform_style_combo.currentIndexChanged.connect(self._on_waveform_style_changed)
        indicator.add_row(
            strings.SETTINGS_WAVEFORM_STYLE_LABEL, self.waveform_style_combo, strings.SETTINGS_WAVEFORM_STYLE_HINT
        )

        self._color_value = s.waveform_color
        self._color_group = QButtonGroup(self)
        self._color_group.setExclusive(True)
        self.color_dots: List[widgets.ColorDot] = []
        for preset in theme.WAVEFORM_PRESETS:
            dot = widgets.ColorDot(preset)
            dot.clicked.connect(lambda _c=False, c=preset: self._set_waveform_color(c))
            self._color_group.addButton(dot)
            self.color_dots.append(dot)
        # One extra dot that shows a custom colour when one is picked.
        self.custom_color_dot = widgets.ColorDot(self._color_value)
        self.custom_color_dot.clicked.connect(lambda: self._set_waveform_color(self.custom_color_dot.color))
        self._color_group.addButton(self.custom_color_dot)
        self.color_btn = QPushButton(strings.SETTINGS_WAVEFORM_CUSTOM_COLOR)
        self.color_btn.clicked.connect(self._on_pick_color)
        indicator.add_row(
            strings.SETTINGS_WAVEFORM_COLOR_LABEL,
            widgets.hbox(*self.color_dots, self.custom_color_dot, self.color_btn, stretch_end=False, spacing=6),
        )
        self._sync_color_dots()

        # The floating pill: when it shows, and a way to put it back where
        # it started. Both values can also change from outside this page
        # (dragging the pill, the menu-bar menu), so _on_settings_changed
        # keeps these two controls in step.
        self.floating_pill_combo = QComboBox()
        self.floating_pill_combo.setMinimumWidth(_FIELD_WIDTH - 60)
        for mode in VALID_PILL_MODES:
            self.floating_pill_combo.addItem(strings.PILL_MODE_LABELS[mode], mode)
        self._set_combo_by_data(self.floating_pill_combo, s.floating_pill_mode, fallback_index=0)
        self.floating_pill_combo.currentIndexChanged.connect(self._on_pill_mode_changed)
        self.pill_reset_btn = QPushButton(strings.SETTINGS_PILL_RESET)
        self.pill_reset_btn.setEnabled(s.floating_pill_pos is not None)
        self.pill_reset_btn.clicked.connect(self._on_pill_reset_clicked)
        indicator.add_row(
            strings.SETTINGS_PILL_LABEL,
            widgets.hbox(self.floating_pill_combo, self.pill_reset_btn, stretch_end=False),
            strings.SETTINGS_PILL_HINT,
        )
        settings_manager.on_change(self._on_settings_changed)
        column.addWidget(widgets.section(strings.SETTINGS_SECTION_INDICATOR, indicator))

        # ---- عام ----------------------------------------------------------------
        general = widgets.SettingsCard()
        self.launch_check = widgets.ToggleSwitch()
        self.launch_check.setChecked(s.launch_at_login)
        self.launch_check.toggled.connect(self._on_launch_at_login_changed)
        general.add_row(strings.SETTINGS_LAUNCH_AT_LOGIN_LABEL, self.launch_check, strings.SETTINGS_LAUNCH_AT_LOGIN_HINT)

        self.glass_check = widgets.ToggleSwitch()
        self.glass_check.setChecked(s.glass_effect)
        self.glass_check.toggled.connect(lambda v: self._sm.update(glass_effect=v))
        general.add_row(strings.SETTINGS_GLASS_LABEL, self.glass_check, strings.SETTINGS_GLASS_HINT)

        # app.py applies ui_font live through its own settings listener; this
        # control only writes the setting.
        self.ui_font_combo = QComboBox()
        self.ui_font_combo.setMinimumWidth(_FIELD_WIDTH - 60)
        for font in VALID_UI_FONTS:
            self.ui_font_combo.addItem(strings.UI_FONT_LABELS[font], font)
        self._set_combo_by_data(self.ui_font_combo, s.ui_font, fallback_index=0)
        self.ui_font_combo.currentIndexChanged.connect(self._on_ui_font_changed)
        general.add_row(strings.SETTINGS_UI_FONT_LABEL, self.ui_font_combo, strings.SETTINGS_UI_FONT_HINT)
        column.addWidget(widgets.section(strings.SETTINGS_SECTION_GENERAL, general))

        # ---- السجل --------------------------------------------------------------
        hist = widgets.SettingsCard()
        self.history_retention_combo = QComboBox()
        self.history_retention_combo.setMinimumWidth(_FIELD_WIDTH)
        for days in VALID_RETENTION_DAYS:
            self.history_retention_combo.addItem(strings.HISTORY_RETENTION_LABELS[days], days)
        self._set_combo_by_data(self.history_retention_combo, s.history_retention_days, fallback_index=0)
        self.history_retention_combo.currentIndexChanged.connect(self._on_retention_changed)
        hist.add_row(
            strings.SETTINGS_HISTORY_RETENTION_LABEL,
            self.history_retention_combo,
            strings.SETTINGS_HISTORY_RETENTION_HINT,
        )
        self.history_clear_btn = QPushButton(strings.SETTINGS_HISTORY_CLEAR_BUTTON)
        self.history_clear_btn.clicked.connect(self._on_clear_history_clicked)
        clear_row = hist.add_row(strings.SETTINGS_HISTORY_CLEAR_LABEL, self.history_clear_btn, " ")
        self.history_count_label = clear_row.findChild(QLabel, "rowHint")
        self.refresh_history_count()
        column.addWidget(widgets.section(strings.SETTINGS_SECTION_HISTORY, hist))

        # ---- OpenRouter rephrase (Phase 6) ---------------------------------
        ai = widgets.SettingsCard()
        self.openrouter_enabled_check = widgets.ToggleSwitch()
        self.openrouter_enabled_check.setChecked(s.openrouter_enabled)
        self.openrouter_enabled_check.toggled.connect(self._on_openrouter_enabled_changed)
        ai.add_row(
            strings.SETTINGS_OPENROUTER_ENABLED_LABEL,
            self.openrouter_enabled_check,
            strings.SETTINGS_OPENROUTER_ENABLED_HINT,
        )

        # Model id and the API key are both Latin-script identifiers, not
        # Arabic prose -- left-to-right layout direction reads naturally for
        # typing/pasting them, unlike the free-form (often Arabic)
        # instructions box below, which stays at the page's RTL default.
        self.openrouter_model_edit = QLineEdit(s.openrouter_model)
        self.openrouter_model_edit.setMinimumWidth(_FIELD_WIDTH + 40)
        self.openrouter_model_edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.openrouter_model_edit.setPlaceholderText(strings.SETTINGS_OPENROUTER_MODEL_PLACEHOLDER)
        self.openrouter_model_edit.editingFinished.connect(self._on_openrouter_model_changed)
        self._ai_rows = [ai.add_row(strings.SETTINGS_OPENROUTER_MODEL_LABEL, self.openrouter_model_edit)]

        self.openrouter_key_edit = QLineEdit()
        self.openrouter_key_edit.setMinimumWidth(_FIELD_WIDTH)
        self.openrouter_key_edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.openrouter_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._refresh_key_placeholder()
        self.openrouter_key_save_btn = QPushButton(strings.SETTINGS_OPENROUTER_SAVE_BUTTON)
        self.openrouter_key_save_btn.clicked.connect(self._on_save_key)
        self.openrouter_key_delete_btn = QPushButton(strings.SETTINGS_OPENROUTER_DELETE_BUTTON)
        self.openrouter_key_delete_btn.clicked.connect(self._on_delete_key)
        self._ai_rows.append(
            ai.add_row(
                strings.SETTINGS_OPENROUTER_KEY_LABEL,
                widgets.hbox(
                    self.openrouter_key_edit,
                    self.openrouter_key_save_btn,
                    self.openrouter_key_delete_btn,
                    stretch_end=False,
                ),
            )
        )

        instructions_box = QWidget()
        iv = QVBoxLayout(instructions_box)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.setSpacing(6)
        iv.addWidget(widgets.label(strings.SETTINGS_OPENROUTER_INSTRUCTIONS_LABEL, "rowTitle"))
        self.openrouter_instructions_edit = _FocusOutPlainTextEdit()
        self.openrouter_instructions_edit.setPlainText(s.openrouter_instructions)
        self.openrouter_instructions_edit.setFixedHeight(84)
        self.openrouter_instructions_edit.focus_lost.connect(self._on_openrouter_instructions_changed)
        iv.addWidget(self.openrouter_instructions_edit)
        self._ai_rows.append(ai.add_widget(instructions_box))

        self.openrouter_test_btn = QPushButton(strings.SETTINGS_OPENROUTER_TEST_BUTTON)
        self.openrouter_test_btn.clicked.connect(self._on_test_connection)
        self.openrouter_test_result_label = QLabel("")
        self.openrouter_test_result_label.setWordWrap(True)
        self._ai_rows.append(
            ai.add_widget(widgets.hbox(self.openrouter_test_btn, self.openrouter_test_result_label, spacing=12))
        )
        self._on_openrouter_enabled_changed(s.openrouter_enabled, save=False)
        column.addWidget(widgets.section(strings.SETTINGS_SECTION_AI, ai))

        self._openrouter_test_worker: Optional[_TestConnectionWorker] = None

        # ---- التحديثات --------------------------------------------------------
        updates = widgets.SettingsCard()
        self.check_updates_btn = QPushButton(strings.SETTINGS_CHECK_UPDATES_BUTTON)
        self.check_updates_btn.clicked.connect(self.check_updates_requested.emit)
        updates.add_row(
            strings.SETTINGS_VERSION_LABEL,
            self.check_updates_btn,
            strings.version_label(__version__),
        )
        self.auto_update_check = widgets.ToggleSwitch()
        self.auto_update_check.setChecked(s.auto_check_updates)
        self.auto_update_check.toggled.connect(lambda v: self._sm.update(auto_check_updates=v))
        updates.add_row(strings.SETTINGS_AUTO_UPDATE_LABEL, self.auto_update_check, strings.SETTINGS_AUTO_UPDATE_HINT)
        column.addWidget(widgets.section(strings.SETTINGS_SECTION_UPDATES, updates))

        column.addStretch()

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def _set_combo_by_data(combo: QComboBox, value, fallback_index: int) -> None:
        idx = combo.findData(value)
        combo.setCurrentIndex(idx if idx >= 0 else fallback_index)

    def _sync_color_dots(self) -> None:
        current = (self._color_value or "").lower()
        for dot in self.color_dots:
            if dot.color.lower() == current:
                dot.setChecked(True)
                self.custom_color_dot.setVisible(False)
                return
        self.custom_color_dot.set_color(self._color_value)
        self.custom_color_dot.setVisible(True)
        self.custom_color_dot.setChecked(True)

    def _set_waveform_color(self, hex_color: str) -> None:
        self._color_value = hex_color
        self._sync_color_dots()
        self._sm.update(waveform_color=hex_color)

    def refresh_history_count(self) -> None:
        try:
            n = history.count()
        except Exception:  # noqa: BLE001 -- a count is decoration; never break the page over it
            return
        if self.history_count_label is not None:
            self.history_count_label.setText(strings.history_count_text(n))
        self.history_clear_btn.setEnabled(n > 0)

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

    def _on_pill_mode_changed(self, index: int) -> None:
        self._sm.update(floating_pill_mode=self.floating_pill_combo.itemData(index))

    def _on_pill_reset_clicked(self) -> None:
        self._sm.update(floating_pill_pos=None)

    def _on_ui_font_changed(self, index: int) -> None:
        self._sm.update(ui_font=self.ui_font_combo.itemData(index))

    def _on_settings_changed(self, s) -> None:
        """
        Settings listener: the pill's mode and position also change outside
        this page (dragging the pill, the menu-bar menu). Only reflects the
        new values in the controls -- signals are blocked while syncing, so
        nothing is written back and there is no update loop.
        """
        try:
            self.floating_pill_combo.blockSignals(True)
            try:
                self._set_combo_by_data(
                    self.floating_pill_combo, s.floating_pill_mode, fallback_index=self.floating_pill_combo.currentIndex()
                )
            finally:
                self.floating_pill_combo.blockSignals(False)
            self.pill_reset_btn.setEnabled(s.floating_pill_pos is not None)
        except RuntimeError:
            # The page (and its Qt widgets) was deleted while the listener
            # stayed registered on the settings manager -- nothing to sync.
            pass

    def _on_pick_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._color_value), self, strings.SETTINGS_WAVEFORM_COLOR_LABEL)
        if color.isValid():
            self._set_waveform_color(color.name())

    def _on_launch_at_login_changed(self, enabled: bool) -> None:
        self._sm.update(launch_at_login=enabled)
        if login_item.is_supported():
            login_item.set_enabled(enabled)

    def _on_retention_changed(self, index: int) -> None:
        days = self.history_retention_combo.itemData(index)
        # A shorter period deletes history right away -- ask first, and put
        # the previous choice back if the user says no.
        try:
            doomed = history.count_older_than(days)
        except Exception:  # noqa: BLE001
            doomed = 0
        if doomed and not widgets.confirm_destructive(
            self,
            strings.SETTINGS_HISTORY_PRUNE_CONFIRM_TITLE,
            strings.history_prune_confirm_text(doomed),
        ):
            self.history_retention_combo.blockSignals(True)
            self._set_combo_by_data(
                self.history_retention_combo, self._sm.current.history_retention_days, fallback_index=0
            )
            self.history_retention_combo.blockSignals(False)
            return
        self._sm.update(history_retention_days=days)
        try:
            removed = history.prune_older_than(days)
        except Exception:  # noqa: BLE001
            logger.exception("Could not prune history")
            return
        if removed:
            self.history_cleared.emit()
        self.refresh_history_count()

    def _on_clear_history_clicked(self) -> None:
        if not widgets.confirm_destructive(
            self,
            strings.SETTINGS_HISTORY_CLEAR_CONFIRM_TITLE,
            strings.SETTINGS_HISTORY_CLEAR_CONFIRM_TEXT,
        ):
            return
        self.clear_history()

    def clear_history(self) -> None:
        """Delete every history entry (no confirmation -- the button's handler asks first)."""
        history.clear_all()
        self.refresh_history_count()
        self.history_cleared.emit()

    def _on_openrouter_enabled_changed(self, enabled: bool, save: bool = True) -> None:
        if save:
            self._sm.update(openrouter_enabled=enabled)
        for row in self._ai_rows:
            row.setEnabled(enabled)

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
        self.openrouter_test_result_label.setObjectName("secondaryText")
        self.openrouter_test_result_label.setText(strings.SETTINGS_OPENROUTER_TEST_RUNNING)
        worker = _TestConnectionWorker(model, parent=self)
        worker.finished_result.connect(self._on_test_connection_result)
        worker.finished.connect(self._on_test_worker_thread_finished)
        self._openrouter_test_worker = worker
        worker.start()

    def _on_test_connection_result(self, result) -> None:
        self.openrouter_test_btn.setEnabled(True)
        if result.ok:
            self.openrouter_test_result_label.setObjectName("successText")
            self.openrouter_test_result_label.setText(strings.SETTINGS_OPENROUTER_TEST_SUCCESS)
        else:
            self.openrouter_test_result_label.setObjectName("errorText")
            self.openrouter_test_result_label.setText(strings.openrouter_test_failure(result.error or ""))
        # objectName-based colours need a re-polish to take effect.
        self.openrouter_test_result_label.style().unpolish(self.openrouter_test_result_label)
        self.openrouter_test_result_label.style().polish(self.openrouter_test_result_label)

    def _on_test_worker_thread_finished(self) -> None:
        # QThread housekeeping only, same pattern as dictation.py's
        # _on_worker_thread_finished / filejob_page.py's worker teardown --
        # UI state is already fully handled by _on_test_connection_result.
        self._openrouter_test_worker = None

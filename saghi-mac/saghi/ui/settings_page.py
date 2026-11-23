"""
Configuration page: live-editing controls for the user-facing settings
(the floating-pill / waveform / glass options are gone: the notch replaces
the pill; their fields stay in settings.py untouched). Every control writes through SettingsManager.update() immediately
on change -- no separate "Save" button, matching settings.py's atomic-
write-on-every-change design.

Laid out like macOS System Settings: section titles over rounded cards
(widgets.SettingsCard), switches instead of checkboxes.

Notes on two fields:

  * hotkey     -- a shortcut recorder (`ShortcutRecorder`): any 1-2 keys
    (hotkey_spec.py); the default stays Ctrl+Cmd, and "fn" is not reliably
    hookable cross-app on macOS, so it cannot be recorded.
  * microphone -- the input devices macOS has right now, re-read every
    time the page is shown and every time the list is opened (via
    recorder.list_input_devices(refresh=True)), so a mic plugged in after
    launch shows up. A saved mic that is unplugged stays listed, marked as
    not connected, instead of the list silently showing the default. If
    `sounddevice` is missing, a single "الافتراضي" (system default) entry
    is offered.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from PySide6.QtCore import QEvent, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, history, login_item, openrouter, recorder
from ..hotkey_labels import hotkey_label
from ..hotkey_spec import (
    DEFAULT_HOTKEY,
    ESC_VK,
    MODIFIER_VK_TO_TOKEN,
    family_of,
    is_bare_typing_key,
    normalize_hotkey,
    vk_token,
)
from ..settings import (
    VALID_RETENTION_DAYS,
    SettingsManager,
)
from . import strings, widgets

logger = logging.getLogger("saghi.ui.settings_page")

_FIELD_WIDTH = 160

# Modifier order inside a recorded two-modifier shortcut (matches the presets:
# "ctrl+cmd", "alt+cmd", "shift+cmd", "ctrl+alt", "ctrl+shift").
_MODIFIER_ORDER = {"ctrl": 0, "alt": 1, "shift": 2, "cmd": 3}
# Caps lock and fn report oddly and cannot be used; their events are ignored.
_IGNORED_VKS = frozenset({57, 63})


class ShortcutRecorder(QPushButton):
    """
    A button showing the current shortcut; clicking it records the next one.

    Rules (see hotkey_spec.py for the resulting string):
      * a non-modifier key finishes the recording at once: held modifiers
        (at most 1) + that key, e.g. Command held + Space -> "cmd+vk:49";
      * only modifiers: the recording finishes when every key is released.
        One modifier alone becomes the side-specific token ("alt_r" for the
        right Option key); two different ones become the families
        ("ctrl+alt");
      * more than 2 keys at once: a message, and recording continues;
      * Esc cancels and keeps the old shortcut; losing focus does too.

    Keys come from Qt key events: nativeVirtualKey() is the macOS virtual
    key code, which tells the modifier and its side apart.
    """

    hotkey_changed = Signal(str)
    recording_changed = Signal(bool)
    message_changed = Signal(str)

    def __init__(self, hotkey: str = DEFAULT_HOTKEY, parent=None):
        super().__init__(parent)
        self._hotkey = normalize_hotkey(hotkey)
        self._recording = False
        self._down: dict = {}  # vk -> token of the keys currently held
        self._mods_seen: dict = {}  # vk -> token of the modifiers pressed in this attempt
        self._invalid = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self.start_recording)
        self._refresh_text()

    # ---- state ------------------------------------------------------------

    @property
    def hotkey(self) -> str:
        return self._hotkey

    @property
    def is_recording(self) -> bool:
        return self._recording

    def set_hotkey(self, hotkey: str) -> None:
        self._hotkey = normalize_hotkey(hotkey)
        self._refresh_text()

    def _refresh_text(self) -> None:
        if self._recording:
            self.setText(strings.SETTINGS_HOTKEY_PROMPT)
        else:
            self.setText(strings.isolate_ltr(hotkey_label(self._hotkey)))

    def start_recording(self) -> None:
        if self._recording:
            return
        self._recording = True
        self._reset_attempt()
        self._refresh_text()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.message_changed.emit("")
        self.recording_changed.emit(True)

    def cancel_recording(self) -> None:
        if self._recording:
            self._end_recording()

    def _end_recording(self) -> None:
        self._recording = False
        self._reset_attempt()
        self._refresh_text()
        self.recording_changed.emit(False)

    def _reset_attempt(self) -> None:
        self._down = {}
        self._mods_seen = {}
        self._invalid = False

    def _finish(self, hotkey: str) -> None:
        self._hotkey = hotkey
        self._end_recording()
        self.message_changed.emit("")
        self.hotkey_changed.emit(hotkey)

    # ---- Qt events ----------------------------------------------------------

    def event(self, e) -> bool:  # noqa: N802 -- Qt override
        # While recording, keep ordinary shortcuts (Cmd+W, Cmd+Q ...) from
        # firing: they are the user's input here.
        if self._recording and e.type() == QEvent.Type.ShortcutOverride:
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802 -- Qt override
        if not self._recording:
            super().keyPressEvent(e)
            return
        e.accept()
        if e.isAutoRepeat():
            return
        vk = int(e.nativeVirtualKey())
        if vk == ESC_VK or e.key() == Qt.Key.Key_Escape:
            self._end_recording()
            self.message_changed.emit("")
            return
        if vk in _IGNORED_VKS:
            return
        mod = MODIFIER_VK_TO_TOKEN.get(vk)
        if mod is not None:
            self._down[vk] = mod
            self._mods_seen[vk] = mod
            if len(self._down) > 2:
                self._too_many()
            return
        # An ordinary key.
        if self._invalid or len(self._down) > 1:
            self._down[vk] = vk_token(vk)
            self._too_many()
            return
        mods = [t for t in self._down.values() if family_of(t)]
        combo = [family_of(m) for m in mods] + [vk_token(vk)]
        self._finish("+".join(combo))

    def keyReleaseEvent(self, e) -> None:  # noqa: N802 -- Qt override
        if not self._recording:
            super().keyReleaseEvent(e)
            return
        e.accept()
        if e.isAutoRepeat():
            return
        self._down.pop(int(e.nativeVirtualKey()), None)
        if self._down:
            return
        # Everything is up.
        if self._invalid:
            self._reset_attempt()  # try again; the message stays
            return
        tokens = list(self._mods_seen.values())
        if len(tokens) == 1:
            self._finish(tokens[0])
        elif len(tokens) == 2:
            fams = sorted({family_of(t) for t in tokens}, key=_MODIFIER_ORDER.get)
            if len(fams) == 2:
                self._finish("+".join(fams))
            else:
                self._reset_attempt()  # both sides of one modifier: not a shortcut

    def _too_many(self) -> None:
        self._invalid = True
        self.message_changed.emit(strings.SETTINGS_HOTKEY_MAX_TWO)

    def focusOutEvent(self, e) -> None:  # noqa: N802 -- Qt override
        super().focusOutEvent(e)
        self.cancel_recording()

    def hideEvent(self, e) -> None:  # noqa: N802 -- Qt override
        super().hideEvent(e)
        self.cancel_recording()


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


def _list_microphones(saved: Optional[str] = None) -> List[Tuple[Optional[str], str]]:
    """
    [(device_name_or_None, display_label), ...]; first entry is always the
    built-in-mic placeholder (iPhone/Continuity mics are never listed). Re-reads the device list from macOS (see
    recorder.refresh_devices). `saved`, if given and not connected, is
    appended with a "not connected" label so the list never pretends the
    default is selected. list_input_devices() never raises, so a missing
    audio backend just leaves the default-only list.
    """
    options: List[Tuple[Optional[str], str]] = [(None, strings.MIC_DEFAULT)]
    seen = set()
    for name, _idx in recorder.list_input_devices(refresh=True):
        if name not in seen and not recorder.is_continuity_name(name):
            seen.add(name)
            options.append((name, name))
    if saved is not None and saved not in seen and not recorder.is_continuity_name(saved):
        options.append((saved, strings.mic_disconnected_label(saved)))
    return options


class _RefreshingComboBox(QComboBox):
    """A QComboBox that calls `before_popup` right before its list opens."""

    def __init__(self, before_popup, parent=None):
        super().__init__(parent)
        self._before_popup = before_popup

    def showPopup(self) -> None:  # noqa: N802 -- Qt override
        self._before_popup()
        super().showPopup()


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
    # Open the file-transcription sheet / quit Saghi: the window owns both.
    filejob_requested = Signal()
    quit_requested = Signal()
    # True while the shortcut recorder is listening, False when it is done: the
    # app pauses the global hotkey in between (DictationController).
    hotkey_capture_changed = Signal(bool)

    def __init__(self, settings_manager: SettingsManager, parent=None):
        super().__init__(parent)
        self._sm = settings_manager
        s = settings_manager.current

        # Three sub-pages (Dictation / Text / App), picked from the sidebar's
        # Settings sub-menu (MainWindow), each short enough to need little scrolling.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.sections = widgets.FadeStack()
        outer.addWidget(self.sections)
        columns = []
        for _ in range(3):
            scroll = QScrollArea()
            scroll.setObjectName("pageScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            body = QWidget()
            body.setObjectName("scrollBody")
            scroll.setWidget(body)
            col = QVBoxLayout(body)
            col.setContentsMargins(24, 0, 24, 20)
            col.setSpacing(16)
            columns.append(col)
            self.sections.addWidget(scroll)
        col_dictation, col_text, col_app = columns

        # ---- الإملاء --------------------------------------------------------
        dictation = widgets.SettingsCard()

        self.hotkey_recorder = ShortcutRecorder(s.hotkey)
        self.hotkey_recorder.setMinimumWidth(_FIELD_WIDTH)
        self.hotkey_recorder.hotkey_changed.connect(self._on_hotkey_changed)
        self.hotkey_recorder.recording_changed.connect(self.hotkey_capture_changed.emit)
        self.hotkey_reset_btn = QPushButton(strings.SETTINGS_HOTKEY_RESET)
        self.hotkey_reset_btn.setFlat(True)
        self.hotkey_reset_btn.setObjectName("linkButton")
        self.hotkey_reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hotkey_reset_btn.clicked.connect(self._on_hotkey_reset)
        self.hotkey_note = widgets.label("", "rowHint", wrap=True)
        self.hotkey_recorder.message_changed.connect(self._set_hotkey_message)
        hotkey_box = QWidget()
        hotkey_col = QVBoxLayout(hotkey_box)
        hotkey_col.setContentsMargins(0, 0, 0, 0)
        hotkey_col.setSpacing(4)
        hotkey_col.addWidget(self.hotkey_recorder)
        hotkey_col.addWidget(self.hotkey_reset_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        hotkey_col.addWidget(self.hotkey_note)
        self._refresh_hotkey_widgets()
        dictation.add_row(strings.SETTINGS_HOTKEY_LABEL, hotkey_box, strings.SETTINGS_HOTKEY_HINT)

        # Bound like every other bool; getattr keeps the page working with a
        # Settings object that predates the field.
        self.tap_toggle_check = widgets.ToggleSwitch()
        self.tap_toggle_check.setChecked(bool(getattr(s, "tap_to_toggle", True)))
        self.tap_toggle_check.toggled.connect(lambda v: self._sm.update(tap_to_toggle=v))
        dictation.add_row(strings.SETTINGS_TAP_TOGGLE_LABEL, self.tap_toggle_check, strings.SETTINGS_TAP_TOGGLE_HINT)

        self._mic_options: List[Tuple[Optional[str], str]] = []
        self.mic_combo = _RefreshingComboBox(self.refresh_microphones)
        self.mic_combo.setMinimumWidth(_FIELD_WIDTH)
        self.mic_combo.setMinimumWidth(_FIELD_WIDTH + 60)
        self.refresh_microphones()
        self.mic_combo.currentIndexChanged.connect(self._on_mic_changed)
        dictation.add_row(strings.SETTINGS_MIC_LABEL, self.mic_combo, strings.SETTINGS_MIC_HINT)

        self.language_combo = QComboBox()
        self.language_combo.setMinimumWidth(_FIELD_WIDTH)
        for lang in ("ar", "en"):
            self.language_combo.addItem(strings.LANGUAGE_LABELS[lang], lang)
        self._set_combo_by_data(self.language_combo, s.language, fallback_index=0)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        dictation.add_row(strings.SETTINGS_LANGUAGE_LABEL, self.language_combo, strings.SETTINGS_LANGUAGE_HINT)

        self.sound_check = widgets.ToggleSwitch()
        self.sound_check.setChecked(s.sound_feedback)
        self.sound_check.toggled.connect(lambda v: self._sm.update(sound_feedback=v))
        dictation.add_row(strings.SETTINGS_SOUND_LABEL, self.sound_check, strings.SETTINGS_SOUND_HINT)

        self.autopaste_check = widgets.ToggleSwitch()
        self.autopaste_check.setChecked(s.autopaste)
        self.autopaste_check.toggled.connect(lambda v: self._sm.update(autopaste=v))
        dictation.add_row(strings.SETTINGS_AUTOPASTE_LABEL, self.autopaste_check, strings.SETTINGS_AUTOPASTE_HINT)

        self.save_rec_check = widgets.ToggleSwitch()
        self.save_rec_check.setChecked(s.save_recordings)
        self.save_rec_check.toggled.connect(lambda v: self._sm.update(save_recordings=v))
        dictation.add_row(strings.SETTINGS_SAVE_RECORDINGS_LABEL, self.save_rec_check, strings.SETTINGS_SAVE_RECORDINGS_HINT)

        col_dictation.addWidget(dictation)

        # ---- النص ------------------------------------------------------------
        ai = widgets.SettingsCard()
        self.cleanup_combo = QComboBox()
        self.cleanup_combo.setMinimumWidth(_FIELD_WIDTH)
        for level in ("none", "light", "medium"):
            self.cleanup_combo.addItem(strings.CLEANUP_LEVEL_LABELS[level], level)
        self._set_combo_by_data(self.cleanup_combo, s.cleanup_level, fallback_index=1)
        self.cleanup_combo.currentIndexChanged.connect(self._on_cleanup_changed)
        ai.add_row(strings.SETTINGS_CLEANUP_LABEL, self.cleanup_combo, strings.SETTINGS_CLEANUP_HINT)

        # OpenRouter rephrase (Phase 6), in the same card.
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
        self.openrouter_model_edit.setFixedWidth(_FIELD_WIDTH)
        self.openrouter_model_edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.openrouter_model_edit.setPlaceholderText(strings.SETTINGS_OPENROUTER_MODEL_PLACEHOLDER)
        self.openrouter_model_edit.editingFinished.connect(self._on_openrouter_model_changed)
        self._ai_rows = [ai.add_row(strings.SETTINGS_OPENROUTER_MODEL_LABEL, self.openrouter_model_edit)]

        self.openrouter_key_edit = QLineEdit()
        self.openrouter_key_edit.setMinimumWidth(60)
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
        col_text.addWidget(ai)

        # ---- التطبيق ---------------------------------------------------------
        app_card = widgets.SettingsCard()
        self.launch_check = widgets.ToggleSwitch()
        self.launch_check.setChecked(s.launch_at_login)
        self.launch_check.toggled.connect(self._on_launch_at_login_changed)
        app_card.add_row(strings.SETTINGS_LAUNCH_AT_LOGIN_LABEL, self.launch_check, strings.SETTINGS_LAUNCH_AT_LOGIN_HINT)


        self.history_retention_combo = QComboBox()
        self.history_retention_combo.setMinimumWidth(_FIELD_WIDTH - 60)
        for days in VALID_RETENTION_DAYS:
            self.history_retention_combo.addItem(strings.HISTORY_RETENTION_LABELS[days], days)
        self._set_combo_by_data(self.history_retention_combo, s.history_retention_days, fallback_index=0)
        self.history_retention_combo.currentIndexChanged.connect(self._on_retention_changed)
        app_card.add_row(
            strings.SETTINGS_HISTORY_RETENTION_LABEL,
            self.history_retention_combo,
            strings.SETTINGS_HISTORY_RETENTION_HINT,
        )
        self.history_clear_btn = QPushButton(strings.SETTINGS_HISTORY_CLEAR_BUTTON)
        self.history_clear_btn.clicked.connect(self._on_clear_history_clicked)
        clear_row = app_card.add_row(strings.SETTINGS_HISTORY_CLEAR_LABEL, self.history_clear_btn, " ")
        self.history_count_label = clear_row.findChild(QLabel, "rowHint")
        self.refresh_history_count()

        self.auto_update_check = widgets.ToggleSwitch()
        self.auto_update_check.setChecked(s.auto_check_updates)
        self.auto_update_check.toggled.connect(lambda v: self._sm.update(auto_check_updates=v))
        app_card.add_row(strings.SETTINGS_AUTO_UPDATE_LABEL, self.auto_update_check, strings.SETTINGS_AUTO_UPDATE_HINT)

        self.check_updates_btn = QPushButton(strings.SETTINGS_CHECK_UPDATES_BUTTON)
        self.check_updates_btn.clicked.connect(self.check_updates_requested.emit)
        app_card.add_row(strings.SETTINGS_VERSION_LABEL, self.check_updates_btn, strings.version_label(__version__))

        self.filejob_btn = QPushButton(strings.SETTINGS_FILEJOB_BUTTON)
        self.filejob_btn.clicked.connect(self.filejob_requested.emit)
        app_card.add_row(strings.SETTINGS_FILEJOB_LABEL, self.filejob_btn, strings.PAGE_FILEJOB_SUBTITLE)

        self.quit_btn = QPushButton(strings.SETTINGS_QUIT_BUTTON)
        self.quit_btn.clicked.connect(self.quit_requested.emit)
        app_card.add_row(strings.SETTINGS_QUIT_LABEL, self.quit_btn, strings.SETTINGS_QUIT_HINT)
        col_app.addWidget(app_card)

        self._openrouter_test_worker: Optional[_TestConnectionWorker] = None

        # Long item texts (hotkey, microphone names) must not widen the page:
        # a fixed field width, the full text stays readable in the open list.
        for combo in self.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)
            combo.setMinimumWidth(0)
            combo.setFixedWidth(_FIELD_WIDTH)
        self.hotkey_recorder.setFixedWidth(_FIELD_WIDTH - 20)
        self.mic_combo.setFixedWidth(_FIELD_WIDTH + 70)

        for col in columns:
            col.addStretch()

    # ---- sub-pages --------------------------------------------------------

    SECTION_KEYS = ("dictation", "text", "app")

    def show_section(self, key: str) -> None:
        """Show one sub-page: "dictation" | "text" | "app"."""
        self.sections.setCurrentIndex(self.SECTION_KEYS.index(key))

    def current_section(self) -> str:
        return self.SECTION_KEYS[self.sections.currentIndex()]

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def _set_combo_by_data(combo: QComboBox, value, fallback_index: int) -> None:
        idx = combo.findData(value)
        combo.setCurrentIndex(idx if idx >= 0 else fallback_index)

    def refresh_microphones(self) -> None:
        """Re-read the connected mics and keep the saved one selected (no settings write)."""
        saved = self._sm.current.microphone
        options = _list_microphones(saved)
        if options == self._mic_options:
            return
        self._mic_options = options
        self.mic_combo.blockSignals(True)
        try:
            self.mic_combo.clear()
            for value, label in options:
                self.mic_combo.addItem(label, value)
            self.mic_combo.setCurrentIndex(next((i for i, (v, _l) in enumerate(options) if v == saved), 0))
        finally:
            self.mic_combo.blockSignals(False)

    def showEvent(self, event) -> None:  # noqa: N802 -- Qt override
        super().showEvent(event)
        self.refresh_microphones()

    def refresh_history_count(self) -> None:
        try:
            n = history.count()
        except Exception:  # noqa: BLE001 -- a count is decoration; never break the page over it
            return
        if self.history_count_label is not None:
            self.history_count_label.setText(strings.history_count_text(n))
        self.history_clear_btn.setEnabled(n > 0)

    # ---- change handlers ----------------------------------------------------

    def _on_hotkey_changed(self, hotkey: str) -> None:
        self._sm.update(hotkey=hotkey)
        self._refresh_hotkey_widgets()

    def _on_hotkey_reset(self) -> None:
        self.hotkey_recorder.cancel_recording()
        self.hotkey_recorder.set_hotkey(DEFAULT_HOTKEY)
        self._sm.update(hotkey=DEFAULT_HOTKEY)
        self._refresh_hotkey_widgets()

    def _set_hotkey_message(self, text: str) -> None:
        """A message from the recorder, else the "this key will not type" warning."""
        if not text and is_bare_typing_key(self._sm.current.hotkey):
            text = strings.SETTINGS_HOTKEY_BARE_WARNING
        self.hotkey_note.setText(text)
        self.hotkey_note.setVisible(bool(text))

    def _refresh_hotkey_widgets(self) -> None:
        current = self._sm.current.hotkey
        self.hotkey_reset_btn.setVisible(current != DEFAULT_HOTKEY)
        self._set_hotkey_message("")

    def _on_mic_changed(self, index: int) -> None:
        self._sm.update(microphone=self.mic_combo.itemData(index))

    def _on_cleanup_changed(self, index: int) -> None:
        self._sm.update(cleanup_level=self.cleanup_combo.itemData(index))

    def _on_language_changed(self, index: int) -> None:
        self._sm.update(language=self.language_combo.itemData(index))

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

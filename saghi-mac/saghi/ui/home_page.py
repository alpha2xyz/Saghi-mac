"""
Home page: three usage stats, how to dictate, and which model to use.

Stats come from history.stats() (words and recording time) and are shown as
compact Arabic durations. Typing-time saved assumes 40 words per minute.

The model choice writes settings.engine ("gguf") and settings.gguf_variant
("fast" = Q6_K, "full" = Q8_0); the engine is built once at
start-up, so a change only applies after a restart, and the page then shows
an inline note with a "restart now" button (signal `restart_requested`,
handled by ui/app.py, which quits and relaunches Saghi).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEasingCurve, QTimer, Qt, QVariantAnimation, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import history
from . import strings, widgets

logger = logging.getLogger("saghi.ui.home_page")

TYPING_WORDS_PER_MINUTE = 40
_REFRESH_MS = 5000  # live refresh while visible (history has no change signal)


def compute_stats(entries_stats: dict) -> dict:
    """{"words", "spoken_s", "saved_s"}: saved = typing time at 40 wpm minus speaking time, floored at 0."""
    words = int(entries_stats.get("words", 0))
    spoken = float(entries_stats.get("spoken_s", 0.0))
    typing_s = words / TYPING_WORDS_PER_MINUTE * 60
    return {"words": words, "spoken_s": spoken, "saved_s": max(0.0, typing_s - spoken)}


def full_available() -> bool:
    """True when the bundled Q8_0 ("Full") file is present."""
    try:
        from .. import gguf_engine

        return gguf_engine.variant_available("full")
    except Exception:  # noqa: BLE001 -- availability is a hint; never break the page
        logger.debug("Full model availability check failed", exc_info=True)
        return False


def model_choice(s) -> str:
    from ..engines import model_choice as _mc

    return _mc(s)


def gguf_available(gguf_model: str = "") -> bool:
    """True when both the GGUF runner and a .gguf model file can be found."""
    try:
        from .. import gguf_engine

        cli = gguf_engine.find_cli()
        model = gguf_engine.find_model(gguf_model or None)
        return bool(cli and model and Path(cli).exists() and Path(model).exists())
    except Exception:  # noqa: BLE001 -- availability is a hint; never break the page
        logger.debug("GGUF availability check failed", exc_info=True)
        return False


class _Stat(QFrame):
    """A stat card whose number counts up to its value (ease-out, ~1.8 s)."""

    COUNT_MS = 1800  # slow enough to watch the numbers climb

    def __init__(self, caption: str, fmt=lambda v: str(int(round(v)))) -> None:
        super().__init__()
        self.setObjectName("card")
        self._fmt = fmt
        self._shown = 0.0
        self._target = 0.0
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(4)
        self.value = widgets.label(fmt(0), "statValue")
        self.value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap = widgets.label(caption, "rowHint")
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.value)
        v.addWidget(cap)
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutQuart)
        self._anim.valueChanged.connect(self._on_frame)

    def _on_frame(self, v) -> None:
        self._shown = float(v)
        self.value.setText(self._fmt(self._shown))

    def set_value(self, target: float, from_zero: bool = False) -> None:
        """Count from 0 (page just opened) or from what is shown now (a live refresh)."""
        target = float(target)
        start = 0.0 if from_zero else self._shown
        self._target = target
        self._anim.stop()
        if start == target:
            self._on_frame(target)
            return
        self._anim.setStartValue(start)
        self._anim.setEndValue(target)
        self._anim.setDuration(self.COUNT_MS if from_zero else 500)
        self._anim.start()

    @property
    def target(self) -> float:
        return self._target


def _key_cap(text: str) -> QLabel:
    cap = widgets.label(strings.isolate_ltr(text), "keyCap")
    cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
    cap.setMinimumWidth(52)
    return cap


class HomePage(QWidget):
    restart_requested = Signal()

    def __init__(self, settings_manager, parent=None) -> None:
        super().__init__(parent)
        self._sm = settings_manager
        # The model the running process was built with.
        self._full_ok = full_available()
        self._running_engine = model_choice(settings_manager.current)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        self._scroll = scroll
        body = QWidget()
        body.setObjectName("scrollBody")
        scroll.setWidget(body)
        column = QVBoxLayout(body)
        column.setContentsMargins(24, 0, 24, 16)
        column.setSpacing(10)

        # ---- stats ----------------------------------------------------------
        row = QHBoxLayout()
        row.setSpacing(12)
        self.saved_stat = _Stat(strings.HOME_STAT_SAVED, strings.format_duration)
        self.words_stat = _Stat(strings.HOME_STAT_WORDS)
        self.spoken_stat = _Stat(strings.HOME_STAT_SPOKEN, strings.format_duration)
        for stat in (self.saved_stat, self.words_stat, self.spoken_stat):
            row.addWidget(stat, 1)
        column.addLayout(row)

        # ---- how to use -----------------------------------------------------
        self.howto_card = widgets.SettingsCard()
        self.hotkey_cap = _key_cap("")
        self.hotkey_text = widgets.label("", "rowTitle", wrap=True)
        self.howto_card.add_widget(self._how_row(self.hotkey_cap, self.hotkey_text))
        self.howto_card.add_widget(
            self._how_row(_key_cap("esc"), widgets.label(strings.HOME_HOWTO_ESC, "rowTitle", wrap=True))
        )
        column.addWidget(widgets.section(strings.HOME_HOWTO_TITLE, self.howto_card))

        # ---- model ----------------------------------------------------------
        self.model_card = widgets.SettingsCard()
        self._group = QButtonGroup(self)
        self.fast_radio = QRadioButton()
        self.full_radio = QRadioButton()
        self._group.addButton(self.fast_radio)
        self._group.addButton(self.full_radio)
        fast_row = self.model_card.add_row(strings.HOME_MODEL_FAST, self.fast_radio, strings.HOME_MODEL_FAST_HINT)
        self._fast_hint_label = fast_row.findChild(QLabel, "rowHint")
        self._full_row = self.model_card.add_row(strings.HOME_MODEL_FULL, self.full_radio, strings.HOME_MODEL_FULL_HINT)
        # "Full" (Q8_0) shows up when that file is bundled.
        self._full_row.setVisible(self._full_ok)

        self.restart_note = widgets.label(strings.HOME_MODEL_RESTART_NOTE, "secondaryText")
        self.restart_btn = QPushButton(strings.HOME_MODEL_RESTART_BUTTON)
        self.restart_btn.clicked.connect(self.restart_requested.emit)
        self.restart_row = widgets.hbox(self.restart_note, self.restart_btn)
        self.model_card.add_widget(self.restart_row)
        column.addWidget(widgets.section(strings.HOME_MODEL_TITLE, self.model_card))
        column.addStretch()

        # The "fast" model needs the GGUF runner + file; hint when missing.
        self._gguf_ok = gguf_available(settings_manager.current.gguf_model)
        self._sync_engine_controls(settings_manager.current)
        self.fast_radio.toggled.connect(self._on_choice)
        settings_manager.on_change(self._on_settings_changed)

        self._timer = QTimer(self)
        self._timer.setInterval(_REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self._apply_hotkey(settings_manager.current)
        self.refresh()

    @staticmethod
    def _how_row(cap: QWidget, text: QWidget) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(14)
        h.addWidget(cap, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(text, 1)
        return w

    # ---- stats ----------------------------------------------------------------

    def refresh(self, count_up: bool = False) -> None:
        try:
            stats = compute_stats(history.stats())
        except Exception:  # noqa: BLE001 -- decoration; keep the page alive
            logger.exception("Could not read history stats")
            return
        self.saved_stat.set_value(stats["saved_s"], from_zero=count_up)
        self.words_stat.set_value(stats["words"], from_zero=count_up)
        self.spoken_stat.set_value(stats["spoken_s"], from_zero=count_up)

    def showEvent(self, event) -> None:  # noqa: N802 -- Qt override
        super().showEvent(event)
        self._scroll.verticalScrollBar().setValue(0)  # always open at the top (the stats)
        self.refresh(count_up=True)
        self._timer.start()

    def finish_animations(self) -> None:
        """Jump every counter to its final value (tests, screenshots)."""
        for stat in (self.saved_stat, self.words_stat, self.spoken_stat):
            stat._anim.stop()
            stat._on_frame(stat.target)

    def hideEvent(self, event) -> None:  # noqa: N802 -- Qt override
        super().hideEvent(event)
        self._timer.stop()

    # ---- hotkey line -------------------------------------------------------------

    def _apply_hotkey(self, s) -> None:
        self.hotkey_cap.setText(strings.isolate_ltr(strings.hotkey_symbols(s.hotkey)))
        tap = bool(getattr(s, "tap_to_toggle", True))
        self.hotkey_text.setText(strings.HOME_HOWTO_HOLD_AND_TAP if tap else strings.HOME_HOWTO_HOLD_ONLY)

    # ---- model choice --------------------------------------------------------------

    def _sync_engine_controls(self, s) -> None:
        want_fast = model_choice(s) == "fast" or not self._full_ok
        # Block both radios for the whole update: the exclusive group toggles
        # the other one, and that must not write settings back.
        self.fast_radio.blockSignals(True)
        self.full_radio.blockSignals(True)
        self.fast_radio.setChecked(want_fast)
        self.full_radio.setChecked(not want_fast)
        self.fast_radio.blockSignals(False)
        self.full_radio.blockSignals(False)
        # Keep it usable when it is already the saved choice.
        self.fast_radio.setEnabled(self._gguf_ok or want_fast)
        if not self._gguf_ok:
            self.fast_radio.setToolTip(strings.HOME_MODEL_FAST_MISSING)
            self._fast_hint_label.setText(f"{strings.HOME_MODEL_FAST_HINT} ({strings.HOME_MODEL_FAST_MISSING})")
        self.restart_row.setVisible(model_choice(s) != self._running_engine)

    def _on_choice(self, _checked: bool) -> None:
        # An old explicit gguf_model path (trial installs) would override the choice: clear it.
        self._sm.update(engine="gguf", gguf_model="", gguf_variant="fast" if self.fast_radio.isChecked() else "full")

    def _on_settings_changed(self, s) -> None:
        try:
            self._sync_engine_controls(s)
            self._apply_hotkey(s)
        except RuntimeError:
            pass  # the page was deleted while the listener stayed registered

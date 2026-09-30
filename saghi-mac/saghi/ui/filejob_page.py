"""
File transcription page: pick or drop a long audio file, choose
language/cleanup/timestamps, run it through saghi.filejobs.FileJob on a
background QThread (NEVER on the UI thread -- FileJob.run() blocks for
real inference, up to hours for a long file), and show real progress
(current chunk / percent / elapsed / ETA) driven by FileJob's own
`on_progress(done_chunks, total_chunks, percent, elapsed_s, eta_s)`
callback.

Resume detection: FileJob.run() calls on_progress() once immediately after
loading/creating its manifest, reflecting any chunks already marked "done"
from a previous run (elapsed_s=0.0 for that first call -- see
filejobs.py's run() docstring). This page treats that very first progress
signal of a run as the resume signal: if it reports done > 0, the
"سيتم الاستكمال من آخر جزء محفوظ" notice is shown immediately, before any
new chunk in *this* run has actually been processed.

Cancel: FileJob's constructor (Phase 4 addition, see filejobs.py) accepts
a `cancel_event` (a plain threading.Event) checked once at the top of each
pending chunk's loop iteration -- cooperative, stops after the chunk
currently in flight finishes, never mid-chunk. The Cancel button here just
sets that event; the worker thread's normal finished_ok signal still fires
once FileJob.run() returns (with `result.cancelled == True`).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..filejobs import FileJob, FileJobResult
from . import strings, theme, widgets

logger = logging.getLogger("saghi.ui.filejob_page")

_AUDIO_FILTER = "Audio (*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.mp4)"


class _DropCard(QFrame):
    """
    The big drop zone at the top of the page: accepts a dropped audio/video
    file (drag-and-drop), and holds the "choose file" button. Once a file is
    picked it shows the file's name and size instead of the hint.
    """

    file_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumHeight(190)
        self.setObjectName("dropCard")
        self.setProperty("dragActive", False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(6)
        layout.addStretch()
        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._icon)
        self.title_label = QLabel(strings.FILEJOB_DROP_TITLE)
        self.title_label.setObjectName("dropTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)
        self._hint_label = QLabel(strings.FILEJOB_DROP_HINT)
        self._hint_label.setObjectName("secondaryText")
        self._hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint_label.setWordWrap(True)
        layout.addWidget(self._hint_label)
        self.button_row = QHBoxLayout()
        self.button_row.addStretch()
        layout.addSpacing(6)
        layout.addLayout(self.button_row)
        self._formats = QLabel(strings.FILEJOB_FORMATS)
        self._formats.setObjectName("secondaryText")
        self._formats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._formats)
        layout.addStretch()
        self.refresh_icon()

    def refresh_icon(self) -> None:
        self._icon.setPixmap(widgets.icon_pixmap("drop", 40, QColor(theme.tokens().accent)))

    def show_file(self, title: str, info: str) -> None:
        self.title_label.setText(title)
        self._hint_label.setText(info)
        self._formats.setVisible(False)

    def _set_drag_active(self, active: bool) -> None:
        self.setProperty("dragActive", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 -- Qt override naming
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_drag_active(True)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 -- Qt override naming
        self._set_drag_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 -- Qt override naming
        self._set_drag_active(False)
        urls = event.mimeData().urls()
        if urls:
            self.file_dropped.emit(urls[0].toLocalFile())


class FileJobWorker(QThread):
    """
    Runs one FileJob.run() call on a background thread. Signals are Qt's
    standard cross-thread-safe mechanism (queued automatically to whatever
    thread the receiver lives on -- the UI thread here), so the page never
    touches FileJob/engine state directly from a slot running on this
    thread.
    """

    progress = Signal(int, int, float, float, object)  # done, total, percent, elapsed_s, eta_s (float|None)
    finished_ok = Signal(object)  # FileJobResult
    failed = Signal(str)

    def __init__(
        self,
        engine,
        source_path,
        language: str,
        cleanup_level: str,
        timestamps: bool,
        cancel_event: threading.Event,
        chunk_s: Optional[float] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._engine = engine
        self._source_path = source_path
        self._language = language
        self._cleanup_level = cleanup_level
        self._timestamps = timestamps
        self._cancel_event = cancel_event
        # Not exposed in the page's UI (per the Phase 4 task's documented
        # controls: language/cleanup/timestamps only) -- FileJob's own
        # memory-aware default (see filejobs.default_chunk_s()) is what
        # the GUI uses in practice. Exists as a constructor parameter so
        # dev/test_gui_filejob.py can force a small chunk_s and exercise
        # the multi-chunk progress-reporting path on a short test file,
        # without monkeypatching anything.
        self._chunk_s = chunk_s

    def run(self) -> None:  # noqa: N802 -- QThread override
        try:
            job = FileJob(
                self._engine,
                self._source_path,
                language=self._language,
                cleanup_level=self._cleanup_level,
                timestamps=self._timestamps,
                cancel_event=self._cancel_event,
                chunk_s=self._chunk_s,
            )

            def on_progress(done, total, percent, elapsed_s, eta_s):
                self.progress.emit(done, total, percent, elapsed_s, eta_s)

            result = job.run(on_progress=on_progress)
            self.finished_ok.emit(result)
        except Exception as exc:  # noqa: BLE001 -- surface any failure to the UI instead of crashing silently
            logger.exception("File job failed")
            self.failed.emit(str(exc))


class FileJobPage(QWidget):
    def __init__(self, engine, engine_status=None, parent=None, settings_manager=None):
        super().__init__(parent)
        self._engine = engine
        self._engine_status = engine_status
        self._picked_path: Optional[Path] = None
        self._worker: Optional[FileJobWorker] = None
        self._cancel_event: Optional[threading.Event] = None
        self._first_progress_seen = False
        self._last_job_dir: Optional[Path] = None

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

        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 0, 28, 24)
        layout.setSpacing(16)

        # ---- drop zone + pick button ------------------------------------------
        self.drop_card = _DropCard()
        self.drop_card.file_dropped.connect(self._set_picked_path)
        self.pick_btn = QPushButton(strings.FILEJOB_PICK_BUTTON)
        self.pick_btn.clicked.connect(self._on_pick_clicked)
        self.drop_card.button_row.addWidget(self.pick_btn)
        self.drop_card.button_row.addStretch()
        layout.addWidget(self.drop_card)
        # Kept for tests/screenshots: the plain file name that was picked.
        self.picked_label = QLabel(strings.FILEJOB_NO_FILE_PICKED)
        self.picked_label.setVisible(False)

        # ---- options ------------------------------------------------------------
        defaults = settings_manager.current if settings_manager is not None else None
        options = widgets.SettingsCard()
        self.language_combo = QComboBox()
        self.language_combo.setMinimumWidth(180)
        for lang in ("ar", "en"):
            self.language_combo.addItem(strings.LANGUAGE_LABELS[lang], lang)
        if defaults is not None:
            idx = self.language_combo.findData(defaults.language)
            self.language_combo.setCurrentIndex(max(idx, 0))
        options.add_row(strings.FILEJOB_LANGUAGE_LABEL, self.language_combo)

        self.cleanup_combo = QComboBox()
        self.cleanup_combo.setMinimumWidth(180)
        for level in ("none", "light", "medium"):
            self.cleanup_combo.addItem(strings.CLEANUP_LEVEL_LABELS[level], level)
        self.cleanup_combo.setCurrentIndex(1)  # light, matches the app-wide default
        if defaults is not None:
            idx = self.cleanup_combo.findData(defaults.cleanup_level)
            if idx >= 0:
                self.cleanup_combo.setCurrentIndex(idx)
        options.add_row(strings.FILEJOB_CLEANUP_LABEL, self.cleanup_combo, strings.SETTINGS_CLEANUP_HINT)

        self.timestamps_check = widgets.ToggleSwitch()
        options.add_row(strings.FILEJOB_TIMESTAMPS_LABEL, self.timestamps_check, strings.FILEJOB_TIMESTAMPS_HINT)
        layout.addWidget(widgets.section(strings.FILEJOB_OPTIONS_TITLE, options))

        # ---- actions --------------------------------------------------------------
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.start_btn = QPushButton(strings.FILEJOB_START_BUTTON)
        self.start_btn.setDefault(True)  # macOS draws the default button in the accent colour
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._on_start_clicked)
        action_row.addWidget(self.start_btn)

        self.cancel_btn = QPushButton(strings.FILEJOB_CANCEL_BUTTON)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        action_row.addWidget(self.cancel_btn)
        action_row.addStretch()
        layout.addLayout(action_row)

        # ---- progress (shown once a run starts) -----------------------------------
        progress = widgets.SettingsCard()
        progress_box = QWidget()
        pv = QVBoxLayout(progress_box)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(8)
        self.resume_label = QLabel(strings.FILEJOB_RESUME_NOTICE)
        self.resume_label.setObjectName("secondaryText")
        self.resume_label.setVisible(False)
        pv.addWidget(self.resume_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(False)
        pv.addWidget(self.progress_bar)

        status_row = QHBoxLayout()
        status_row.setSpacing(18)
        self.chunk_label = QLabel("")
        self.chunk_label.setObjectName("rowTitle")
        status_row.addWidget(self.chunk_label)
        status_row.addStretch()
        self.elapsed_label = QLabel("")
        self.elapsed_label.setObjectName("secondaryText")
        status_row.addWidget(self.elapsed_label)
        self.eta_label = QLabel("")
        self.eta_label.setObjectName("secondaryText")
        status_row.addWidget(self.eta_label)
        pv.addLayout(status_row)
        progress.add_widget(progress_box, margins=(0, 14, 0, 14))
        self.progress_section = widgets.section(strings.FILEJOB_PROGRESS_TITLE, progress)
        self.progress_section.setVisible(False)
        layout.addWidget(self.progress_section)

        # ---- result -----------------------------------------------------------------
        result = widgets.SettingsCard()
        result_box = QWidget()
        rv = QVBoxLayout(result_box)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(10)
        self.result_view = QPlainTextEdit()
        self.result_view.setObjectName("cardText")
        self.result_view.setReadOnly(True)
        self.result_view.setFrameShape(QFrame.Shape.NoFrame)
        self.result_view.setMinimumHeight(220)
        rv.addWidget(self.result_view)

        result_btn_row = QHBoxLayout()
        self.copy_result_btn = QPushButton(strings.FILEJOB_COPY_TEXT)
        self.copy_result_btn.setEnabled(False)
        self.copy_result_btn.clicked.connect(self._copy_result_text)
        result_btn_row.addWidget(self.copy_result_btn)

        self.open_folder_btn = QPushButton(strings.FILEJOB_OPEN_FOLDER)
        self.open_folder_btn.setEnabled(False)
        self.open_folder_btn.clicked.connect(self._open_output_folder)
        result_btn_row.addWidget(self.open_folder_btn)
        result_btn_row.addStretch()
        rv.addLayout(result_btn_row)
        result.add_widget(result_box, margins=(0, 14, 0, 14))
        self.result_section = widgets.section(strings.FILEJOB_RESULT_TITLE, result)
        self.result_section.setVisible(False)
        layout.addWidget(self.result_section)
        layout.addStretch()

    # ---- file selection -------------------------------------------------

    def _on_pick_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, strings.FILEJOB_PICK_BUTTON, "", _AUDIO_FILTER)
        if path:
            self._set_picked_path(path)

    def _set_picked_path(self, path: str) -> None:
        self._picked_path = Path(path)
        self.picked_label.setText(self._picked_path.name)
        try:
            size = self._picked_path.stat().st_size
        except OSError:
            size = 0
        self._show_picked(self._picked_path.name, size)
        self.start_btn.setEnabled(self._worker is None)

    def _show_picked(self, name: str, size_bytes: int) -> None:
        self.drop_card.show_file(strings.isolate_ltr(name), strings.filejob_file_size(size_bytes))
        self.pick_btn.setText(strings.FILEJOB_CHANGE_FILE)

    # ---- run / cancel -----------------------------------------------------

    def _on_start_clicked(self) -> None:
        if self._picked_path is None or self._worker is not None:
            return

        self._cancel_event = threading.Event()
        self._first_progress_seen = False
        self.resume_label.setVisible(False)
        self.result_view.setPlainText("")
        self.open_folder_btn.setEnabled(False)
        self.copy_result_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_section.setVisible(True)
        self.result_section.setVisible(False)
        self.chunk_label.setText("")
        self.elapsed_label.setText("")
        self.eta_label.setText("")
        self._set_running_ui(True)

        if self._engine_status is not None:
            self._engine_status.mark_loading()

        language = self.language_combo.currentData()
        cleanup_level = self.cleanup_combo.currentData()
        timestamps = self.timestamps_check.isChecked()

        self._worker = FileJobWorker(
            self._engine,
            self._picked_path,
            language,
            cleanup_level,
            timestamps,
            self._cancel_event,
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_worker_thread_finished)
        self._worker.start()

    def _on_cancel_clicked(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
        self.cancel_btn.setEnabled(False)

    def _on_worker_thread_finished(self) -> None:
        # QThread housekeeping only -- UI state is already fully handled by
        # _on_finished/_on_failed, which always fire before this.
        self._worker = None

    # ---- progress / completion --------------------------------------------

    def _on_progress(self, done: int, total: int, percent: float, elapsed_s: float, eta_s) -> None:
        self.progress_section.setVisible(True)
        if not self._first_progress_seen:
            self._first_progress_seen = True
            if done > 0:
                self.resume_label.setVisible(True)
        self.apply_progress(done, total, percent, elapsed_s, eta_s)

    def apply_progress(self, done: int, total: int, percent: float, elapsed_s: float, eta_s) -> None:
        """Public so tests/dev/grab_screens.py can preview a mid-run state without a real worker."""
        self.progress_section.setVisible(True)
        self.progress_bar.setValue(int(round(percent)))
        self.chunk_label.setText(strings.filejob_chunk_progress(done, total))
        self.elapsed_label.setText(strings.filejob_elapsed_text(elapsed_s))
        self.eta_label.setText(strings.filejob_eta_text(eta_s))

    def _settle_engine_status(self) -> None:
        # _on_start_clicked announced a model load; if the run ended (failed,
        # or cancelled early) without the model loading, clear "loading" so
        # the status pill and menu-bar icon don't keep saying so.
        engine_loaded = bool(getattr(self._engine, "is_loaded", False))
        if self._engine_status is not None and self._engine is not None and not engine_loaded:
            self._engine_status.mark_failed()

    def _on_finished(self, result: FileJobResult) -> None:
        self._settle_engine_status()
        self._set_running_ui(False)
        self.result_section.setVisible(True)
        self._last_job_dir = result.job_dir
        self.open_folder_btn.setEnabled(True)
        self.copy_result_btn.setEnabled(True)

        if result.cancelled:
            self.resume_label.setVisible(False)
            self.result_view.setPlainText(strings.FILEJOB_CANCELLED_NOTICE + "\n\n" + result.text)
        else:
            self.result_view.setPlainText(result.text)

    def _on_failed(self, message: str) -> None:
        self._settle_engine_status()
        self._set_running_ui(False)
        self.result_section.setVisible(True)
        self.result_view.setPlainText(strings.FILEJOB_ERROR_PREFIX + message)

    # ---- result actions -----------------------------------------------------

    def _open_output_folder(self) -> None:
        if self._last_job_dir is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_job_dir)))

    def _copy_result_text(self) -> None:
        QApplication.clipboard().setText(self.result_view.toPlainText())

    # ---- UI state helpers (also used by tests / dev/grab_screens.py) -------

    def _set_running_ui(self, running: bool) -> None:
        self.start_btn.setEnabled(not running and self._picked_path is not None)
        self.cancel_btn.setEnabled(running)
        self.pick_btn.setEnabled(not running)
        self.language_combo.setEnabled(not running)
        self.cleanup_combo.setEnabled(not running)
        self.timestamps_check.setEnabled(not running)

    def set_running(self, running: bool) -> None:
        """Public wrapper around _set_running_ui, for tests/screenshots previewing the running state."""
        self._set_running_ui(running)

    def preview_picked(self, filename: str, size_bytes: int = 0) -> None:
        """Test/screenshot helper: show `filename` as picked without a real file or worker."""
        self.picked_label.setText(filename)
        self._show_picked(filename, size_bytes)

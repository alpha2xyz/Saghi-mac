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
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..filejobs import FileJob, FileJobResult
from . import strings

logger = logging.getLogger("saghi.ui.filejob_page")

_AUDIO_FILTER = "Audio (*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.mp4)"


class _DropCard(QFrame):
    """A card that accepts a dropped audio file (drag-and-drop)."""

    file_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(80)
        self.setObjectName("dropCard")

        layout = QVBoxLayout(self)
        self._hint_label = QLabel(strings.FILEJOB_DROP_HINT)
        self._hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint_label.setWordWrap(True)
        layout.addWidget(self._hint_label)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 -- Qt override naming
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 -- Qt override naming
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
    def __init__(self, engine, engine_status=None, parent=None):
        super().__init__(parent)
        self._engine = engine
        self._engine_status = engine_status
        self._picked_path: Optional[Path] = None
        self._worker: Optional[FileJobWorker] = None
        self._cancel_event: Optional[threading.Event] = None
        self._first_progress_seen = False
        self._last_job_dir: Optional[Path] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        self.drop_card = _DropCard()
        self.drop_card.file_dropped.connect(self._set_picked_path)
        layout.addWidget(self.drop_card)

        pick_row = QHBoxLayout()
        self.pick_btn = QPushButton(strings.FILEJOB_PICK_BUTTON)
        self.pick_btn.clicked.connect(self._on_pick_clicked)
        pick_row.addWidget(self.pick_btn)
        self.picked_label = QLabel(strings.FILEJOB_NO_FILE_PICKED)
        pick_row.addWidget(self.picked_label)
        pick_row.addStretch()
        layout.addLayout(pick_row)

        opts_row = QHBoxLayout()
        opts_row.addWidget(QLabel(strings.FILEJOB_LANGUAGE_LABEL))
        self.language_combo = QComboBox()
        self.language_combo.setMaximumWidth(140)
        for lang in ("ar", "en"):
            self.language_combo.addItem(strings.LANGUAGE_LABELS[lang], lang)
        opts_row.addWidget(self.language_combo)

        opts_row.addWidget(QLabel(strings.FILEJOB_CLEANUP_LABEL))
        self.cleanup_combo = QComboBox()
        self.cleanup_combo.setMaximumWidth(140)
        for level in ("none", "light", "medium"):
            self.cleanup_combo.addItem(strings.CLEANUP_LEVEL_LABELS[level], level)
        self.cleanup_combo.setCurrentIndex(1)  # light, matches the app-wide default
        opts_row.addWidget(self.cleanup_combo)

        self.timestamps_check = QCheckBox(strings.FILEJOB_TIMESTAMPS_LABEL)
        opts_row.addWidget(self.timestamps_check)
        opts_row.addStretch()
        layout.addLayout(opts_row)

        action_row = QHBoxLayout()
        self.start_btn = QPushButton(strings.FILEJOB_START_BUTTON)
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._on_start_clicked)
        action_row.addWidget(self.start_btn)

        self.cancel_btn = QPushButton(strings.FILEJOB_CANCEL_BUTTON)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        action_row.addWidget(self.cancel_btn)
        action_row.addStretch()
        layout.addLayout(action_row)

        self.resume_label = QLabel(strings.FILEJOB_RESUME_NOTICE)
        self.resume_label.setVisible(False)
        layout.addWidget(self.resume_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)

        status_row = QHBoxLayout()
        self.chunk_label = QLabel("")
        status_row.addWidget(self.chunk_label)
        self.elapsed_label = QLabel("")
        status_row.addWidget(self.elapsed_label)
        self.eta_label = QLabel("")
        status_row.addWidget(self.eta_label)
        status_row.addStretch()
        layout.addLayout(status_row)

        self.result_view = QPlainTextEdit()
        self.result_view.setReadOnly(True)
        layout.addWidget(self.result_view)

        result_btn_row = QHBoxLayout()
        self.open_folder_btn = QPushButton(strings.FILEJOB_OPEN_FOLDER)
        self.open_folder_btn.setEnabled(False)
        self.open_folder_btn.clicked.connect(self._open_output_folder)
        result_btn_row.addWidget(self.open_folder_btn)

        self.copy_result_btn = QPushButton(strings.FILEJOB_COPY_TEXT)
        self.copy_result_btn.setEnabled(False)
        self.copy_result_btn.clicked.connect(self._copy_result_text)
        result_btn_row.addWidget(self.copy_result_btn)
        result_btn_row.addStretch()
        layout.addLayout(result_btn_row)

    # ---- file selection -------------------------------------------------

    def _on_pick_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, strings.FILEJOB_PICK_BUTTON, "", _AUDIO_FILTER)
        if path:
            self._set_picked_path(path)

    def _set_picked_path(self, path: str) -> None:
        self._picked_path = Path(path)
        self.picked_label.setText(self._picked_path.name)
        self.start_btn.setEnabled(True)

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
        if not self._first_progress_seen:
            self._first_progress_seen = True
            if done > 0:
                self.resume_label.setVisible(True)
        self.apply_progress(done, total, percent, elapsed_s, eta_s)

    def apply_progress(self, done: int, total: int, percent: float, elapsed_s: float, eta_s) -> None:
        """Public so tests/dev/grab_screens.py can preview a mid-run state without a real worker."""
        self.progress_bar.setValue(int(round(percent)))
        self.chunk_label.setText(strings.filejob_chunk_progress(done, total))
        self.elapsed_label.setText(strings.filejob_elapsed_text(elapsed_s))
        self.eta_label.setText(strings.filejob_eta_text(eta_s))

    def _on_finished(self, result: FileJobResult) -> None:
        self._set_running_ui(False)
        self._last_job_dir = result.job_dir
        self.open_folder_btn.setEnabled(True)
        self.copy_result_btn.setEnabled(True)

        if result.cancelled:
            self.resume_label.setVisible(False)
            self.result_view.setPlainText(strings.FILEJOB_CANCELLED_NOTICE + "\n\n" + result.text)
        else:
            self.result_view.setPlainText(result.text)

    def _on_failed(self, message: str) -> None:
        self._set_running_ui(False)
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

    def preview_picked(self, filename: str) -> None:
        """Test/screenshot helper: show `filename` as picked without a real file or worker."""
        self.picked_label.setText(filename)

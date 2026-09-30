"""
The "check for updates" window and the app-side update flow (the network
and file work itself lives in saghi/updater.py, off the UI thread).

  UpdateController   owned by ui/app.py. Opens the dialog (Settings button,
                     tray menu), runs the optional once-a-day background
                     check (settings.auto_check_updates) and shows a tray
                     notice when a newer release exists -- it never
                     installs anything on its own.
  UpdateDialog       checking -> up to date / new version (+ release notes)
                     -> downloading (progress) -> "restart" (light update)
                     or "quit and run the installer" (full update).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from .. import __version__, updater
from . import strings, theme, widgets

logger = logging.getLogger("saghi.ui.update_dialog")

_AUTO_CHECK_EVERY = timedelta(days=1)
_AUTO_CHECK_FIRST_DELAY_MS = 30_000  # let the app settle after start-up first
_AUTO_CHECK_POLL_MS = 60 * 60 * 1000


class _CheckWorker(QThread):
    done = Signal(object)  # updater.CheckResult
    failed = Signal(str)

    def run(self) -> None:  # noqa: N802 -- QThread override
        try:
            self.done.emit(updater.check())
        except Exception as exc:  # noqa: BLE001 -- surface anything to the UI
            self.failed.emit(str(exc))


class _PrepareWorker(QThread):
    progress = Signal(int, int)
    done = Signal(object)  # updater.PreparedUpdate
    failed = Signal(str)

    def __init__(self, release, parent=None) -> None:
        super().__init__(parent)
        self._release = release

    def run(self) -> None:  # noqa: N802 -- QThread override
        try:
            prepared = updater.prepare(self._release, on_progress=lambda d, t: self.progress.emit(d, t))
            if prepared.kind == "light":
                updater.apply_light(prepared)
            self.done.emit(prepared)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Update failed")
            self.failed.emit(str(exc))


class UpdateDialog(QDialog):
    def __init__(self, quit_app: Callable[[], None], parent=None) -> None:
        super().__init__(parent)
        self._quit_app = quit_app
        self._release: Optional[updater.ReleaseInfo] = None
        self._prepared: Optional[updater.PreparedUpdate] = None
        self._worker: Optional[QThread] = None

        self.setWindowTitle(strings.UPDATE_TITLE)
        self.setMinimumWidth(460)
        self.setStyleSheet(theme.stylesheet())

        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 20)
        v.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(14)
        self._icon = QLabel()
        from .tray import _ICON_PATH  # local import: tray.py imports nothing from here, but keep load order simple

        px = QPixmap(str(_ICON_PATH))
        if not px.isNull():
            px.setDevicePixelRatio(2.0)
            self._icon.setPixmap(px.scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        head.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        self.title_label = widgets.label(strings.UPDATE_CHECKING, "emptyTitle", wrap=True)
        texts.addWidget(self.title_label)
        self.detail_label = widgets.label(strings.update_current(__version__), "secondaryText", wrap=True)
        texts.addWidget(self.detail_label)
        head.addLayout(texts, 1)
        v.addLayout(head)

        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        self.notes.setMinimumHeight(140)
        self.notes.setVisible(False)
        v.addWidget(self.notes)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        v.addWidget(self.progress)

        buttons = QHBoxLayout()
        self.page_btn = QPushButton(strings.UPDATE_OPEN_PAGE)
        self.page_btn.clicked.connect(self._open_release_page)
        self.page_btn.setVisible(False)
        buttons.addWidget(self.page_btn)
        buttons.addStretch()
        self.close_btn = QPushButton(strings.UPDATE_CLOSE)
        self.close_btn.clicked.connect(self.reject)
        buttons.addWidget(self.close_btn)
        self.action_btn = QPushButton(strings.UPDATE_NOW)
        self.action_btn.setDefault(True)
        self.action_btn.setVisible(False)
        self.action_btn.clicked.connect(self._on_action)
        buttons.addWidget(self.action_btn)
        v.addLayout(buttons)

        self._action = ""

    # ---- flow -----------------------------------------------------------------

    def start_check(self) -> None:
        self._set_action("", None)
        self.title_label.setText(strings.UPDATE_CHECKING)
        self.detail_label.setText(strings.update_current(__version__))
        self.progress.setRange(0, 0)  # indeterminate
        self.progress.setVisible(True)
        worker = _CheckWorker(self)
        worker.done.connect(self._on_checked)
        worker.failed.connect(self._on_failed)
        self._run(worker)

    def show_result(self, result: "updater.CheckResult") -> None:
        """Show an already-finished check (from the background auto-check)."""
        self._on_checked(result)

    def _run(self, worker: QThread) -> None:
        self._worker = worker
        worker.finished.connect(lambda: setattr(self, "_worker", None))
        worker.start()

    def _on_checked(self, result) -> None:
        self.progress.setVisible(False)
        self._release = result.latest
        self.page_btn.setVisible(bool(result.latest and result.latest.html_url))
        if not result.update_available or result.latest is None:
            self.title_label.setText(strings.UPDATE_UP_TO_DATE)
            self.detail_label.setText(strings.update_current(result.current))
            self.notes.setVisible(False)
            return
        self.title_label.setText(strings.update_available(result.latest.version))
        self.detail_label.setText(strings.update_current(result.current))
        if result.latest.notes:
            self.notes.setMarkdown(f"**{strings.UPDATE_RELEASE_NOTES}**\n\n{result.latest.notes}")
            self.notes.setVisible(True)
        if updater.can_self_update():
            self._set_action("update", strings.UPDATE_NOW)
        else:
            self.detail_label.setText(strings.UPDATE_NOT_INSTALLED)

    def _on_failed(self, message: str) -> None:
        self.progress.setVisible(False)
        self.title_label.setText(strings.update_error(message))
        self._set_action("retry", strings.UPDATE_RETRY)

    def _on_action(self) -> None:
        if self._action == "retry":
            self.start_check()
        elif self._action == "update" and self._release is not None:
            self._download()
        elif self._action == "restart":
            updater.relaunch_after_exit()
            self._quit_app()
        elif self._action == "installer" and self._prepared is not None:
            try:
                updater.launch_full_installer_after_exit(self._prepared)
            except updater.UpdateError as exc:
                self._on_failed(str(exc))
                return
            self._quit_app()

    def _download(self) -> None:
        self._set_action("", None)
        self.close_btn.setEnabled(False)
        self.title_label.setText(strings.UPDATE_DOWNLOADING)
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        worker = _PrepareWorker(self._release, self)
        worker.progress.connect(self._on_progress)
        worker.done.connect(self._on_prepared)
        worker.failed.connect(self._on_prepare_failed)
        self._run(worker)

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(done * 1000 / total))

    def _on_prepare_failed(self, message: str) -> None:
        self.close_btn.setEnabled(True)
        self._on_failed(message)

    def _on_prepared(self, prepared) -> None:
        self.close_btn.setEnabled(True)
        self.progress.setVisible(False)
        self._prepared = prepared
        if prepared.kind == "light":
            self.title_label.setText(strings.UPDATE_DONE_LIGHT)
            self._set_action("restart", strings.UPDATE_RESTART)
        else:
            self.title_label.setText(strings.update_available(prepared.version))
            self.detail_label.setText(strings.UPDATE_NEEDS_FULL)
            self._set_action("installer", strings.UPDATE_RUN_INSTALLER)

    def _set_action(self, action: str, text: Optional[str]) -> None:
        self._action = action
        self.action_btn.setVisible(bool(text))
        if text:
            self.action_btn.setText(text)

    def _open_release_page(self) -> None:
        if self._release and self._release.html_url:
            QDesktopServices.openUrl(QUrl(self._release.html_url))

    def reject(self) -> None:
        if self._worker is not None and isinstance(self._worker, _PrepareWorker):
            return  # never abandon a half-installed update
        super().reject()


class UpdateController(QObject):
    """App-wide owner of the update flow (one dialog at a time)."""

    update_found = Signal(object)  # updater.CheckResult

    def __init__(self, settings_manager, quit_app: Callable[[], None], parent=None) -> None:
        super().__init__(parent)
        self._sm = settings_manager
        self._quit_app = quit_app
        self._dialog: Optional[UpdateDialog] = None
        self._auto_worker: Optional[_CheckWorker] = None
        self._last_result = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.maybe_auto_check)

    def start(self) -> None:
        """Begin the once-a-day background check (does nothing while it's switched off in Settings)."""
        QTimer.singleShot(_AUTO_CHECK_FIRST_DELAY_MS, self.maybe_auto_check)
        self._timer.start(_AUTO_CHECK_POLL_MS)

    def open_dialog(self) -> None:
        if self._dialog is None:
            self._dialog = UpdateDialog(self._quit_app)
            self._dialog.finished.connect(self._on_dialog_closed)
            if self._last_result is not None and self._last_result.update_available:
                self._dialog.show_result(self._last_result)
            else:
                self._dialog.start_check()
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()

    def _on_dialog_closed(self, _code: int) -> None:
        dialog, self._dialog = self._dialog, None
        worker = dialog._worker if dialog is not None else None
        if worker is not None and worker.isRunning():
            # A check is still in flight: its QThread is a child of the
            # dialog, so only delete the dialog once the thread is done.
            worker.finished.connect(dialog.deleteLater)
        elif dialog is not None:
            dialog.deleteLater()

    def auto_check_due(self, now: Optional[datetime] = None) -> bool:
        s = self._sm.current
        if not s.auto_check_updates:
            return False
        if not s.last_update_check:
            return True
        try:
            last = datetime.fromisoformat(s.last_update_check)
        except ValueError:
            return True
        now = now or datetime.now(timezone.utc)
        return now - last >= _AUTO_CHECK_EVERY

    def maybe_auto_check(self) -> None:
        if self._auto_worker is not None or not self.auto_check_due():
            return
        self._sm.update(last_update_check=datetime.now(timezone.utc).isoformat())
        worker = _CheckWorker(self)
        worker.done.connect(self._on_auto_checked)
        worker.failed.connect(lambda msg: logger.info("Automatic update check failed: %s", msg))
        worker.finished.connect(lambda: setattr(self, "_auto_worker", None))
        self._auto_worker = worker
        worker.start()

    def _on_auto_checked(self, result) -> None:
        self._last_result = result
        if result.update_available:
            self.update_found.emit(result)

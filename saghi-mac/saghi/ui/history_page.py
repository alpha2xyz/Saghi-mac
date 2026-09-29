"""
History page: search saved transcripts (saghi.history.search(), SQLite --
see history.py) and show the full text of a selected entry. Read-only from
the GUI's perspective; entries are written by api.py and filejobs.py, not
here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .. import history
from . import strings

_SEARCH_LIMIT = 50


def _format_created_at(iso_str: str) -> str:
    """
    history.created_at is stored as a full UTC ISO-8601 timestamp with
    microseconds (see history.py's schema docstring) -- far too noisy for
    a list row. Show "YYYY-MM-DD HH:MM" and isolate it per strings.py's
    mixed-text rule (it's a Latin/numeric run sitting right before Arabic
    text in the same label). Falls back to the raw string if it somehow
    doesn't parse, rather than raising out of a list-rendering loop.
    """
    try:
        dt = datetime.fromisoformat(iso_str)
        return strings.isolate_ltr(dt.strftime("%Y-%m-%d %H:%M"))
    except (ValueError, TypeError):
        return strings.isolate_ltr(iso_str)


class HistoryPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._entries: dict[int, dict] = {}
        self._selected_id: Optional[int] = None
        self._showing_raw = False

        layout = QVBoxLayout(self)

        top_row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(strings.HISTORY_SEARCH_PLACEHOLDER)
        self.search_box.textChanged.connect(self._on_search_changed)
        top_row.addWidget(self.search_box)

        self.refresh_btn = QPushButton(strings.HISTORY_REFRESH)
        self.refresh_btn.clicked.connect(self.refresh)
        top_row.addWidget(self.refresh_btn)
        layout.addLayout(top_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.results_list = QListWidget()
        # Long entries (a full sentence after the timestamp) would
        # otherwise force a horizontal scrollbar -- elide instead, which
        # reads far better in a narrow list column.
        self.results_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.results_list.currentRowChanged.connect(self._on_row_changed)
        splitter.addWidget(self.results_list)

        detail_widget = QWidget()
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.setContentsMargins(0, 0, 0, 0)

        self.detail_view = QPlainTextEdit()
        self.detail_view.setReadOnly(True)
        detail_layout.addWidget(self.detail_view)

        btn_row = QHBoxLayout()
        self.copy_btn = QPushButton(strings.HISTORY_COPY_BUTTON)
        self.copy_btn.clicked.connect(self._copy_text)
        btn_row.addWidget(self.copy_btn)

        self.raw_toggle_btn = QPushButton(strings.HISTORY_RAW_TOGGLE)
        self.raw_toggle_btn.setCheckable(True)
        self.raw_toggle_btn.toggled.connect(self._on_raw_toggled)
        btn_row.addWidget(self.raw_toggle_btn)
        btn_row.addStretch()
        detail_layout.addLayout(btn_row)

        splitter.addWidget(detail_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

        self.refresh()

    def refresh(self) -> None:
        query = self.search_box.text().strip()
        entries = history.search(limit=_SEARCH_LIMIT, query=query)
        self._entries = {e["id"]: e for e in entries}

        self.results_list.blockSignals(True)
        self.results_list.clear()
        for e in entries:
            body = e.get("text") or e.get("raw_text") or ""
            first_line = body.splitlines()[0] if body else ""
            item = QListWidgetItem(f"{_format_created_at(e['created_at'])}  —  {first_line}")
            item.setData(Qt.ItemDataRole.UserRole, e["id"])
            self.results_list.addItem(item)
        self.results_list.blockSignals(False)

        if entries:
            self.results_list.setCurrentRow(0)
        else:
            self._selected_id = None
            self.detail_view.setPlainText(strings.HISTORY_EMPTY)

    def _on_search_changed(self, _text: str) -> None:
        self.refresh()

    def _on_row_changed(self, row: int) -> None:
        if row < 0:
            self._selected_id = None
            self.detail_view.setPlainText("")
            return
        item = self.results_list.item(row)
        self._selected_id = item.data(Qt.ItemDataRole.UserRole)
        self._update_detail_view()

    def _on_raw_toggled(self, checked: bool) -> None:
        self._showing_raw = checked
        self._update_detail_view()

    def _update_detail_view(self) -> None:
        if self._selected_id is None or self._selected_id not in self._entries:
            self.detail_view.setPlainText("")
            return
        entry = self._entries[self._selected_id]
        text = entry["raw_text"] if self._showing_raw else entry["text"]
        self.detail_view.setPlainText(text or "")

    def _copy_text(self) -> None:
        QApplication.clipboard().setText(self.detail_view.toPlainText())

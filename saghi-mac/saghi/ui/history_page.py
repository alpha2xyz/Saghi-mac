"""
History page: search saved transcripts (saghi.history.search(), SQLite --
see history.py) and show the full text of a selected entry.

Layout: a list of two-line entries on one side (date + source, then the
start of the text), the selected entry in a card on the other side with
its details (duration, language, cleanup level) and actions: copy, raw
text, export as a .txt file, delete. An empty history shows a friendly
empty state instead of two blank panes.

Entries are written by api.py, filejobs.py and ui/dictation.py; this page
only reads them, deletes single entries, and exports them.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from .. import history
from . import strings, theme, widgets

_SEARCH_LIMIT = 50

_ROLE_ID = Qt.ItemDataRole.UserRole
_ROLE_DATE = Qt.ItemDataRole.UserRole + 1
_ROLE_META = Qt.ItemDataRole.UserRole + 2
_ROLE_SNIPPET = Qt.ItemDataRole.UserRole + 3


def _format_created_at(iso_str: str) -> str:
    """
    history.created_at is stored as a full UTC ISO-8601 timestamp with
    microseconds (see history.py's schema docstring) -- far too noisy for
    a list row. Show local "YYYY-MM-DD HH:MM" and isolate it per
    strings.py's mixed-text rule (it's a Latin/numeric run sitting next to
    Arabic text). Falls back to the raw string if it somehow doesn't parse,
    rather than raising out of a list-rendering loop.
    """
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is not None:
            dt = dt.astimezone()  # show the user's local time, not UTC
        return strings.isolate_ltr(dt.strftime("%Y-%m-%d %H:%M"))
    except (ValueError, TypeError):
        return strings.isolate_ltr(iso_str)


class _EntryDelegate(QStyledItemDelegate):
    """Paints each entry as a rounded two-line row; the selected one gets the accent capsule."""

    # Row height follows the fonts' real line heights: Amiri (the default
    # interface font) has much taller ascenders/descenders than the system
    # font, so a fixed height made the two lines overlap.
    _PAD_Y = 8
    _GAP = 2

    @staticmethod
    def _fonts():
        body = QApplication.font()
        small = QFont(body)
        if small.pixelSize() > 0:
            small.setPixelSize(max(9, small.pixelSize() - 2))
        else:
            small.setPointSizeF(max(8.0, small.pointSizeF() - 2))
        return small, body

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 -- Qt override
        small, body = self._fonts()
        h = QFontMetrics(small).height() + self._GAP + QFontMetrics(body).height() + 2 * self._PAD_Y + 4
        return QSize(option.rect.width(), h)

    def paint(self, painter: QPainter, option, index) -> None:
        t = theme.tokens()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(option.rect).adjusted(4, 2, -4, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.accent))
            painter.drawRoundedRect(rect, 9, 9)
        elif hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.qcolor(t.hover))
            painter.drawRoundedRect(rect, 9, 9)

        primary = QColor(t.accent_text) if selected else QColor(t.text)
        secondary = QColor(255, 255, 255, 200) if selected else QColor(t.text_secondary)
        text_rect = rect.adjusted(12, self._PAD_Y, -12, -self._PAD_Y)
        align = Qt.AlignmentFlag.AlignRight if option.direction == Qt.LayoutDirection.RightToLeft else Qt.AlignmentFlag.AlignLeft

        small, body = self._fonts()
        painter.setFont(small)
        painter.setPen(QPen(secondary))
        top = f"{index.data(_ROLE_DATE)}  ·  {index.data(_ROLE_META)}"
        fm_small = QFontMetrics(small)
        painter.drawText(
            QRectF(text_rect.left(), text_rect.top(), text_rect.width(), fm_small.height()),
            int(align | Qt.AlignmentFlag.AlignVCenter),
            fm_small.elidedText(top, Qt.TextElideMode.ElideRight, int(text_rect.width())),
        )

        painter.setFont(body)
        painter.setPen(QPen(primary))
        fm = QFontMetrics(body)
        snippet = index.data(_ROLE_SNIPPET) or ""
        painter.drawText(
            QRectF(text_rect.left(), text_rect.top() + fm_small.height() + self._GAP, text_rect.width(), fm.height()),
            int(align | Qt.AlignmentFlag.AlignVCenter),
            fm.elidedText(snippet, Qt.TextElideMode.ElideRight, int(text_rect.width())),
        )
        painter.restore()


class HistoryPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._entries: dict[int, dict] = {}
        self._selected_id: Optional[int] = None
        self._showing_raw = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 0, 28, 24)
        layout.setSpacing(14)

        top_row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(strings.HISTORY_SEARCH_PLACEHOLDER)
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setMaximumWidth(360)
        self.search_box.textChanged.connect(self._on_search_changed)
        top_row.addWidget(self.search_box)
        top_row.addStretch()
        self.refresh_btn = QPushButton(strings.HISTORY_REFRESH)
        self.refresh_btn.clicked.connect(self.refresh)
        top_row.addWidget(self.refresh_btn)
        layout.addLayout(top_row)

        # ---- list + detail --------------------------------------------------
        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(14)

        list_card = widgets.SettingsCard()
        list_card.layout().setContentsMargins(6, 6, 6, 6)
        self.results_list = QListWidget()
        self.results_list.setObjectName("historyList")
        self.results_list.setItemDelegate(_EntryDelegate(self.results_list))
        self.results_list.setMouseTracking(True)
        self.results_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Long entries would otherwise force a horizontal scrollbar -- the
        # delegate elides instead, which reads far better in a list column.
        self.results_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.results_list.currentRowChanged.connect(self._on_row_changed)
        list_card.layout().addWidget(self.results_list)
        list_card.setMinimumWidth(260)
        splitter.addWidget(list_card)

        detail_card = widgets.SettingsCard()
        dl = detail_card.layout()
        dl.setContentsMargins(18, 14, 18, 14)
        dl.setSpacing(10)
        self.meta_label = widgets.label("", "secondaryText", wrap=True)
        dl.addWidget(self.meta_label)
        dl.addWidget(widgets.separator())
        self.detail_view = QPlainTextEdit()
        self.detail_view.setObjectName("cardText")
        self.detail_view.setReadOnly(True)
        self.detail_view.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        dl.addWidget(self.detail_view, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.copy_btn = QPushButton(strings.HISTORY_COPY_BUTTON)
        self.copy_btn.clicked.connect(self._copy_text)
        btn_row.addWidget(self.copy_btn)

        self.raw_toggle_btn = QPushButton(strings.HISTORY_RAW_TOGGLE)
        self.raw_toggle_btn.setCheckable(True)
        self.raw_toggle_btn.toggled.connect(self._on_raw_toggled)
        btn_row.addWidget(self.raw_toggle_btn)

        self.export_btn = QPushButton(strings.HISTORY_EXPORT_BUTTON)
        self.export_btn.clicked.connect(lambda: self.export_selected())
        btn_row.addWidget(self.export_btn)
        btn_row.addStretch()

        self.delete_btn = QPushButton(strings.HISTORY_DELETE_BUTTON)
        self.delete_btn.clicked.connect(lambda: self.delete_selected())
        btn_row.addWidget(self.delete_btn)
        dl.addLayout(btn_row)
        splitter.addWidget(detail_card)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        self._stack.addWidget(splitter)

        # ---- empty state ---------------------------------------------------------
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.addStretch()
        self._empty_icon = QLabel()
        self._empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev.addWidget(self._empty_icon)
        self.empty_title = widgets.label(strings.HISTORY_EMPTY, "emptyTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev.addWidget(self.empty_title)
        self.empty_hint = widgets.label(strings.HISTORY_EMPTY_HINT, "secondaryText", wrap=True)
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev.addWidget(self.empty_hint)
        ev.addStretch(2)
        self._stack.addWidget(empty)

        self.refresh()

    # ---- data ------------------------------------------------------------------

    def refresh(self) -> None:
        query = self.search_box.text().strip()
        entries = history.search(limit=_SEARCH_LIMIT, query=query)
        self._entries = {e["id"]: e for e in entries}

        self.results_list.blockSignals(True)
        self.results_list.clear()
        for e in entries:
            body = e.get("text") or e.get("raw_text") or ""
            first_line = body.splitlines()[0] if body else ""
            date = _format_created_at(e["created_at"])
            item = QListWidgetItem(f"{date}  —  {first_line}")
            item.setData(_ROLE_ID, e["id"])
            item.setData(_ROLE_DATE, date)
            item.setData(_ROLE_META, strings.history_source_label(e.get("source", "")))
            item.setData(_ROLE_SNIPPET, first_line)
            self.results_list.addItem(item)
        self.results_list.blockSignals(False)

        if entries:
            self._stack.setCurrentIndex(0)
            self.results_list.setCurrentRow(0)
        else:
            self._selected_id = None
            self.detail_view.setPlainText(strings.HISTORY_EMPTY)
            self.meta_label.setText("")
            self._set_actions_enabled(False)
            self._show_empty(searching=bool(query))

    def _show_empty(self, searching: bool) -> None:
        self._empty_icon.setPixmap(widgets.icon_pixmap("empty", 48, QColor(theme.tokens().text_secondary)))
        self.empty_title.setText(strings.HISTORY_NO_RESULTS if searching else strings.HISTORY_EMPTY)
        self.empty_hint.setVisible(not searching)
        self._stack.setCurrentIndex(1)

    def _set_actions_enabled(self, enabled: bool) -> None:
        for btn in (self.copy_btn, self.raw_toggle_btn, self.export_btn, self.delete_btn):
            btn.setEnabled(enabled)

    def _on_search_changed(self, _text: str) -> None:
        self.refresh()

    def _on_row_changed(self, row: int) -> None:
        if row < 0:
            self._selected_id = None
            self.detail_view.setPlainText("")
            self.meta_label.setText("")
            self._set_actions_enabled(False)
            return
        item = self.results_list.item(row)
        self._selected_id = item.data(_ROLE_ID)
        self._set_actions_enabled(True)
        self._update_detail_view()

    def _on_raw_toggled(self, checked: bool) -> None:
        self._showing_raw = checked
        self._update_detail_view()

    def _update_detail_view(self) -> None:
        if self._selected_id is None or self._selected_id not in self._entries:
            self.detail_view.setPlainText("")
            self.meta_label.setText("")
            return
        entry = self._entries[self._selected_id]
        text = entry["raw_text"] if self._showing_raw else entry["text"]
        self.detail_view.setPlainText(text or "")
        self.meta_label.setText(f"{_format_created_at(entry['created_at'])}  ·  {strings.history_meta(entry)}")

    # ---- actions -------------------------------------------------------------------

    def _copy_text(self) -> None:
        QApplication.clipboard().setText(self.detail_view.toPlainText())

    def delete_selected(self, confirm: bool = True) -> bool:
        """Delete the selected entry (asks first unless confirm=False). Returns whether it was deleted."""
        if self._selected_id is None:
            return False
        if confirm and not widgets.confirm_destructive(
            self, strings.HISTORY_CONFIRM_DELETE_TITLE, strings.HISTORY_CONFIRM_DELETE_TEXT
        ):
            return False
        row = self.results_list.currentRow()
        history.delete_entry(self._selected_id)
        self.refresh()
        if self.results_list.count():
            self.results_list.setCurrentRow(min(row, self.results_list.count() - 1))
        return True

    def export_selected(self, path: Optional[str] = None) -> Optional[Path]:
        """Save the shown text as a UTF-8 .txt file (asks where unless `path` is given)."""
        if self._selected_id is None:
            return None
        if path is None:
            entry = self._entries.get(self._selected_id, {})
            stamp = (entry.get("created_at") or "")[:16].replace(":", "-").replace("T", " ")
            suggested = str(Path.home() / "Documents" / f"صاغي {stamp}.txt")
            path, _ = QFileDialog.getSaveFileName(self, strings.HISTORY_EXPORT_BUTTON, suggested, strings.HISTORY_EXPORT_FILTER)
            if not path:
                return None
        out = Path(path)
        if out.suffix.lower() != ".txt":
            out = out.with_suffix(".txt")
        out.write_text(self.detail_view.toPlainText() + "\n", encoding="utf-8")
        return out

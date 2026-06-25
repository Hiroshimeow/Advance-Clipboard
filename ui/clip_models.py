from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QAbstractListModel, QModelIndex, Qt


ROW_ROLE = Qt.ItemDataRole.UserRole + 100
GROUP_ROLE = Qt.ItemDataRole.UserRole + 101


@dataclass(frozen=True)
class ClipRow:
    row_kind: str
    clip: dict[str, Any] | None = None
    group_name: str = ""
    group_count: int = 0
    is_pinned: bool = False
    is_grouped_child: bool = False
    is_group_expanded: bool = False
    is_expanded: bool = False
    search_query: str = ""

    @property
    def clip_id(self) -> int | None:
        if not self.clip:
            return None
        return self.clip.get("id")

    @property
    def is_clip(self) -> bool:
        return self.row_kind == "clip" and bool(self.clip)


class ClipListItem:
    """Small QModelIndex-backed adapter for legacy controller/test call sites."""

    def __init__(self, model: "ClipListModel", row: int):
        self._model = model
        self._row = row
        self._extra: dict[int, Any] = {}

    def data(self, role: int | Qt.ItemDataRole):
        row = self._model.row_at(self._row)
        if row is None:
            return self._extra.get(int(role))
        if role == Qt.ItemDataRole.UserRole:
            return row.clip if row.is_clip else None
        if role == Qt.ItemDataRole.UserRole + 1:
            return row.group_name if row.is_grouped_child else None
        if role == ROW_ROLE:
            return row
        if role == GROUP_ROLE:
            return row.group_name
        return self._extra.get(int(role))

    def setData(self, role: int | Qt.ItemDataRole, value):
        if role == Qt.ItemDataRole.UserRole and isinstance(value, dict):
            current = self._model.row_at(self._row)
            group_name = ""
            is_grouped_child = False
            if current is not None:
                group_name = current.group_name
                is_grouped_child = current.is_grouped_child
            self._model.replace_row(
                self._row,
                ClipRow(
                    row_kind="clip",
                    clip=dict(value),
                    group_name=group_name,
                    is_grouped_child=is_grouped_child,
                    is_pinned=bool(value.get("is_pinned")),
                ),
            )
            return
        if role == Qt.ItemDataRole.UserRole + 1:
            current = self._model.row_at(self._row)
            if current is not None and current.is_clip:
                self._model.replace_row(
                    self._row,
                    ClipRow(
                        row_kind="clip",
                        clip=current.clip,
                        group_name=str(value or ""),
                        is_grouped_child=bool(value),
                        is_pinned=current.is_pinned,
                        is_expanded=current.is_expanded,
                        search_query=current.search_query,
                    ),
                )
                return
        self._extra[int(role)] = value

    def sizeHint(self):
        return self._model.size_hint_at(self._row)

    def setSizeHint(self, size):
        self._model.set_size_hint_at(self._row, size)

    def setHidden(self, hidden: bool):
        self._model.set_hidden_at(self._row, hidden)

    def isHidden(self) -> bool:
        return self._model.is_hidden_at(self._row)


class ClipListModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[ClipRow] = []
        self._size_hints: dict[int, Any] = {}
        self._hidden_rows: set[int] = set()

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.row_at(index.row())
        if row is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            if row.is_clip:
                return str(row.clip.get("content", ""))
            if row.row_kind == "group_header":
                return row.group_name
            return ""
        if role == Qt.ItemDataRole.UserRole:
            return row.clip if row.is_clip else None
        if role == Qt.ItemDataRole.UserRole + 1:
            return row.group_name if row.is_grouped_child else None
        if role == ROW_ROLE:
            return row
        if role == GROUP_ROLE:
            return row.group_name
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def set_rows(self, rows: list[ClipRow]):
        self.beginResetModel()
        self._rows = list(rows)
        self._size_hints.clear()
        self._hidden_rows.clear()
        self.endResetModel()

    def append_rows(self, rows: list[ClipRow]):
        if not rows:
            return
        start = len(self._rows)
        self.beginInsertRows(QModelIndex(), start, start + len(rows) - 1)
        self._rows.extend(rows)
        self.endInsertRows()

    def insert_row(self, row_index: int, row: ClipRow):
        row_index = max(0, min(row_index, len(self._rows)))
        self.beginInsertRows(QModelIndex(), row_index, row_index)
        self._rows.insert(row_index, row)
        self.endInsertRows()

    def take_row(self, row_index: int) -> ClipListItem | None:
        if not 0 <= row_index < len(self._rows):
            return None
        item = ClipListItem(self, row_index)
        self.beginRemoveRows(QModelIndex(), row_index, row_index)
        self._rows.pop(row_index)
        self.endRemoveRows()
        return item

    def remove_rows_by_clip_id(self, clip_id: int) -> bool:
        removed = False
        for row_index in range(len(self._rows) - 1, -1, -1):
            row = self._rows[row_index]
            if row.clip_id == clip_id:
                self.beginRemoveRows(QModelIndex(), row_index, row_index)
                self._rows.pop(row_index)
                self.endRemoveRows()
                removed = True
        return removed

    def replace_row(self, row_index: int, row: ClipRow):
        if not 0 <= row_index < len(self._rows):
            return
        self._rows[row_index] = row
        model_index = self.index(row_index, 0)
        self.dataChanged.emit(model_index, model_index, [])

    def row_at(self, row_index: int) -> ClipRow | None:
        if 0 <= row_index < len(self._rows):
            return self._rows[row_index]
        return None

    def item_at(self, row_index: int) -> ClipListItem | None:
        if 0 <= row_index < len(self._rows):
            return ClipListItem(self, row_index)
        return None

    def rows(self) -> list[ClipRow]:
        return list(self._rows)

    def set_size_hint_at(self, row_index: int, size):
        self._size_hints[row_index] = size

    def size_hint_at(self, row_index: int):
        return self._size_hints.get(row_index)

    def set_hidden_at(self, row_index: int, hidden: bool):
        if hidden:
            self._hidden_rows.add(row_index)
        else:
            self._hidden_rows.discard(row_index)

    def is_hidden_at(self, row_index: int) -> bool:
        return row_index in self._hidden_rows


class HistoryListModel(ClipListModel):
    pass


class PinnedListModel(ClipListModel):
    pass

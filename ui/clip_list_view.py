from __future__ import annotations

import math

from PyQt6.QtCore import QModelIndex, QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QListView,
    QStyleOptionViewItem,
)

from .clip_delegate import ClipRowDelegate
from .clip_models import ClipListItem, ClipListModel, ClipRow, ROW_ROLE
from .clip_context_menu import show_clip_context_menu


class ClipListView(QListView):
    copyRequested = pyqtSignal(dict)
    pinToggleRequested = pyqtSignal(int, bool)
    deleteRequested = pyqtSignal(int)
    expandToggleRequested = pyqtSignal(int)
    groupToggleRequested = pyqtSignal(str, bool)
    rowActivated = pyqtSignal(object)
    itemClicked = pyqtSignal(object)
    previewCandidate = pyqtSignal(dict)

    def __init__(self, model: ClipListModel | None = None, parent=None):
        super().__init__(parent)
        self._model = model or ClipListModel(self)
        self._delegate = ClipRowDelegate(self)
        self.setModel(self._model)
        self.setItemDelegate(self._delegate)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setMouseTracking(True)
        self.setUniformItemSizes(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._pressed_index = QModelIndex()
        self._pressed_action = None
        self._wheel_angle_pixel_remainder = 0.0
        self._last_preview_hover_key = None
        self.setProperty("activeSide", False)

    def currentChanged(self, current, previous):
        super().currentChanged(current, previous)
        self._last_preview_hover_key = None
        row = current.data(ROW_ROLE) if current.isValid() else None
        if isinstance(row, ClipRow) and row.is_clip:
            self.previewCandidate.emit(row.clip)

    def set_active_visual(self, active: bool):
        self.setProperty("activeSide", bool(active))
        self.style().unpolish(self)
        self.style().polish(self)
        self.viewport().update()

    def viewOptions(self):
        option = QStyleOptionViewItem()
        self.initViewItemOption(option)
        return option

    def set_rows(self, rows: list[ClipRow]):
        self._model.set_rows(rows)

    def append_rows(self, rows: list[ClipRow]):
        self._model.append_rows(rows)

    def rows(self) -> list[ClipRow]:
        return self._model.rows()

    def count(self) -> int:
        return self._model.rowCount()

    def item(self, row: int) -> ClipListItem | None:
        return self._model.item_at(row)

    def currentRow(self) -> int:
        index = self.currentIndex()
        return index.row() if index.isValid() else -1

    def setCurrentRow(self, row: int):
        if row < 0 or row >= self.count():
            self.setCurrentIndex(QModelIndex())
            return
        self.setCurrentIndex(self._model.index(row, 0))

    def currentItem(self) -> ClipListItem | None:
        return self.item(self.currentRow())

    def clear(self):
        self._model.set_rows([])

    def addItem(self, item):
        row = self._row_from_external_item(item)
        self._model.append_rows([row])

    def insertItem(self, row_index: int, item):
        row = self._row_from_external_item(item)
        self._model.insert_row(row_index, row)

    def takeItem(self, row_index: int):
        return self._model.take_row(row_index)

    def itemWidget(self, item):
        return None

    def setItemWidget(self, item, widget):
        raise RuntimeError("ClipListView does not support setItemWidget")

    def scrollToItem(self, item, hint=QAbstractItemView.ScrollHint.PositionAtCenter):
        row = self._row_for_item(item)
        if row is not None:
            self.scrollTo(self._model.index(row, 0), hint)

    def scrollToSelected(self, hint=QAbstractItemView.ScrollHint.PositionAtCenter):
        index = self.currentIndex()
        if index.isValid():
            self.scrollTo(index, hint)

    # -- Forwarding methods so ClipRowWidget can use this view as parent_list --

    @property
    def storage(self):
        w = self.window()
        return getattr(w, "storage", None)

    def handle_toggle_expand(self, clip_id):
        self.expandToggleRequested.emit(clip_id)

    def handle_copy_only(self, data):
        self.copyRequested.emit(data)

    def handle_star(self, clip_id, should_pin):
        self.pinToggleRequested.emit(clip_id, should_pin)

    def handle_delete(self, clip_id):
        self.deleteRequested.emit(clip_id)

    def handle_add_tag(self, clip_id, tag):
        w = self.window()
        if hasattr(w, "handle_add_tag"):
            w.handle_add_tag(clip_id, tag)

    def handle_set_group(self, clip_id, group):
        w = self.window()
        if hasattr(w, "handle_set_group"):
            w.handle_set_group(clip_id, group)

    def handle_fix_clip(self, clip_id, content):
        w = self.window()
        if hasattr(w, "handle_fix_clip"):
            w.handle_fix_clip(clip_id, content)

    def _clear_hover(self):
        self.viewport().update()

    def _row_for_item(self, item) -> int | None:
        if isinstance(item, ClipListItem):
            return item._row
        return None

    def _row_from_external_item(self, item) -> ClipRow:
        clip = None
        group_name = ""
        try:
            clip = item.data(Qt.ItemDataRole.UserRole)
            group_name = item.data(Qt.ItemDataRole.UserRole + 1) or ""
        except Exception:
            clip = None
        if isinstance(clip, dict):
            return ClipRow(
                row_kind="clip",
                clip=dict(clip),
                is_pinned=bool(clip.get("is_pinned")),
                group_name=str(group_name),
                is_grouped_child=bool(group_name),
            )
        text = ""
        try:
            text = item.text()
        except Exception:
            text = ""
        return ClipRow(row_kind="group_header", group_name=text, group_count=0)

    def mouseMoveEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        row = index.data(ROW_ROLE) if index.isValid() else None
        if isinstance(row, ClipRow) and row.is_clip:
            clip = row.clip
            key = (clip.get("id"), clip.get("type"))
            if key != self._last_preview_hover_key:
                self._last_preview_hover_key = key
                self.previewCandidate.emit(clip)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            self._pressed_index = QModelIndex()
            self._pressed_action = None
            super().mousePressEvent(event)
            return
        self._pressed_index = self.indexAt(event.position().toPoint())
        self._pressed_action = self._hit_action(self._pressed_index, event.position().toPoint())
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            self._pressed_index = QModelIndex()
            self._pressed_action = None
            super().mouseReleaseEvent(event)
            return
        index = self.indexAt(event.position().toPoint())
        action = self._hit_action(index, event.position().toPoint())
        if index.isValid() and index == self._pressed_index and action == self._pressed_action:
            self._emit_action(index, action)
            event.accept()
            self._pressed_index = QModelIndex()
            self._pressed_action = None
            return
        super().mouseReleaseEvent(event)
        self._pressed_index = QModelIndex()
        self._pressed_action = None

    def mouseDoubleClickEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        row = index.data(ROW_ROLE) if index.isValid() else None
        if isinstance(row, ClipRow) and row.is_clip:
            self.rowActivated.emit(row.clip)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        index = self.indexAt(event.pos())
        row = index.data(ROW_ROLE) if index.isValid() else None
        if not isinstance(row, ClipRow) or not row.is_clip:
            return
        show_clip_context_menu(self, row.clip, row.is_pinned, self.window(), event.globalPos())

    def wheelEvent(self, event):
        bar = self.verticalScrollBar()
        pixel_delta = event.pixelDelta().y()
        if pixel_delta:
            self._wheel_angle_pixel_remainder = 0.0
            bar.setValue(bar.value() - pixel_delta)
            event.accept()
            return
        angle_delta = event.angleDelta().y()
        if not angle_delta:
            event.ignore()
            return
        raw_step = (
            self._wheel_angle_pixel_remainder
            + (angle_delta / 120.0) * self._wheel_pixels_per_notch()
        )
        step = math.trunc(raw_step)
        self._wheel_angle_pixel_remainder = raw_step - step
        if step:
            bar.setValue(bar.value() - step)
        event.accept()

    def _wheel_pixels_per_notch(self) -> int:
        line_count = max(1, QApplication.wheelScrollLines())
        return max(24, line_count * self.fontMetrics().lineSpacing())

    def _hit_action(self, index, pos: QPoint):
        if not index.isValid():
            return None
        option = self.viewOptions()
        option.rect = self.visualRect(index)
        return self._delegate.hit_test(option, index, pos)

    def _emit_action(self, index, action):
        row = index.data(ROW_ROLE)
        if not isinstance(row, ClipRow):
            return
        if action == "group":
            self.groupToggleRequested.emit(row.group_name, not row.is_group_expanded)
            return
        if not row.is_clip:
            return
        if action == "copy":
            self.copyRequested.emit(row.clip)
        elif action == "pin" and row.clip_id:
            self.pinToggleRequested.emit(row.clip_id, not row.is_pinned)
        elif action == "delete" and row.clip_id:
            self.deleteRequested.emit(row.clip_id)
        elif action == "expand" and row.clip_id:
            self.expandToggleRequested.emit(row.clip_id)
        elif action == "row":
            item = self.item(index.row())
            self.itemClicked.emit(item)

from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate

from .clip_models import ROW_ROLE, ClipRow
from .clip_row import (
    ACTION_BUTTON_HEIGHT,
    ACTION_BUTTON_SPACING,
    ACTION_COLUMN_WIDTH,
    META_COLUMN_WIDTH,
    ROW_GROUP_INDENT,
    ROW_MARGIN_BOTTOM,
    ROW_MARGIN_LEFT,
    ROW_MARGIN_RIGHT,
    ROW_MARGIN_TOP,
    ROW_SPACING,
    ROW_FRAME_INSET_X,
    ClipRowMetrics,
    ClipRowState,
)
from .widgets import IMAGE_DIR, TAG_FONT, TEXT_FONT, THUMB_SIZE, MAX_DISPLAY_CHARS


@dataclass(frozen=True)
class RowRects:
    outer: QRect
    content: QRect
    meta: QRect
    actions: QRect
    copy: QRect
    pin: QRect
    delete: QRect
    expand: QRect
    tag: QRect


class ClipRowDelegate(QStyledItemDelegate):
    MAX_THUMBNAIL_CACHE_ENTRIES = 128

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._thumbnail_cache = OrderedDict()

    def sizeHint(self, option, index):
        row = index.data(ROW_ROLE)
        width = max(240, option.rect.width() or option.widget.viewport().width())
        if not isinstance(row, ClipRow):
            return QSize(width, 45)
        if row.row_kind == "group_header":
            return QSize(width, 45)
        metrics = self.measure_row(row, width)
        return QSize(width, metrics.row_height)

    def measure_row(self, row: ClipRow, available_width: int) -> ClipRowMetrics:
        return ClipRowMetrics.for_clip(
            row.clip or {},
            ClipRowState(
                is_pinned=row.is_pinned,
                is_grouped=row.is_grouped_child,
                expanded=row.is_expanded,
            ),
            available_width,
        )

    def rects_for(self, option, row: ClipRow) -> RowRects:
        outer = QRect(option.rect)
        if row.row_kind == "group_header":
            return RowRects(outer, outer, QRect(), QRect(), QRect(), QRect(), QRect(), QRect(), QRect())

        metrics = self.measure_row(row, outer.width())
        left = ROW_MARGIN_LEFT + (ROW_GROUP_INDENT if row.is_grouped_child else 0)
        x = outer.left() + left
        y = outer.top() + ROW_MARGIN_TOP
        content = QRect(x, y, metrics.content_width, metrics.content_height)
        meta_x = content.right() + 1 + ROW_SPACING
        meta = QRect(meta_x, y, META_COLUMN_WIDTH, metrics.meta_height)
        actions_x = outer.right() - ROW_MARGIN_RIGHT - ACTION_COLUMN_WIDTH + 1
        actions = QRect(actions_x, y, ACTION_COLUMN_WIDTH, metrics.actions_height)
        copy = QRect(actions_x, y, ACTION_COLUMN_WIDTH, ACTION_BUTTON_HEIGHT)
        pin = QRect(actions_x, copy.bottom() + 1 + ACTION_BUTTON_SPACING, ACTION_COLUMN_WIDTH, ACTION_BUTTON_HEIGHT)
        delete = QRect(actions_x, pin.bottom() + 1 + ACTION_BUTTON_SPACING, ACTION_COLUMN_WIDTH, ACTION_BUTTON_HEIGHT)
        expand = QRect(meta_x + 4, y + QFontMetrics(TAG_FONT).height() + 6, 28, 16)

        tag_text = self._badge_text(row)
        tag = QRect()
        if tag_text:
            tag_fm = QFontMetrics(TAG_FONT)
            tag_h = tag_fm.height() + 4
            tag_text_width = min(
                tag_fm.horizontalAdvance(tag_text) + 12,
                outer.width() - left - ROW_MARGIN_RIGHT,
            )
            tag_x = outer.right() - ROW_MARGIN_RIGHT - tag_text_width
            tag_y = delete.bottom() + 1 + ACTION_BUTTON_SPACING
            tag = QRect(tag_x, tag_y, tag_text_width, tag_h)
        return RowRects(outer, content, meta, actions, copy, pin, delete, expand, tag)

    def hit_test(self, option, index, pos: QPoint | None = None):  # type: ignore[name-defined]
        row = index.data(ROW_ROLE)
        if not isinstance(row, ClipRow):
            return None
        if row.row_kind == "group_header":
            return "group"
        rects = self.rects_for(option, row)
        if rects.copy.contains(pos):
            return "copy"
        if rects.pin.contains(pos):
            return "pin"
        if rects.delete.contains(pos):
            return "delete"
        if row.clip and row.clip.get("type") == "text" and rects.expand.contains(pos):
            return "expand"
        return "row" if rects.outer.contains(pos) else None

    def paint(self, painter: QPainter, option, index):
        row = index.data(ROW_ROLE)
        if not isinstance(row, ClipRow):
            return
        painter.save()
        try:
            if row.row_kind == "group_header":
                self._paint_group_header(painter, option, row)
            else:
                self._paint_clip(painter, option, row)
        finally:
            painter.restore()

    def _paint_group_header(self, painter, option, row: ClipRow):
        rect = option.rect.adjusted(0, 3, -1, -3)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.setPen(QPen(QColor("#aa8030" if hovered else "#3a3a3a"), 1))
        painter.setBrush(QColor("#353535" if hovered else "#2a2a2a"))
        painter.drawRoundedRect(rect, 4, 4)
        painter.setPen(QColor("#aa8030"))
        arrow = "▼" if row.is_group_expanded else "▶"
        painter.drawText(rect.adjusted(10, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, arrow)
        painter.setPen(QColor("#e0e0e0"))
        name_rect = rect.adjusted(38, 0, -46, 0)
        painter.setFont(TEXT_FONT)
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine, f"📁 {row.group_name}")
        count_rect = QRect(rect.right() - 38, rect.top() + 10, 28, 24)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#aa8030"))
        painter.drawRoundedRect(count_rect, 8, 8)
        painter.setPen(QColor("white"))
        painter.drawText(count_rect, Qt.AlignmentFlag.AlignCenter, str(row.group_count))

    def _paint_clip(self, painter, option, row: ClipRow):
        clip = row.clip or {}
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        bg = "#26394a" if selected else ("#2a3a4a" if hovered else "#1f1f1f")
        border = "#3daee9" if selected else ("#3a7abf" if hovered else "#333333")
        frame = option.rect.adjusted(ROW_FRAME_INSET_X, 3, -ROW_FRAME_INSET_X, -3)

        # 1. Fill background (no border yet)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(frame, 4, 4)

        # 2. Clip all content inside frame so nothing bleeds out
        painter.setClipRect(frame.adjusted(2, 2, -2, -2))

        rects = self.rects_for(option, row)

        # 3. Paint text / image content
        painter.setFont(TEXT_FONT)
        painter.setPen(QColor("#e0e0e0"))
        if clip.get("type") == "image":
            self._paint_image(painter, clip, rects.content)
        else:
            display_text = str(clip.get("content", ""))
            if not row.is_expanded and len(display_text) > MAX_DISPLAY_CHARS:
                display_text = display_text[:MAX_DISPLAY_CHARS] + "..."
            text_flags = int(Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextExpandTabs | Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            painter.drawText(rects.content, text_flags, display_text)

        if not row.is_expanded:
            # Expanded rows are covered by a persistent editor; painting these below it
            # leaks duplicate arrows through transparent gaps in the editor controls.
            painter.setFont(TAG_FONT)
            painter.setPen(QColor("#e6c36a"))
            line_count = len(str(clip.get("content", "")).splitlines()) if clip.get("type") == "text" else 1
            painter.drawText(QRect(rects.meta.left(), rects.meta.top(), rects.meta.width(), 18), Qt.AlignmentFlag.AlignCenter, str(line_count))
            if clip.get("type") == "text":
                self._paint_button(painter, rects.expand, "▼", "#333333", "#888888")

            self._paint_button(painter, rects.copy, "❐", "#2b5c75", "#dddddd")
            self._paint_button(painter, rects.pin, "★" if row.is_pinned else "☆", "#7a5c20" if row.is_pinned else "#3a3a3a", "#ffd700" if row.is_pinned else "#dddddd")
            self._paint_button(painter, rects.delete, "✕", "#752b2b", "#dddddd")

            tag_text = self._badge_text(row)
            if tag_text and not rects.tag.isNull():
                tag_font = QFont(TAG_FONT)
                tag_font.setItalic(True)
                painter.setFont(tag_font)
                painter.setPen(QColor("#aa8030"))
                painter.drawText(rects.tag, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), tag_text)

        # 7. Frame border LAST -- always on top of all content
        painter.setClipping(False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(border), 1))
        painter.drawRoundedRect(frame, 4, 4)

    def _paint_image(self, painter, clip, rect):
        image_rect = QRect(rect.left(), rect.top(), THUMB_SIZE.width(), THUMB_SIZE.height())
        painter.setPen(QPen(QColor("#444444"), 1))
        painter.setBrush(QColor("#000000"))
        painter.drawRoundedRect(image_rect, 4, 4)
        path = os.path.join(IMAGE_DIR, str(clip.get("content", "")))
        scaled = self._thumbnail_for_path(path)
        if scaled is None:
            return
        target = QRect(0, 0, scaled.width(), scaled.height())
        target.moveCenter(image_rect.center())
        painter.drawPixmap(target, scaled)

    def _thumbnail_for_path(self, path):
        try:
            stat = os.stat(path)
        except OSError:
            return None
        key = (path, stat.st_mtime_ns, stat.st_size)
        cached = self._thumbnail_cache.get(key)
        if cached is not None:
            self._thumbnail_cache.move_to_end(key)
            return cached

        pixmap = QPixmap(path)
        if pixmap.isNull():
            return None
        scaled = pixmap.scaled(
            THUMB_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self._thumbnail_cache[key] = scaled
        while len(self._thumbnail_cache) > self.MAX_THUMBNAIL_CACHE_ENTRIES:
            self._thumbnail_cache.popitem(last=False)
        return scaled

    def _paint_button(self, painter, rect, text, bg, fg):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(rect, 3, 3)
        painter.setPen(QColor(fg))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def createEditor(self, parent, option, index):
        row = index.data(ROW_ROLE)
        if not isinstance(row, ClipRow) or not row.is_clip or not row.is_expanded:
            return None
        from .clip_row import ClipRowWidget
        view = parent.parent()
        width = max(240, option.rect.width() or parent.width())
        widget = ClipRowWidget(
            row.clip,
            is_pinned=row.is_pinned,
            parent_list=view,
            is_grouped=row.is_grouped_child,
            expanded=True,
            available_width=width,
        )
        widget.setParent(parent)
        return widget

    def setEditorData(self, editor, index):
        pass

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)

    def _badge_text(self, row: ClipRow) -> str:
        clip = row.clip or {}
        tag = str(clip.get("tag", "") or "")
        group_name = str(clip.get("group_name", "") or "")
        return tag or (f"[{group_name}]" if group_name and not row.is_grouped_child else "")

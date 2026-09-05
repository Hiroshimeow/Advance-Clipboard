import math
import os
from dataclasses import dataclass

from PyQt6.QtCore import QPoint, QSize, Qt
from PyQt6.QtGui import QFontMetrics, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .clip_context_menu import show_clip_context_menu
from .widgets import (
    CLIP_TEXT_BOTTOM_PADDING,
    COLLAPSED_MAX_LINES,
    EXPANDED_MAX_LINES,
    IMAGE_DIR,
    MAX_DISPLAY_CHARS,
    TAG_FONT,
    TEXT_FONT,
    THUMB_SIZE,
)


ROW_MARGIN_TOP = 6
ROW_MARGIN_BOTTOM = 6
ROW_MARGIN_LEFT = 8
ROW_GROUP_INDENT = 14
ROW_MARGIN_RIGHT = 2
ROW_SPACING = 4
ROW_FRAME_INSET_X = 2
META_COLUMN_WIDTH = 36
ACTION_COLUMN_WIDTH = 32
ACTION_BUTTON_HEIGHT = 22
ACTION_BUTTON_SPACING = 4
TEXT_MAX_WIDTH = 520
TEXT_MIN_WIDTH = 120
EXPANDED_MIN_VISIBLE_LINES = 6
TAG_SPACING = 4


@dataclass(frozen=True)
class ClipRowState:
    is_pinned: bool = False
    is_grouped: bool = False
    expanded: bool = False
    selected: bool = False
    hovered: bool = False


@dataclass(frozen=True)
class ClipRowMetrics:
    available_width: int
    content_width: int
    content_height: int
    text_height: int
    meta_height: int
    actions_height: int
    row_height: int
    rendered_lines: int

    @classmethod
    def for_clip(cls, item_data: dict, state: ClipRowState, available_width: int):
        available_width = max(240, int(available_width))
        left_margin = ROW_MARGIN_LEFT + (ROW_GROUP_INDENT if state.is_grouped else 0)
        fixed_width = (
            left_margin
            + ROW_MARGIN_RIGHT
            + (ROW_SPACING * 2)
            + META_COLUMN_WIDTH
            + ACTION_COLUMN_WIDTH
        )
        content_width = max(TEXT_MIN_WIDTH, min(TEXT_MAX_WIDTH, available_width - fixed_width))
        actions_height = (ACTION_BUTTON_HEIGHT * 3) + (ACTION_BUTTON_SPACING * 2)

        tag_text = item_data.get("tag", "")
        group_name = item_data.get("group_name", "")
        badge_text = tag_text or (f"[{group_name}]" if group_name and not state.is_grouped else "")
        # Tag sits below action buttons (bottom-right), add to actions column
        if badge_text:
            actions_height += QFontMetrics(TAG_FONT).height() + 4 + ACTION_BUTTON_SPACING

        if item_data.get("type") == "text":
            text = item_data.get("content", "")
            display_text = text
            if not state.expanded and len(text) > MAX_DISPLAY_CHARS:
                display_text = text[:MAX_DISPLAY_CHARS] + "..."
            max_lines = EXPANDED_MAX_LINES if state.expanded else COLLAPSED_MAX_LINES
            rendered_lines, text_height = measure_visible_text(display_text, content_width, max_lines)
            if state.expanded:
                line_spacing = QFontMetrics(TEXT_FONT).lineSpacing()
                min_text_height = (
                    EXPANDED_MIN_VISIBLE_LINES * line_spacing
                ) + CLIP_TEXT_BOTTOM_PADDING + 2
                text_height = max(text_height, min_text_height)
            content_height = text_height
        else:
            rendered_lines = 1
            text_height = THUMB_SIZE.height()
            content_height = THUMB_SIZE.height()

        line_height = QFontMetrics(TAG_FONT).height() + 4
        expand_height = 16 if item_data.get("type") == "text" else 0
        meta_height = line_height + expand_height + ACTION_BUTTON_SPACING
        inner_height = max(content_height, meta_height, actions_height)
        row_height = inner_height + ROW_MARGIN_TOP + ROW_MARGIN_BOTTOM

        return cls(
            available_width=available_width,
            content_width=content_width,
            content_height=content_height,
            text_height=text_height,
            meta_height=meta_height,
            actions_height=actions_height,
            row_height=row_height,
            rendered_lines=rendered_lines,
        )


def measure_visible_text(text: str, width: int, max_lines: int) -> tuple[int, int]:
    metrics = QFontMetrics(TEXT_FONT)
    wrap_width = max(40, width)
    rect = metrics.boundingRect(
        0,
        0,
        wrap_width,
        10000,
        int(Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextExpandTabs),
        text,
    )
    line_spacing = metrics.lineSpacing()
    rendered_lines = max(1, math.ceil(max(rect.height(), line_spacing) / line_spacing))
    visible_lines = min(rendered_lines, max_lines)
    return rendered_lines, (visible_lines * line_spacing) + CLIP_TEXT_BOTTOM_PADDING + 2


def measure_tag_height(text: str, width: int) -> int:
    metrics = QFontMetrics(TAG_FONT)
    rect = metrics.boundingRect(
        0,
        0,
        max(40, width),
        10000,
        int(Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextExpandTabs),
        text,
    )
    return max(metrics.height(), rect.height()) + 6


class ClipContentView(QWidget):
    def __init__(self, item_data: dict, state: ClipRowState, metrics: ClipRowMetrics):
        super().__init__()
        self.item_data = item_data
        self.state = state
        self.metrics = metrics
        self.image_path = None
        self._loaded_pixmap_cache = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setFixedWidth(metrics.content_width)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        if item_data.get("type") == "text":
            self.lbl_content = self._build_text_content()
        else:
            self.lbl_content = self._build_image_content()
        layout.addWidget(self.lbl_content, 0, Qt.AlignmentFlag.AlignTop)
        self.lbl_tag = self._build_tag()
        if self.lbl_tag is not None:
            layout.addSpacing(TAG_SPACING)
            layout.addWidget(self.lbl_tag, 0, Qt.AlignmentFlag.AlignLeft)
        self.setMinimumHeight(metrics.content_height)

    def _build_text_content(self):
        text = self.item_data.get("content", "")
        display_text = text
        if not self.state.expanded and len(text) > MAX_DISPLAY_CHARS:
            display_text = text[:MAX_DISPLAY_CHARS] + "..."

        if self.state.expanded:
            label = QPlainTextEdit(self)
            label.setPlainText(display_text)
            label.setReadOnly(True)
            label.setFrameStyle(QFrame.Shape.NoFrame)
            label.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
            label.document().setDocumentMargin(0)
            label.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            label.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            label.setStyleSheet(
                "QPlainTextEdit { color: #e0e0e0; background: #1f1f1f; padding: 0px; }"
                "QPlainTextEdit::viewport { background: #1f1f1f; }"
            )
            label.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            label.viewport().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        else:
            label = QLabel(display_text, self)
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            label.setStyleSheet("color: #e0e0e0; background: transparent;")
        label.setFont(TEXT_FONT)
        label.setFixedWidth(self.metrics.content_width)
        label.setFixedHeight(self.metrics.text_height)
        label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        return label

    def _build_image_content(self):
        label = QLabel(self)
        label.setFixedSize(THUMB_SIZE)
        label.setScaledContents(False)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("border: 1px solid #444; background-color: #000; border-radius: 4px;")
        self.image_path = os.path.join(IMAGE_DIR, self.item_data.get("content", ""))
        if os.path.exists(self.image_path):
            self._apply_thumbnail_pixmap(label)
        return label

    def _build_tag(self):
        tag_text = self.item_data.get("tag", "")
        group_name = self.item_data.get("group_name", "")
        badge_text = tag_text or (
            f"[{group_name}]" if group_name and not self.state.is_grouped else ""
        )
        if not badge_text:
            return None
        label = QLabel(badge_text, self)
        label.setFont(TAG_FONT)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setMaximumWidth(self.metrics.content_width)
        label.setMinimumWidth(self.metrics.content_width)
        label.setMinimumHeight(measure_tag_height(badge_text, self.metrics.content_width))
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        label.setStyleSheet(
            """
            QLabel {
                color: #d18616;
                font-size: 7pt;
                font-style: italic;
                font-weight: normal;
                background: rgba(209, 134, 22, 0.15);
                border-radius: 3px;
                padding: 1px 5px;
                margin: 0px;
            }
            """
        )
        return label

    def _apply_thumbnail_pixmap(self, label):
        pix = self._loaded_pixmap_cache
        if pix is None or pix.isNull():
            pix = QPixmap(self.image_path)
            self._loaded_pixmap_cache = pix
        if pix.isNull():
            return
        label.setPixmap(
            pix.scaled(
                THUMB_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        )


class ClipMetaColumn(QWidget):
    def __init__(self, item_data: dict, state: ClipRowState, line_count: int, parent_row):
        super().__init__()
        self.item_data = item_data
        self.state = state
        self.parent_row = parent_row
        self.setFixedWidth(META_COLUMN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 0, 0, 0)
        layout.setSpacing(ACTION_BUTTON_SPACING)

        self.lbl_line_count = QLabel(str(line_count), self)
        self.lbl_line_count.setToolTip("So dong")
        self.lbl_line_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_line_count.setStyleSheet(
            "QLabel { color: #e6c36a; background: #1f1f1f; font-size: 8pt; font-weight: 600; }"
        )
        self.lbl_line_count.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout.addWidget(self.lbl_line_count)

        self.btn_expand = None
        if item_data.get("type") == "text":
            self.btn_expand = QPushButton("▲" if state.expanded else "▼", self)
            self.btn_expand.setToolTip("Collapse" if state.expanded else "Expand")
            self.btn_expand.setFixedSize(28, 16)
            self.btn_expand.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_expand.setStyleSheet(
                "QPushButton { background: #333; color: #888; border: none; border-radius: 2px; font-size: 8pt; }"
                "QPushButton:hover { background: #444; color: #fff; }"
            )
            self.btn_expand.clicked.connect(parent_row.on_expand_clicked)
            layout.addWidget(self.btn_expand)

        layout.addStretch()


class ClipActionColumn(QWidget):
    def __init__(self, state: ClipRowState, parent_row):
        super().__init__()
        self.state = state
        self.parent_row = parent_row
        self.setFixedWidth(ACTION_COLUMN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(ACTION_BUTTON_SPACING)

        layout.addWidget(
            self._create_button("❐", "Copy", "#2b5c75", "#3daee9", parent_row.on_copy_clicked)
        )
        star_char = "★" if state.is_pinned else "☆"
        star_bg = "#7a5c20" if state.is_pinned else "#3a3a3a"
        star_hover = "#aa8030" if state.is_pinned else "#555"
        self.btn_star = self._create_button(
            star_char, "Pin/Unpin", star_bg, star_hover, parent_row.on_star_clicked
        )
        if state.is_pinned:
            self.btn_star.setStyleSheet(self.btn_star.styleSheet() + "color: #ffd700;")
        layout.addWidget(self.btn_star)
        layout.addWidget(
            self._create_button("✕", "Delete", "#752b2b", "#e93d3d", parent_row.on_delete_clicked)
        )

    def _create_button(self, text, tooltip, color, hover, callback):
        button = QPushButton(text, self)
        button.setToolTip(tooltip)
        button.setFixedSize(ACTION_COLUMN_WIDTH, ACTION_BUTTON_HEIGHT)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(
            f"QPushButton {{ background: {color}; border: none; border-radius: 3px; color: #ddd; font-size: 8pt; }}"
            f"QPushButton:hover {{ background: {hover}; color: #fff; }}"
        )
        button.clicked.connect(callback)
        return button


class ClipRowWidget(QWidget):
    def __init__(
        self,
        item_data,
        is_pinned=False,
        parent_list=None,
        is_grouped=False,
        expanded=False,
        available_width=300,
        selected=False,
        hovered=False,
    ):
        super().__init__()
        self.item_data = item_data
        self.clip_id = item_data.get("id")
        self.is_pinned = is_pinned
        self.parent_list = parent_list
        self.is_grouped = is_grouped
        self.expanded = expanded
        self.available_width = max(240, int(available_width))
        self.selected = selected
        self.hovered = hovered
        self.state = ClipRowState(is_pinned, is_grouped, expanded, selected, hovered)
        self.line_count = (
            len(item_data.get("content", "").splitlines())
            if item_data.get("type") == "text"
            else 1
        )
        self.metrics = ClipRowMetrics.for_clip(item_data, self.state, self.available_width)
        self.rendered_lines = self.metrics.rendered_lines
        self.display_height = self.metrics.content_height

        self.setObjectName("clipRow")
        self.setFixedWidth(self.metrics.available_width)
        self.setMinimumHeight(self.metrics.row_height)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        left_margin = ROW_MARGIN_LEFT + (ROW_GROUP_INDENT if is_grouped else 0)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(left_margin, ROW_MARGIN_TOP, ROW_MARGIN_RIGHT, ROW_MARGIN_BOTTOM)
        layout.setSpacing(ROW_SPACING)

        self.content_container = ClipContentView(item_data, self.state, self.metrics)
        self.lbl_content = self.content_container.lbl_content
        self.lbl_tag = self.content_container.lbl_tag
        self.btn_v_widget = ClipMetaColumn(item_data, self.state, self.line_count, self)
        self.lbl_line_count = self.btn_v_widget.lbl_line_count
        self.btn_expand = self.btn_v_widget.btn_expand
        self.btn_container = ClipActionColumn(self.state, self)
        self.btn_star = self.btn_container.btn_star

        layout.addWidget(self.content_container, 1)
        layout.addWidget(self.btn_v_widget, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.btn_container, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        self._apply_frame_style()

    def sizeHint(self):
        return QSize(self.metrics.available_width, self.metrics.row_height)

    def height(self):
        return self.metrics.row_height

    def set_hovered(self, hovered: bool):
        if self.hovered == hovered:
            return
        self.hovered = hovered
        self.state = ClipRowState(
            self.is_pinned, self.is_grouped, self.expanded, self.selected, self.hovered
        )
        self._apply_frame_style()

    def set_selected(self, selected: bool):
        if self.selected == selected:
            return
        self.selected = selected
        self.state = ClipRowState(
            self.is_pinned, self.is_grouped, self.expanded, self.selected, self.hovered
        )
        self._apply_frame_style()

    def _apply_frame_style(self):
        if self.selected:
            background = "#26394a"
            border = "#3daee9"
        elif self.hovered:
            background = "#2a3a4a"
            border = "#3a7abf"
        else:
            background = "#1f1f1f"
            border = "#333"
        self.setStyleSheet(
            f"#clipRow {{ background-color: {background}; border: 1px solid {border}; border-radius: 4px; }}"
        )

    def show_line_info(self):
        from .widgets import LineInfoPopup

        self.popup = LineInfoPopup(self.line_count)
        p = self.lbl_line_count.mapToGlobal(QPoint(0, 0))
        self.popup.show_at(QPoint(p.x() - self.popup.width() - 5, p.y()))

    def on_expand_clicked(self):
        if self.parent_list and self.clip_id:
            self.parent_list.handle_toggle_expand(self.clip_id)

    def on_copy_clicked(self):
        if self.parent_list:
            self.parent_list.handle_copy_only(self.item_data)

    def on_star_clicked(self):
        if self.parent_list and self.clip_id:
            self.parent_list.handle_star(self.clip_id, not self.is_pinned)

    def on_delete_clicked(self):
        if self.parent_list and self.clip_id:
            self.parent_list.handle_delete(self.clip_id)

    def contextMenuEvent(self, event):
        show_clip_context_menu(
            self,
            self.item_data,
            self.is_pinned,
            self.parent_list,
            self.mapToGlobal(event.pos()),
        )

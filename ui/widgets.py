import math
import os

from PyQt6.QtCore import Qt, QTimer, QPoint, QSize
from PyQt6.QtGui import QFont, QFontMetrics, QPixmap
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QApplication,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

# Base directory is one level up from this file (ui/ directory)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE_DIR = os.path.join(BASE_DIR, "images")

MAX_DISPLAY_CHARS = 300
THUMB_SIZE = QSize(80, 60)
PAGE_SIZE_HISTORY = 20
PAGE_SIZE_PINNED = 50
TEXT_FONT = QFont("Segoe UI", 10)
TAG_FONT = QFont("Segoe UI", 7)
COLLAPSED_MAX_LINES = 4
EXPANDED_MAX_LINES = 10
ROW_VERTICAL_PADDING = 10
CLIP_TEXT_BOTTOM_PADDING = 4


def _measure_text_lines(text: str, font: QFont, width: int) -> tuple[int, int]:
    metrics = QFontMetrics(font)
    wrap_width = max(40, width)
    rect = metrics.boundingRect(
        0,
        0,
        wrap_width,
        10000,
        int(Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextExpandTabs),
        text,
    )
    line_height = metrics.lineSpacing()
    rendered_lines = max(1, math.ceil(max(rect.height(), line_height) / line_height))
    return rendered_lines, line_height


def _visible_text_height(text: str, font: QFont, width: int, max_lines: int) -> tuple[int, int]:
    rendered_lines, line_height = _measure_text_lines(text, font, width)
    visible_lines = min(rendered_lines, max_lines)
    return rendered_lines, (visible_lines * line_height) + CLIP_TEXT_BOTTOM_PADDING


class SmoothListWidget(QListWidget):
    """QListWidget with smooth scrolling, hover highlighting, and variable-height item support."""

    HOVER_BACKGROUND = "#2a3a4a"
    HOVER_BORDER_COLOR = "#3a7abf"
    DEFAULT_BORDER_COLOR = "#333"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setMouseTracking(True)
        self.setStyleSheet(
            self.styleSheet()
            + "QScrollBar:vertical { width: 8px; margin: 0px; }"
              "QScrollBar::handle:vertical { min-height: 24px; background: #666; border-radius: 4px; }"
              "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }"
              "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
              "QListWidget[activeSide=\"true\"] { border-color: #aa8030; }"
              "QListWidget[activeSide=\"false\"] { border-color: #333; }"
        )
        self._hovered_row = -1
        self._hovered_item = None
        self._previous_border_color = ""
        self._wheel_angle_pixel_remainder = 0.0
        self.setProperty("activeSide", False)

    def set_active_visual(self, active: bool):
        self.setProperty("activeSide", bool(active))
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def _wheel_pixels_per_notch(self) -> int:
        line_count = max(1, QApplication.wheelScrollLines())
        return max(24, line_count * self.fontMetrics().lineSpacing())

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

    def _clear_hover(self):
        if self._hovered_item is not None:
            self._restore_item_border(self._hovered_item)
            self._hovered_item = None
            self._hovered_row = -1

    def _set_item_border(self, item, color):
        widget = self.itemWidget(item)
        if widget is None:
            return
        if hasattr(widget, "set_hovered"):
            widget.set_hovered(True)
            return
        widget.setProperty("hovered", True)

    def _restore_item_border(self, item):
        widget = self.itemWidget(item)
        if widget is None:
            return
        if hasattr(widget, "set_hovered"):
            widget.set_hovered(False)
            return
        widget.setProperty("hovered", False)

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)
        item = self.itemAt(e.position().toPoint()) if e else None
        new_row = self.row(item) if item else -1
        if new_row != self._hovered_row:
            self._clear_hover()
            if item and new_row >= 0:
                self._hovered_item = item
                self._hovered_row = new_row
                self._set_item_border(item, self.HOVER_BORDER_COLOR)

    def leaveEvent(self, a0):
        super().leaveEvent(a0)
        self._clear_hover()

    def scrollToSelected(self, hint: QAbstractItemView.ScrollHint = QAbstractItemView.ScrollHint.PositionAtCenter):
        """Scroll the currently selected item into view, centered in the viewport."""
        current = self.currentItem()
        if current is None:
            return
        self.scrollToItem(current, hint)


class LineInfoPopup(QWidget):
    def __init__(self, line_count, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.container = QFrame()
        self.container.setStyleSheet("""
            QFrame {
                background-color: #333333;
                color: #ffffff;
                border: 1px solid #d18616;
                border-radius: 5px;
            }
            QLabel { border: none; padding: 8px; font-size: 9pt; }
        """)
        container_layout = QVBoxLayout(self.container)
        lbl_greet = QLabel("Xin chào! 👋")
        lbl_greet.setStyleSheet("font-weight: bold; color: #d18616;")
        container_layout.addWidget(lbl_greet)
        container_layout.addWidget(
            QLabel(f"Clip này có tổng cộng {line_count} dòng văn bản.")
        )
        layout.addWidget(self.container)
        self.adjustSize()

    def leaveEvent(self, event):
        self.close()

    def show_at(self, pos):
        self.move(pos)
        self.show()
        self.activateWindow()


class SearchLineEdit(QLineEdit):
    """QLineEdit with triple-click to clear functionality."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.click_count = 0
        self.click_timer = QTimer()
        self.click_timer.setSingleShot(True)
        self.click_timer.timeout.connect(self._reset_click_count)
        self._on_up = None
        self._on_down = None
        self._on_enter = None

    def set_key_handlers(
        self, *, on_up=None, on_down=None, on_left=None, on_right=None, on_enter=None
    ):
        self._on_up = on_up
        self._on_down = on_down
        self._on_enter = on_enter

    def mousePressEvent(self, event):
        self.click_count += 1
        self.click_timer.start(400)
        if self.click_count >= 3:
            self.clear()
            self.click_count = 0
            self.click_timer.stop()
        super().mousePressEvent(event)

    def _reset_click_count(self):
        self.click_count = 0

    def keyPressEvent(self, event):
        k = event.key()
        if k == Qt.Key.Key_Up and self._on_up:
            self._on_up()
            event.accept()
            return
        if k == Qt.Key.Key_Down and self._on_down:
            self._on_down()
            event.accept()
            return
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._on_enter:
            self._on_enter()
            event.accept()
            return
        super().keyPressEvent(event)


class ClipEditPopup(QWidget):
    SCREEN_MARGIN = 12

    def __init__(self, clip_data, parent_list=None, parent=None):
        super().__init__(parent)
        self.clip_data = clip_data
        self.parent_list = parent_list
        self.clip_id = clip_data.get("id")
        self._fitting_to_screen = False
        self.setWindowFlags(Qt.WindowType.Tool)
        self.setWindowTitle(str(clip_data.get("tag") or ""))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMinimumSize(360, 220)
        self.resize(560, 360)
        self.setStyleSheet("""
            QWidget { background: #1f1f1f; color: #e0e0e0; }
            QLabel { color: #d18616; font-size: 9pt; font-weight: bold; }
            QFrame#popupChrome {
                background: #1f1f1f;
                border: 1px solid #3daee9;
                border-radius: 6px;
            }
            QPlainTextEdit {
                background: #2a2a2a;
                border: 1px solid #444;
                color: #f0f0f0;
                selection-background-color: #aa8030;
                font: 10pt 'Segoe UI';
            }
            QPushButton {
                background: #333;
                color: #eee;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 4px 10px;
            }
            QPushButton:hover { border-color: #aa8030; }
            QPushButton#saveButton {
                background: #aa8030;
                border-color: #aa8030;
                color: white;
            }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        chrome = QFrame(self)
        chrome.setObjectName("popupChrome")
        root.addWidget(chrome)

        layout = QVBoxLayout(chrome)
        layout.setContentsMargins(10, 10, 10, 8)
        layout.setSpacing(8)

        self.editor = QPlainTextEdit(chrome)
        self.editor.setPlainText(str(clip_data.get("content", "")))
        layout.addWidget(self.editor, stretch=1)

        self.lbl_error = QLabel("")
        self.lbl_error.setStyleSheet("color: #e57373; font-size: 8pt; font-weight: normal;")
        self.lbl_error.hide()
        layout.addWidget(self.lbl_error)

        footer = QHBoxLayout()
        footer.addStretch()
        btn_cancel = QPushButton("Cancel", chrome)
        btn_cancel.clicked.connect(self.close)
        footer.addWidget(btn_cancel)
        btn_save = QPushButton("Save", chrome)
        btn_save.setObjectName("saveButton")
        btn_save.clicked.connect(self._save)
        footer.addWidget(btn_save)
        layout.addLayout(footer)

    def showEvent(self, event):
        super().showEvent(event)
        self._fit_to_screen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._fitting_to_screen:
            QTimer.singleShot(0, self._fit_to_screen)

    def _screen_geometry(self):
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        return screen.availableGeometry() if screen else None

    def _fit_to_screen(self):
        screen = self._screen_geometry()
        if screen is None:
            return
        self._fitting_to_screen = True
        try:
            frame = self.frameGeometry()
            frame_extra_width = max(0, frame.width() - self.width())
            frame_extra_height = max(0, frame.height() - self.height())
            max_width = max(
                self.minimumWidth(),
                screen.width() - (self.SCREEN_MARGIN * 2) - frame_extra_width,
            )
            max_height = max(
                self.minimumHeight(),
                screen.height() - (self.SCREEN_MARGIN * 2) - frame_extra_height,
            )
            width = min(self.width(), max_width)
            height = min(self.height(), max_height)
            if width != self.width() or height != self.height():
                self.resize(width, height)
                frame = self.frameGeometry()

            x_min = screen.left() + self.SCREEN_MARGIN
            y_min = screen.top() + self.SCREEN_MARGIN
            x_max = screen.right() - frame.width() - self.SCREEN_MARGIN + 1
            y_max = screen.bottom() - frame.height() - self.SCREEN_MARGIN + 1
            frame_x = min(max(frame.left(), x_min), max(x_min, x_max))
            frame_y = min(max(frame.top(), y_min), max(y_min, y_max))
            if frame_x != frame.left() or frame_y != frame.top():
                self.move(self.pos() + (QPoint(frame_x, frame_y) - frame.topLeft()))
        finally:
            self._fitting_to_screen = False

    def _save(self):
        if not self.parent_list or not self.clip_id:
            self.close()
            return
        new_content = self.editor.toPlainText()
        try:
            self.parent_list.handle_fix_clip(self.clip_id, new_content)
        except ValueError as exc:
            self.lbl_error.setText(str(exc))
            self.lbl_error.show()
            return
        self.close()


class GroupHeaderWidget(QWidget):
    """Header for a group of clips - click to toggle expand/collapse."""

    def __init__(self, group_name, clip_count, parent_app=None):
        super().__init__()
        self.group_name = group_name
        self.clip_count = clip_count
        self.parent_app = parent_app
        self.is_expanded = False
        self.child_items = []
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout()
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        self.lbl_arrow = QLabel("▶")
        self.lbl_arrow.setStyleSheet("color: #aa8030; font-size: 12pt;")
        self.lbl_arrow.setFixedWidth(18)
        layout.addWidget(self.lbl_arrow)

        self.lbl_name = QLabel(f"📁 {group_name}")
        self.lbl_name.setStyleSheet(
            "color: #e0e0e0; font-size: 12pt; font-weight: bold;"
        )
        layout.addWidget(self.lbl_name, stretch=1)

        self.lbl_count = QLabel(f"{clip_count}")
        self.lbl_count.setStyleSheet("""
            QLabel {
                background: #aa8030;
                color: white;
                border-radius: 8px;
                padding: 2px 6px;
                font-size: 10pt;
                font-weight: bold;
            }
        """)
        layout.addWidget(self.lbl_count)

        self.setLayout(layout)
        self.setFixedHeight(45)
        self.setStyleSheet("""
            GroupHeaderWidget {
                background-color: #2a2a2a;
                border: 1px solid #3a3a3a;
                border-radius: 4px;
            }
            GroupHeaderWidget:hover {
                background-color: #353535;
                border-color: #aa8030;
            }
        """)

    def set_expanded(self, expanded):
        self.is_expanded = expanded
        self.lbl_arrow.setText("▼" if expanded else "▶")

    def mousePressEvent(self, event):
        if self.is_expanded:
            self.is_expanded = False
            self.lbl_arrow.setText("▶")
            if self.parent_app:
                self.parent_app.collapse_group(self.group_name)
        else:
            self.is_expanded = True
            self.lbl_arrow.setText("▼")
            if self.parent_app:
                self.parent_app.expand_group(self.group_name)
        super().mousePressEvent(event)


class ClipItemWidget(QWidget):
    def __init__(
        self,
        item_data,
        is_pinned=False,
        parent_list=None,
        is_grouped=False,
        expanded=False,
        available_width=300,
    ):
        super().__init__()
        self.item_data = item_data
        self.clip_id = item_data.get("id")
        self.is_pinned = is_pinned
        self.parent_list = parent_list
        self.is_grouped = is_grouped
        self.expanded = expanded
        self.available_width = max(240, int(available_width))
        self.action_width = 32
        self.side_badge_width = 36
        self.outer_left_margin = 8 if not is_grouped else 22
        self.outer_right_margin = 2
        self.outer_spacing = 4
        self.line_count = (
            len(self.item_data["content"].splitlines())
            if self.item_data["type"] == "text"
            else 1
        )

        layout = QHBoxLayout()
        layout.setContentsMargins(self.outer_left_margin, 6, self.outer_right_margin, 6)
        layout.setSpacing(self.outer_spacing)

        self.content_container = QWidget()
        self.content_container.setMinimumWidth(0)
        self.content_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.content_layout = QGridLayout(self.content_container)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(4)

        if self.item_data["type"] == "text":
            text = self.item_data["content"]
            display_text = text
            if not self.expanded and len(text) > MAX_DISPLAY_CHARS:
                display_text = text[:MAX_DISPLAY_CHARS] + "..."
            fixed_side_width = (
                self.outer_left_margin
                + self.outer_right_margin
                + (self.outer_spacing * 2)
                + self.action_width
                + self.side_badge_width
                + 4
                + (15 if is_grouped else 0)
            )
            text_width = max(120, min(520, self.available_width - fixed_side_width))
            self.rendered_lines, text_h = _visible_text_height(
                display_text,
                TEXT_FONT,
                text_width,
                EXPANDED_MAX_LINES if self.expanded else COLLAPSED_MAX_LINES,
            )
            if self.expanded and self.rendered_lines > EXPANDED_MAX_LINES:
                self.lbl_content = QPlainTextEdit(self)
                self.lbl_content.setPlainText(display_text)
                self.lbl_content.setReadOnly(True)
                self.lbl_content.setFrameStyle(QFrame.Shape.NoFrame)
                self.lbl_content.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
                self.lbl_content.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                self.lbl_content.setStyleSheet(
                    "QPlainTextEdit { color: #e0e0e0; background: transparent; padding: 0px; }"
                )
                self.lbl_content.setFont(TEXT_FONT)
            else:
                self.lbl_content = QLabel(display_text)
                self.lbl_content.setStyleSheet("color: #e0e0e0; background: transparent; padding-top: 1px; padding-bottom: 1px;")
                self.lbl_content.setFont(TEXT_FONT)
                self.lbl_content.setWordWrap(True)
                self.lbl_content.setAlignment(
                    Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
                )
            self.lbl_content.setMinimumWidth(0)
            self.lbl_content.setMaximumWidth(text_width)
            self.lbl_content.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
            )
            self.lbl_content.setFixedHeight(text_h)
            self.content_layout.addWidget(self.lbl_content, 0, 0)
            self.display_height = text_h
        else:
            self.lbl_content = QLabel()
            self.lbl_content.setFixedSize(THUMB_SIZE)
            self.lbl_content.setScaledContents(False)
            self.lbl_content.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.lbl_content.setStyleSheet(
                "border: 1px solid #444; background-color: #000; border-radius: 4px;"
            )
            p = os.path.join(IMAGE_DIR, self.item_data["content"])
            self.image_path = p
            self._loaded_pixmap_cache = None
            if os.path.exists(p):
                self._apply_thumbnail_pixmap()
            self.content_layout.addWidget(
                self.lbl_content,
                0,
                0,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            )
            self.display_height = THUMB_SIZE.height()

        # Tag label — stored here; added to badge column (btn_v_widget) later so layout exists first
        self.has_tag = False
        self.tag_height = 0
        tag_text = self.item_data.get("tag", "")
        group_name = self.item_data.get("group_name", "")
        badge_text = tag_text or (
            f"[{group_name}]" if group_name and not is_grouped else ""
        )
        self.has_tag = bool(badge_text)
        self._badge_text = badge_text
        if self.has_tag:
            tag_fm = QFontMetrics(TAG_FONT)
            self.tag_height = tag_fm.height() + 6

        self.btn_v_widget = QWidget()
        self.btn_v_widget.setFixedWidth(self.side_badge_width)
        self.btn_v_widget.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.btn_v_layout = QVBoxLayout(self.btn_v_widget)
        self.btn_v_layout.setContentsMargins(1, 0, 0, 0)
        self.btn_v_layout.setSpacing(4)

        # Insert tag label at the top of the badge column if present
        if self.has_tag:
            self.lbl_tag = QLabel(self._badge_text)
            self.lbl_tag.setStyleSheet("""
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
            """)
            self.lbl_tag.setFont(TAG_FONT)
            self.lbl_tag.setFixedHeight(self.tag_height)
            self.lbl_tag.setMaximumWidth(200)
            self.lbl_tag.setSizePolicy(
                QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
            )
            self.btn_v_layout.insertWidget(0, self.lbl_tag)

        def create_badge_btn(text, tooltip, style, func, h=16):
            btn = QPushButton(text)
            btn.setToolTip(tooltip)
            btn.setFixedSize(28, max(h, 16))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(style)
            btn.clicked.connect(func)
            return btn

        style_arrow = "QPushButton { background: #333; color: #888; border: none; border-radius: 2px; font-size: 8pt; } QPushButton:hover { background: #444; color: #fff; }"

        self.lbl_line_count = QLabel(str(self.line_count), self.content_container)
        self.lbl_line_count.setToolTip("Số dòng")
        self.lbl_line_count.setStyleSheet(
            "QLabel { color: #e6c36a; background: transparent; font-size: 8pt; font-weight: 600; }"
        )
        self.lbl_line_count.adjustSize()
        expand_symbol = "▲" if self.expanded else "▼"
        expand_tooltip = "Collapse" if self.expanded else "Expand"
        self.btn_expand = create_badge_btn(
            expand_symbol, expand_tooltip, style_arrow, self.on_expand_clicked, 14
        )

        if self.item_data["type"] == "text":
            self.btn_v_layout.addWidget(self.btn_expand)
        self.btn_v_layout.addStretch()

        # move badge column out of the content grid so it doesn't inflate text width
        self.content_layout.setColumnStretch(0, 1)
        layout.addWidget(self.content_container, 1)
        # badge column sits between text and action buttons
        layout.addWidget(self.btn_v_widget, 0, Qt.AlignmentFlag.AlignTop)

        self.btn_container = QWidget()
        self.btn_container.setFixedWidth(self.action_width)
        self.btn_container.setMinimumWidth(self.action_width)
        self.btn_container.setMaximumWidth(self.action_width)
        self.btn_container.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        btn_layout = QVBoxLayout(self.btn_container)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(4)

        def create_act_btn(text, tooltip, color, hover, func):
            btn = QPushButton(text)
            btn.setToolTip(tooltip)
            btn.setFixedSize(32, 22)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{ background: {color}; border: none; border-radius: 3px; color: #ddd; font-size: 8pt; }} QPushButton:hover {{ background: {hover}; color: #fff; }}"
            )
            btn.clicked.connect(func)
            return btn

        btn_layout.addWidget(
            create_act_btn("❐", "Copy", "#2b5c75", "#3daee9", self.on_copy_clicked)
        )
        star_char = "★" if is_pinned else "☆"
        star_bg = "#7a5c20" if is_pinned else "#3a3a3a"
        star_hover = "#aa8030" if is_pinned else "#555"
        self.btn_star = create_act_btn(
            star_char, "Pin/Unpin", star_bg, star_hover, self.on_star_clicked
        )
        if is_pinned:
            self.btn_star.setStyleSheet(self.btn_star.styleSheet() + "color: #ffd700;")
        btn_layout.addWidget(self.btn_star)
        btn_layout.addWidget(
            create_act_btn("✕", "Delete", "#752b2b", "#e93d3d", self.on_delete_clicked)
        )

        # ensure action column stays flush-right
        layout.addWidget(self.btn_container, alignment=Qt.AlignmentFlag.AlignRight)
        layout.setStretchFactor(self.content_container, 1)
        layout.setStretchFactor(self.btn_container, 0)
        self.setLayout(layout)

        # pinned rows need enough vertical space to show all action buttons (3 x 22 + spacing + margins ~= 78px)
        min_widget_h = 75 if self.is_pinned else 60
        total_h = self.display_height + self.tag_height
        self.setFixedWidth(self.available_width)
        self.setFixedHeight(max(total_h, min_widget_h) + ROW_VERTICAL_PADDING)
        self.lbl_line_count.move(
            max(0, self.content_container.width() - self.lbl_line_count.width() - 4),
            0,
        )

    def show_line_info(self):
        self.popup = LineInfoPopup(self.line_count)
        p = self.lbl_line_count.mapToGlobal(QPoint(0, 0))
        self.popup.show_at(QPoint(p.x() - self.popup.width() - 5, p.y()))

    def on_expand_clicked(self):
        if self.parent_list and self.clip_id:
            self.parent_list.handle_toggle_expand(self.clip_id)

    def on_copy_clicked(self):
        if self.parent_list:
            self.parent_list.handle_copy_only(self.item_data)

    def _apply_thumbnail_pixmap(self):
        if self.item_data.get("type") != "image":
            return
        image_path = getattr(self, "image_path", None)
        if not image_path or not os.path.exists(image_path):
            return
        pix = self._loaded_pixmap_cache
        if pix is None or pix.isNull():
            pix = QPixmap(image_path)
            self._loaded_pixmap_cache = pix
        if pix.isNull():
            return
        scaled = pix.scaled(
            THUMB_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self.lbl_content.setPixmap(scaled)

    def on_star_clicked(self):
        if self.parent_list and self.clip_id:
            self.parent_list.handle_star(self.clip_id, not self.is_pinned)

    def on_delete_clicked(self):
        if self.parent_list and self.clip_id:
            self.parent_list.handle_delete(self.clip_id)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #2d2d2d; color: #eee; border: 1px solid #444; }
            QMenu::item:selected { background-color: #d18616; color: white; }
        """)

        group_menu = menu.addMenu("📁 Add to Group")
        if self.parent_list:
            groups = self.parent_list.storage.get_groups()
            for g in groups:
                act = group_menu.addAction(g)
                act.setData(("group", g))
            if groups:
                group_menu.addSeparator()
            new_group_act = group_menu.addAction("➕ New Group...")
            new_group_act.setData(("new_group", None))
            current_group = self.item_data.get("group_name", "")
            if current_group:
                remove_act = menu.addAction(f"❌ Remove from '{current_group}'")
                remove_act.setData(("remove_group", None))
            menu.addSeparator()
        add_tag_act = menu.addAction("🏷️ Add Tag")
        add_tag_act.setData(("tag", None))
        if self.is_pinned and self.item_data.get("type") == "text":
            fix_act = menu.addAction("🛠 Fix")
            fix_act.setData(("fix", None))

        action = menu.exec(self.mapToGlobal(event.pos()))
        if action:
            data = action.data()
            if data:
                action_type, value = data
                if action_type == "tag":
                    self.on_add_tag()
                elif action_type == "group":
                    self.on_set_group(value)
                elif action_type == "new_group":
                    self.on_new_group()
                elif action_type == "remove_group":
                    self.on_set_group("")
                elif action_type == "fix":
                    self.on_fix_clip()

    def on_add_tag(self):
        current_tag = self.item_data.get("tag", "")
        tag, ok = QInputDialog.getText(
            self, "Add Tag", "Enter tag name:", text=current_tag
        )
        if ok and self.clip_id and self.parent_list:
            self.parent_list.handle_add_tag(self.clip_id, tag)

    def on_set_group(self, group_name):
        if self.clip_id and self.parent_list:
            self.parent_list.handle_set_group(self.clip_id, group_name)

    def on_new_group(self):
        group_name, ok = QInputDialog.getText(self, "New Group", "Enter group name:")
        if ok and group_name.strip() and self.clip_id and self.parent_list:
            self.parent_list.handle_set_group(self.clip_id, group_name.strip())

    def on_fix_clip(self):
        if not self.parent_list or not self.clip_id:
            return
        self.edit_popup = ClipEditPopup(self.item_data, self.parent_list, self)
        self.edit_popup.move(self.mapToGlobal(QPoint(10, 10)))
        self.edit_popup.show()


try:
    from .clip_row import ClipRowWidget

    ClipItemWidget = ClipRowWidget
except ImportError:
    def ClipItemWidget(*args, **kwargs):
        from .clip_row import ClipRowWidget

        return ClipRowWidget(*args, **kwargs)

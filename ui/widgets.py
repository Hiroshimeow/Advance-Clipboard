import math
import os

from PyQt6.QtCore import Qt, QTimer, QPoint, QSize
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QApplication,
    QTextEdit,
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
        self._search_matches = []
        self._current_search_index = -1
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
            QLineEdit {
                background: #252525;
                border: 1px solid #444;
                border-radius: 4px;
                color: #f0f0f0;
                padding: 4px 8px;
                selection-background-color: #aa8030;
            }
            QLineEdit:focus { border-color: #aa8030; }
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

        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(6)
        self.search_input = QLineEdit(chrome)
        self.search_input.setPlaceholderText("Search in clip")
        self.search_input.textChanged.connect(self._apply_search)
        self.search_input.returnPressed.connect(self._next_search_match)
        search_row.addWidget(self.search_input)
        layout.addLayout(search_row)

        self.editor = QPlainTextEdit(chrome)
        self.editor.setPlainText(str(clip_data.get("content", "")))
        self.editor.textChanged.connect(lambda: self._apply_search(self.search_input.text()))
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

    def _apply_search(self, query):
        self._search_matches = []
        self._current_search_index = -1
        self.editor.setExtraSelections([])
        query = str(query or "")
        if not query:
            return

        cursor = QTextCursor(self.editor.document())
        while True:
            cursor = self.editor.document().find(query, cursor)
            if cursor.isNull():
                break
            self._search_matches.append(QTextCursor(cursor))

        self._highlight_search_matches()
        if self._search_matches:
            self._current_search_index = 0
            self._show_search_match(0)

    def _highlight_search_matches(self):
        highlight_format = QTextCharFormat()
        highlight_format.setBackground(QColor("#5a4318"))
        highlight_format.setForeground(QColor("#ffffff"))
        selections = []
        for cursor in self._search_matches:
            selection = QTextEdit.ExtraSelection()
            selection.cursor = QTextCursor(cursor)
            selection.format = highlight_format
            selections.append(selection)
        self.editor.setExtraSelections(selections)

    def _show_search_match(self, index):
        if not self._search_matches:
            return
        index %= len(self._search_matches)
        self._current_search_index = index
        self.editor.setTextCursor(QTextCursor(self._search_matches[index]))
        self.editor.ensureCursorVisible()

    def _next_search_match(self):
        if not self._search_matches:
            return
        self._show_search_match(self._current_search_index + 1)

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



try:
    from .clip_row import ClipRowWidget

    ClipItemWidget = ClipRowWidget
except ImportError:
    def ClipItemWidget(*args, **kwargs):
        from .clip_row import ClipRowWidget

        return ClipRowWidget(*args, **kwargs)

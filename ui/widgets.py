import math
import os

from PyQt6.QtCore import Qt, QTimer, QPoint, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.action_runner import ActionRunner, is_valid_url

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
    """
    QLineEdit with triple-click to clear, Alt+1..6 quick paste,
    Ctrl+<key> clip shortcuts, and Ctrl+Enter action dispatch.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.click_count = 0
        self.click_timer = QTimer()
        self.click_timer.setSingleShot(True)
        self.click_timer.timeout.connect(self._reset_click_count)
        self._on_up = None
        self._on_down = None
        self._on_enter = None
        self._on_ctrl_enter = None
        self._on_alt_digit = None
        self._on_ctrl_key = None

    def set_key_handlers(
        self,
        *,
        on_up=None,
        on_down=None,
        on_left=None,
        on_right=None,
        on_enter=None,
        on_ctrl_enter=None,
        on_alt_digit=None,
        on_ctrl_key=None,
    ):
        self._on_up = on_up
        self._on_down = on_down
        self._on_enter = on_enter
        self._on_ctrl_enter = on_ctrl_enter
        self._on_alt_digit = on_alt_digit
        self._on_ctrl_key = on_ctrl_key

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
        modifiers = event.modifiers()
        is_repeat = bool(getattr(event, "isAutoRepeat", lambda: False)())

        # 1. Ctrl + Enter -> RUN action or Open URL
        if (modifiers & Qt.KeyboardModifier.ControlModifier) and k in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        ):
            if not is_repeat and self._on_ctrl_enter:
                self._on_ctrl_enter()
                event.accept()
                return

        # 2. Alt + 1..6 -> Quick paste from visible columns
        if (modifiers & Qt.KeyboardModifier.AltModifier) and not (
            modifiers & Qt.KeyboardModifier.ControlModifier
        ):
            if Qt.Key.Key_1 <= k <= Qt.Key.Key_6:
                digit = k - Qt.Key.Key_0
                if not is_repeat and self._on_alt_digit:
                    if self._on_alt_digit(digit):
                        event.accept()
                        return

        # 3. Ctrl + <key> (0-9, A-Z) -> Persistent clip shortcut
        if (modifiers & Qt.KeyboardModifier.ControlModifier) and not (
            modifiers & Qt.KeyboardModifier.AltModifier
        ):
            char = event.text()
            if not char:
                if Qt.Key.Key_0 <= k <= Qt.Key.Key_9:
                    char = chr(k)
                elif Qt.Key.Key_A <= k <= Qt.Key.Key_Z:
                    char = chr(k)

            if char and len(char) == 1 and char.isalnum() and not is_repeat:
                if self._on_ctrl_key and self._on_ctrl_key(char.upper()):
                    event.accept()
                    return

        # Standard navigation
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


class SingleKeyLineEdit(QLineEdit):
    """Single-key receiver box for shortcut binding. Accepts only 1 character (0-9, A-Z)."""

    keyChanged = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaxLength(1)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("""
            QLineEdit {
                background: #2b2b2b;
                color: #ffb830;
                border: 2px solid #555;
                border-radius: 6px;
                font-size: 14pt;
                font-weight: bold;
                padding: 4px;
            }
            QLineEdit:focus {
                border-color: #d18616;
            }
        """)

    def keyPressEvent(self, event):
        k = event.key()
        if k in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            self.clear()
            self.keyChanged.emit("")
            event.accept()
            return

        text = event.text().upper()
        if text and len(text) == 1 and text.isalnum():
            self.setText(text)
            self.keyChanged.emit(text)
            event.accept()
            return

        # Ignore non-alphanumeric or modifiers
        event.ignore()

    def insertFromMimeData(self, source):
        text = source.text().strip().upper()
        if len(text) == 1 and text.isalnum():
            self.setText(text)
            self.keyChanged.emit(text)


class ShortcutAssignDialog(QDialog):
    """Modal dialog for assigning a persistent Ctrl+<key> shortcut to a clip."""

    def __init__(self, clip_data: dict, storage, parent=None):
        super().__init__(parent)
        self.clip_data = clip_data
        self.storage = storage
        self.clip_id = clip_data.get("id")
        self.selected_key = ""
        self.conflict_clip_id = None

        self.setWindowTitle("Gán phím tắt nhanh")
        self.setFixedSize(420, 260)
        self.setStyleSheet("""
            QDialog { background: #1f1f1f; color: #e0e0e0; }
            QLabel { color: #e0e0e0; font-size: 10pt; }
            QPushButton {
                background: #333;
                color: #eee;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 6px 14px;
                font-size: 9pt;
            }
            QPushButton:hover { border-color: #d18616; color: white; }
            QPushButton#saveButton {
                background: #aa8030;
                border-color: #aa8030;
                color: white;
                font-weight: bold;
            }
            QPushButton#removeButton {
                background: #6b2828;
                border-color: #883333;
                color: #ffcccc;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Title / snippet
        lbl_title = QLabel("Gán phím tắt nhanh cho clip:")
        lbl_title.setStyleSheet("font-weight: bold; font-size: 11pt; color: #ffb830;")
        layout.addWidget(lbl_title)

        snippet = str(clip_data.get("content", ""))[:80].replace("\n", " ")
        if len(str(clip_data.get("content", ""))) > 80:
            snippet += "..."
        lbl_snippet = QLabel(f'"{snippet}"')
        lbl_snippet.setStyleSheet("color: #aaa; font-style: italic;")
        lbl_snippet.setWordWrap(True)
        layout.addWidget(lbl_snippet)

        # Shortcut input row
        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        lbl_ctrl = QLabel("Ctrl  +")
        lbl_ctrl.setStyleSheet("font-size: 13pt; font-weight: bold; color: #ffb830;")
        input_row.addWidget(lbl_ctrl)

        self.key_edit = SingleKeyLineEdit(self)
        self.key_edit.setFixedSize(50, 40)
        self.key_edit.keyChanged.connect(self._on_key_entered)
        input_row.addWidget(self.key_edit)
        input_row.addStretch()
        layout.addLayout(input_row)

        # Conflict notice label
        self.lbl_conflict = QLabel("")
        self.lbl_conflict.setStyleSheet("color: #ffcc66; font-size: 9pt;")
        self.lbl_conflict.setWordWrap(True)
        self.lbl_conflict.hide()
        layout.addWidget(self.lbl_conflict)

        layout.addStretch()

        # Action buttons
        btn_row = QHBoxLayout()
        self.btn_remove = QPushButton("Gỡ phím tắt", self)
        self.btn_remove.setObjectName("removeButton")
        self.btn_remove.clicked.connect(self._remove_shortcut)
        self.btn_remove.hide()
        btn_row.addWidget(self.btn_remove)

        btn_row.addStretch()

        btn_cancel = QPushButton("Hủy", self)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        self.btn_save = QPushButton("Lưu", self)
        self.btn_save.setObjectName("saveButton")
        self.btn_save.clicked.connect(self._save_shortcut)
        btn_row.addWidget(self.btn_save)

        layout.addLayout(btn_row)

        # Load existing shortcut if present
        current_sc = self.storage.get_shortcut_for_clip(self.clip_id)
        if current_sc:
            self.key_edit.setText(current_sc)
            self.selected_key = current_sc
            self.btn_remove.show()

    def _on_key_entered(self, key: str):
        self.selected_key = key.strip().upper()
        if not self.selected_key:
            self.lbl_conflict.hide()
            self.btn_save.setText("Lưu")
            self.conflict_clip_id = None
            return

        existing = self.storage.get_clip_by_shortcut(self.selected_key)
        if existing and existing.get("id") != self.clip_id:
            self.conflict_clip_id = existing.get("id")
            snip = str(existing.get("content", ""))[:40].replace("\n", " ")
            self.lbl_conflict.setText(
                f'⚠️ Phím Ctrl+{self.selected_key} đang gán cho clip #{self.conflict_clip_id}: "{snip}". Bạn có muốn chuyển sang clip này không?'
            )
            self.lbl_conflict.show()
            self.btn_save.setText("Chuyển gán")
        else:
            self.conflict_clip_id = None
            self.lbl_conflict.hide()
            self.btn_save.setText("Lưu")

    def _save_shortcut(self):
        if not self.selected_key:
            return
        ok, conflict_id = self.storage.assign_shortcut(
            self.clip_id, self.selected_key, force=True
        )
        if ok:
            self.accept()

    def _remove_shortcut(self):
        self.storage.remove_shortcut(self.clip_id)
        self.accept()


class ActionConfigDialog(QDialog):
    """
    Modal dialog for configuring clip actions:
    - Script file: Python / PowerShell (.py via `uv run`, must be full path)
    - Python inline code
    - External command (argv)
    - URL (open in browser)
    """

    def __init__(self, clip_data: dict, storage, parent=None):
        super().__init__(parent)
        self.clip_data = clip_data
        self.storage = storage
        self.clip_id = clip_data.get("id")

        self.setWindowTitle("Cấu hình Action cho Clip")
        self.setMinimumSize(520, 420)
        self.setStyleSheet("""
            QDialog { background: #1f1f1f; color: #e0e0e0; }
            QLabel { color: #e0e0e0; font-size: 9.5pt; }
            QLineEdit, QPlainTextEdit, QComboBox {
                background: #2b2b2b;
                color: #f0f0f0;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 5px 8px;
            }
            QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
                border-color: #d18616;
            }
            QPushButton {
                background: #333;
                color: #eee;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 6px 14px;
            }
            QPushButton:hover { border-color: #d18616; color: white; }
            QPushButton#saveButton {
                background: #aa8030;
                border-color: #aa8030;
                color: white;
                font-weight: bold;
            }
            QPushButton#removeButton {
                background: #6b2828;
                border-color: #883333;
                color: #ffcccc;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # Header
        lbl_head = QLabel("Cấu hình hành động thực thi (RUN hoặc Ctrl+Enter):")
        lbl_head.setStyleSheet("font-weight: bold; color: #ffb830; font-size: 11pt;")
        layout.addWidget(lbl_head)

        # Type selector
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Loại action:"))
        self.cb_type = QComboBox(self)
        self.cb_type.addItem("File Script (Python .py / .ps1 qua uv run)", "script_file")
        self.cb_type.addItem("Mã Python trực tiếp (Inline)", "python_inline")
        self.cb_type.addItem("Lệnh ngoài (Command + arguments)", "command")
        self.cb_type.addItem("Mở URL (Trình duyệt web)", "url")
        self.cb_type.currentIndexChanged.connect(self._on_type_changed)
        type_row.addWidget(self.cb_type, stretch=1)
        layout.addLayout(type_row)

        # Target input container
        self.lbl_target = QLabel("Đường dẫn script (Full path):")
        layout.addWidget(self.lbl_target)

        target_row = QHBoxLayout()
        self.edit_target = QLineEdit(self)
        target_row.addWidget(self.edit_target, stretch=1)
        self.btn_browse = QPushButton("Duyệt...", self)
        self.btn_browse.clicked.connect(self._browse_file)
        target_row.addWidget(self.btn_browse)
        layout.addLayout(target_row)

        # Multi-line inline code edit (shown only for python_inline)
        self.edit_inline = QPlainTextEdit(self)
        self.edit_inline.setPlaceholderText("Nhập mã Python tại đây...")
        self.edit_inline.hide()
        layout.addWidget(self.edit_inline, stretch=1)

        # Arguments row
        self.lbl_args = QLabel("Tham số (arguments, cách nhau bởi khoảng trắng):")
        layout.addWidget(self.lbl_args)
        self.edit_args = QLineEdit(self)
        layout.addWidget(self.edit_args)

        # Background checkbox
        self.chk_bg = QCheckBox("Chạy ngầm (Detached process, ví dụ: mở Chrome / CDP / tiến trình nền)", self)
        layout.addWidget(self.chk_bg)

        # Helper hint
        self.lbl_hint = QLabel("")
        self.lbl_hint.setStyleSheet("color: #888; font-size: 8.5pt;")
        layout.addWidget(self.lbl_hint)

        # Error label
        self.lbl_error = QLabel("")
        self.lbl_error.setStyleSheet("color: #ff6666; font-size: 9pt;")
        self.lbl_error.hide()
        layout.addWidget(self.lbl_error)

        layout.addStretch()

        # Buttons
        btn_row = QHBoxLayout()
        self.btn_remove = QPushButton("Gỡ Action", self)
        self.btn_remove.setObjectName("removeButton")
        self.btn_remove.clicked.connect(self._remove_action)
        self.btn_remove.hide()
        btn_row.addWidget(self.btn_remove)

        btn_row.addStretch()

        btn_cancel = QPushButton("Hủy", self)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_save = QPushButton("Lưu Action", self)
        btn_save.setObjectName("saveButton")
        btn_save.clicked.connect(self._save_action)
        btn_row.addWidget(btn_save)

        layout.addLayout(btn_row)

        # Load existing config
        self._load_existing()

    def _load_existing(self):
        action = self.storage.get_clip_action(self.clip_id)
        if not action:
            # Check if clip text is URL by default
            content = str(self.clip_data.get("content", "")).strip()
            if is_valid_url(content):
                idx = self.cb_type.findData("url")
                if idx >= 0:
                    self.cb_type.setCurrentIndex(idx)
                self.edit_target.setText(content)
            else:
                self._on_type_changed()
            return

        self.btn_remove.show()
        act_type = action.get("action_type", "script_file")
        idx = self.cb_type.findData(act_type)
        if idx >= 0:
            self.cb_type.setCurrentIndex(idx)

        target = action.get("target", "")
        if act_type == "python_inline":
            self.edit_inline.setPlainText(target)
        else:
            self.edit_target.setText(target)

        args = action.get("args") or []
        self.edit_args.setText(" ".join(args))
        self.chk_bg.setChecked(bool(action.get("is_background")))
        self._on_type_changed()

    def _on_type_changed(self):
        act_type = self.cb_type.currentData()
        self.lbl_error.hide()

        if act_type == "script_file":
            self.lbl_target.setText("Đường dẫn file script (Bắt buộc là Full path):")
            self.lbl_target.show()
            self.edit_target.show()
            self.btn_browse.show()
            self.edit_inline.hide()
            self.lbl_args.show()
            self.edit_args.show()
            self.chk_bg.show()
            self.lbl_hint.setText("Môi trường chạy là chính môi trường của script (chạy bằng: uv run <full_path>).")

        elif act_type == "python_inline":
            self.lbl_target.setText("Mã nguồn Python:")
            self.lbl_target.show()
            self.edit_target.hide()
            self.btn_browse.hide()
            self.edit_inline.show()
            self.lbl_args.hide()
            self.edit_args.hide()
            self.chk_bg.show()
            self.lbl_hint.setText("Chạy mã Python trực tiếp qua uv run python -c <code_string>.")

        elif act_type == "command":
            self.lbl_target.setText("Chương trình / Executable:")
            self.lbl_target.show()
            self.edit_target.show()
            self.btn_browse.show()
            self.edit_inline.hide()
            self.lbl_args.show()
            self.edit_args.show()
            self.chk_bg.show()
            self.lbl_hint.setText("Chạy trực tiếp qua danh sách đối số argv.")

        elif act_type == "url":
            self.lbl_target.setText("Địa chỉ Web URL:")
            self.lbl_target.show()
            self.edit_target.show()
            self.btn_browse.hide()
            self.edit_inline.hide()
            self.lbl_args.hide()
            self.edit_args.hide()
            self.chk_bg.hide()
            self.lbl_hint.setText("Mở liên kết trong trình duyệt mặc định khi bấm RUN hoặc Ctrl+Enter.")

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn file script hoặc chương trình",
            "",
            "Scripts (*.py *.ps1 *.bat *.cmd *.sh);;Tất cả files (*.*)",
        )
        if path:
            self.edit_target.setText(os.path.abspath(path))

    def _save_action(self):
        act_type = self.cb_type.currentData()
        target = self.edit_inline.toPlainText().strip() if act_type == "python_inline" else self.edit_target.text().strip()
        args_text = self.edit_args.text().strip()
        args = args_text.split() if args_text else []
        is_bg = self.chk_bg.isChecked()

        valid, err = ActionRunner.validate_action_config(act_type, target, args)
        if not valid:
            self.lbl_error.setText(f"Lỗi: {err}")
            self.lbl_error.show()
            return

        ok = self.storage.set_clip_action(
            self.clip_id, act_type, target, args=args, is_background=is_bg
        )
        if ok:
            self.accept()
        else:
            self.lbl_error.setText("Không thể lưu action vào cơ sở dữ liệu.")
            self.lbl_error.show()

    def _remove_action(self):
        self.storage.remove_clip_action(self.clip_id)
        self.accept()


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

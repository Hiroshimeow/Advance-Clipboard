import sys
import os
import hashlib
import ctypes
import atexit
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QLabel,
    QPushButton,
    QSizePolicy,
    QAbstractItemView,
    QFrame,
    QMessageBox,
    QMenu,
    QInputDialog,
    QGridLayout,
    QLineEdit,
)
from PyQt6.QtCore import (
    Qt,
    QTimer,
    pyqtSignal,
    QSize,
    QObject,
    QEvent,
    QPoint,
    QByteArray,
    QBuffer,
    QIODevice,
)
from PyQt6.QtGui import (
    QIcon,
    QCursor,
    QGuiApplication,
    QColor,
    QPalette,
    QFontMetrics,
    QAction,
    QFont,
    QPixmap,
    QImage,
)

# Sử dụng pynput cho cả Hotkey và Paste để tránh kẹt phím
from pynput import keyboard
from pynput.keyboard import Key, Controller as KeyboardController

# Import storage and backup modules
from storage import get_storage, ClipboardStorage
from backup_manager import (
    create_backup,
    find_valid_backup,
    import_legacy_json,
    BackupScheduler,
)

# --- Cấu hình ---
DATA_FILE = os.path.join(os.path.dirname(__file__), "data.json")
IMAGE_DIR = os.path.join(os.path.dirname(__file__), "images")

# Pagination config
PAGE_SIZE_HISTORY = 20
PAGE_SIZE_PINNED = 50
MAX_DISPLAY_CHARS = 300
THUMB_SIZE = QSize(80, 60)
UI_EDGE_MARGIN = 150  # Minimum distance from screen edges

# Ensure image directory exists
if not os.path.exists(IMAGE_DIR):
    os.makedirs(IMAGE_DIR)


# --- Smooth scrolling list widget ---
class SmoothListWidget(QListWidget):
    """QListWidget with reduced scroll speed for smoother experience."""

    def wheelEvent(self, event):
        # Reduce scroll speed by manipulating scrollbar directly
        # (avoids QWheelEvent constructor issues in PyQt6)
        delta = event.angleDelta().y()
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() - delta // 3)
        event.accept()


# --- Popup hiển thị thông tin số dòng ---
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


# --- Custom Search Input with triple-click to clear ---
class SearchLineEdit(QLineEdit):
    """QLineEdit with triple-click to clear functionality."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.click_count = 0
        self.click_timer = QTimer()
        self.click_timer.setSingleShot(True)
        self.click_timer.timeout.connect(self._reset_click_count)

    def mousePressEvent(self, event):
        self.click_count += 1
        self.click_timer.start(400)  # Reset after 400ms

        if self.click_count >= 3:
            self.clear()
            self.click_count = 0
            self.click_timer.stop()

        super().mousePressEvent(event)

    def _reset_click_count(self):
        self.click_count = 0


# --- Worker xử lý Hotkey (Logic từ auto-suggest) ---
class HotkeyWorker(QObject):
    activated = pyqtSignal()
    escape_pressed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.hotkeys = None
        self.listener = None

    def start(self):
        self.hotkeys = keyboard.GlobalHotKeys({"<ctrl>+<alt>+v": self.on_activate})
        self.listener = keyboard.Listener(on_press=self.on_press)
        self.hotkeys.start()
        self.listener.start()

    def on_activate(self):
        self.activated.emit()

    def on_press(self, key):
        if key == Key.esc:
            self.escape_pressed.emit()

    def stop(self):
        if self.hotkeys:
            self.hotkeys.stop()
        if self.listener:
            self.listener.stop()


# --- Group Header Widget (Collapsible) ---
class GroupHeaderWidget(QWidget):
    """Header for a group of clips - click to toggle expand/collapse."""

    def __init__(self, group_name, clip_count, parent_app=None):
        super().__init__()
        self.group_name = group_name
        self.clip_count = clip_count
        self.parent_app = parent_app
        self.is_expanded = False
        self.child_items = []  # Will hold QListWidgetItems for children

        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout()
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        # Expand indicator
        self.lbl_arrow = QLabel("▶")
        self.lbl_arrow.setStyleSheet("color: #aa8030; font-size: 12pt;")
        self.lbl_arrow.setFixedWidth(18)
        layout.addWidget(self.lbl_arrow)

        # Group name
        self.lbl_name = QLabel(f"📁 {group_name}")
        self.lbl_name.setStyleSheet(
            "color: #e0e0e0; font-size: 12pt; font-weight: bold;"
        )
        layout.addWidget(self.lbl_name, stretch=1)

        # Count badge
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
        """Set expansion state (called by parent to restore state)."""
        self.is_expanded = expanded
        self.lbl_arrow.setText("▼" if expanded else "▶")

    def mousePressEvent(self, event):
        # Click to toggle - state persists
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


# --- Widget cho từng dòng trong Clipboard ---
class ClipItemWidget(QWidget):
    def __init__(self, item_data, is_pinned=False, parent_list=None, is_grouped=False):
        super().__init__()
        # item_data now is a dict from SQLite: {id, type, content, hash, tag, group_name, ...}
        self.item_data = item_data
        self.clip_id = item_data.get("id")
        self.is_pinned = is_pinned
        self.parent_list = parent_list
        self.is_grouped = is_grouped  # If True, this is a child of a group
        self.line_count = (
            len(self.item_data["content"].splitlines())
            if self.item_data["type"] == "text"
            else 1
        )

        layout = QHBoxLayout()
        layout.setContentsMargins(
            5 if not is_grouped else 20, 5, 5, 5
        )  # Indent if grouped
        layout.setSpacing(8)

        # 1. Phần Content (Trái)
        self.content_container = QWidget()
        self.content_layout = QGridLayout(self.content_container)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)

        if self.item_data["type"] == "text":
            text = self.item_data["content"]
            display_text = (
                text[:MAX_DISPLAY_CHARS] + "..."
                if len(text) > MAX_DISPLAY_CHARS
                else text
            )
            self.lbl_content = QLabel(display_text)
            self.lbl_content.setStyleSheet("color: #e0e0e0; background: transparent;")
            font = QFont("Segoe UI", 11)  # Increased from 9 to 11
            self.lbl_content.setFont(font)
            self.lbl_content.setWordWrap(True)
            self.lbl_content.setAlignment(Qt.AlignmentFlag.AlignTop)
            fm = QFontMetrics(font)
            line_h = fm.lineSpacing()

            # Pinned items use 2 lines, history uses 3
            max_lines = 2 if self.is_pinned else 3
            text_h = (line_h * max_lines) + 12

            self.lbl_content.setFixedHeight(text_h)
            self.content_layout.addWidget(self.lbl_content, 0, 0)  # Row 0, Col 0
            self.display_height = text_h
        else:
            self.lbl_content = QLabel()
            self.lbl_content.setFixedSize(THUMB_SIZE)
            self.lbl_content.setScaledContents(True)
            self.lbl_content.setStyleSheet(
                "border: 1px solid #444; background-color: #000; border-radius: 4px;"
            )
            p = os.path.join(IMAGE_DIR, self.item_data["content"])
            if os.path.exists(p):
                pix = QPixmap(p)
                if not pix.isNull():
                    self.lbl_content.setPixmap(
                        pix.scaled(
                            THUMB_SIZE,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
            self.content_layout.addWidget(self.lbl_content, 0, 0)
            self.display_height = THUMB_SIZE.height()

        # 2. Cụm Badge và Tag (Overlay)
        tag_text = self.item_data.get("tag", "")
        group_name = self.item_data.get("group_name", "")
        badge_text = tag_text or (
            f"[{group_name}]" if group_name and not is_grouped else ""
        )

        self.lbl_tag = QLabel(badge_text)
        self.lbl_tag.setStyleSheet("""
            QLabel {
                color: #d18616; 
                font-size: 10pt; 
                font-style: italic;
                font-weight: normal;
                background: rgba(209, 134, 22, 0.2); 
                border-top-left-radius: 4px;
                padding: 1px 4px;
                margin: 0px;
            }
        """)
        self.lbl_tag.setVisible(bool(badge_text))
        # Căn lề dưới-phải để sát khung
        self.content_layout.addWidget(
            self.lbl_tag,
            0,
            0,
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight,
        )

        # Cột các nút badge (Số dòng, Lên, Xuống) nằm riêng ở Col 1
        self.btn_v_widget = QWidget()
        self.btn_v_layout = QVBoxLayout(self.btn_v_widget)
        self.btn_v_layout.setContentsMargins(5, 0, 0, 0)
        self.btn_v_layout.setSpacing(2)

        # Helper tạo nút badge nhỏ
        def create_badge_btn(text, tooltip, style, func, h=16):
            btn = QPushButton(text)
            btn.setToolTip(tooltip)
            btn.setFixedSize(22, h)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(style)
            btn.clicked.connect(func)
            return btn

        style_lines = "QPushButton { background: #d18616; color: white; border: none; border-radius: 3px; font-size: 9pt; font-weight: bold; } QPushButton:hover { background: #f0ad4e; }"
        style_arrow = "QPushButton { background: #333; color: #888; border: none; border-radius: 2px; font-size: 8pt; } QPushButton:hover { background: #444; color: #fff; }"

        self.btn_lines = create_badge_btn(
            str(self.line_count),
            "Số dòng (Click xem lời chào)",
            style_lines,
            self.show_line_info,
        )
        self.btn_up = create_badge_btn(
            "▲", "Di chuyển lên", style_arrow, self.on_up_clicked, 14
        )
        self.btn_down = create_badge_btn(
            "▼", "Di chuyển xuống", style_arrow, self.on_down_clicked, 14
        )

        self.btn_v_layout.addWidget(self.btn_lines)
        self.btn_v_layout.addWidget(self.btn_up)
        self.btn_v_layout.addWidget(self.btn_down)
        self.btn_v_layout.addStretch()

        self.content_layout.addWidget(
            self.btn_v_widget, 0, 1, Qt.AlignmentFlag.AlignTop
        )
        self.content_layout.setColumnStretch(0, 1)  # Nội dung chính co giãn
        self.content_layout.setColumnStretch(1, 0)  # Cột nút cố định

        layout.addWidget(self.content_container, stretch=1)

        # 3. Nút chức năng dọc (Cố định phải)
        self.btn_container = QWidget()
        self.btn_container.setFixedWidth(30)
        btn_layout = QVBoxLayout(self.btn_container)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(2)

        def create_act_btn(text, tooltip, color, hover, func):
            btn = QPushButton(text)
            btn.setToolTip(tooltip)
            btn.setFixedSize(28, 18)
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

        layout.addWidget(self.btn_container, stretch=0)
        self.setLayout(layout)

        min_widget_h = 35 if self.is_pinned else 60
        self.setFixedHeight(max(self.display_height, min_widget_h) + 10)

    def show_line_info(self):
        self.popup = LineInfoPopup(self.line_count)
        p = self.btn_lines.mapToGlobal(QPoint(0, 0))
        self.popup.show_at(QPoint(p.x() - self.popup.width() - 5, p.y()))

    def on_up_clicked(self):
        if self.parent_list and self.clip_id:
            self.parent_list.handle_move(self.clip_id, -1, self.is_pinned)

    def on_down_clicked(self):
        if self.parent_list and self.clip_id:
            self.parent_list.handle_move(self.clip_id, 1, self.is_pinned)

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
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #2d2d2d; color: #eee; border: 1px solid #444; }
            QMenu::item:selected { background-color: #d18616; color: white; }
        """)

        if self.is_pinned:
            # Group submenu
            group_menu = menu.addMenu("📁 Add to Group")

            # Get existing groups
            if self.parent_list:
                groups = self.parent_list.storage.get_groups()
                for g in groups:
                    act = group_menu.addAction(g)
                    act.setData(("group", g))

                if groups:
                    group_menu.addSeparator()

                new_group_act = group_menu.addAction("➕ New Group...")
                new_group_act.setData(("new_group", None))

                # Remove from group option
                current_group = self.item_data.get("group_name", "")
                if current_group:
                    remove_act = menu.addAction(f"❌ Remove from '{current_group}'")
                    remove_act.setData(("remove_group", None))

                menu.addSeparator()

            add_tag_act = menu.addAction("🏷️ Add Tag")
            add_tag_act.setData(("tag", None))

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

    def on_add_tag(self):
        current_tag = self.item_data.get("tag", "")
        tag, ok = QInputDialog.getText(
            self, "Add Tag", "Enter tag name:", text=current_tag
        )
        if ok and self.clip_id:
            if self.parent_list:
                self.parent_list.handle_add_tag(self.clip_id, tag)

    def on_set_group(self, group_name):
        if self.clip_id and self.parent_list:
            self.parent_list.handle_set_group(self.clip_id, group_name)

    def on_new_group(self):
        group_name, ok = QInputDialog.getText(self, "New Group", "Enter group name:")
        if ok and group_name.strip() and self.clip_id:
            if self.parent_list:
                self.parent_list.handle_set_group(self.clip_id, group_name.strip())


class ClientApp(QWidget):
    def __init__(self):
        super().__init__()
        # SQLite storage - single source of truth
        self.storage = get_storage()

        # Pagination state
        self.history_offset = 0
        self.pinned_offset = 0
        self.history_has_more = True
        self.pinned_has_more = True

        # Group expansion state
        self.expanded_groups = set()
        self.group_headers = {}  # group_name -> QListWidgetItem

        # UI state
        self.ignore_clipboard_change = False
        self.kb_controller = KeyboardController()
        self.is_ui_dirty = True
        self.input_locked = False
        self.last_active_window_handle = None
        self.current_search_query = ""
        self._is_refreshing = False  # Guard for changeEvent during refresh

        # Init UI
        self.initUI()

        # Load data with disaster recovery
        self._init_data()

        # Clipboard monitoring
        self.clipboard = QApplication.clipboard()
        self.clipboard.dataChanged.connect(self.on_clipboard_change)

        # Hotkey handling
        self.hotkey_worker = HotkeyWorker()
        self.hotkey_worker.activated.connect(self.toggle_visibility)
        self.hotkey_worker.escape_pressed.connect(self.hide_if_visible)
        self.hotkey_worker.start()

        # Backup scheduling (30s debounce)
        self.backup_scheduler = BackupScheduler(self._perform_backup)
        self.storage.set_backup_callback(self.backup_scheduler.schedule)

        # Register cleanup on exit
        atexit.register(self._cleanup_on_exit)

    def _init_data(self):
        """Initialize data with disaster recovery logic."""
        # Check if DB is valid
        if self.storage.is_db_valid() and self.storage.get_clip_count() > 0:
            # DB is good, use it
            self.refresh_lists()
            return

        # DB is empty or corrupt - try to recover
        # First, try to find valid backup
        backup_path, clips = find_valid_backup()
        if clips:
            self.storage.import_clips(clips)
            self.refresh_lists()
            return

        # No valid backup - try legacy JSON
        if os.path.exists(DATA_FILE):
            clips = import_legacy_json(DATA_FILE)
            if clips:
                self.storage.import_clips(clips)
                self.refresh_lists()
                return

        # No data to recover - start fresh
        self.refresh_lists()

    def _perform_backup(self):
        """Create backup from current SQLite data."""
        clips = self.storage.get_all_clips()
        create_backup(clips)
        self.storage.clear_backup_flag()

    def _cleanup_on_exit(self):
        """Cleanup when app exits."""
        # Force immediate backup if needed
        if self.storage.need_backup:
            self.backup_scheduler.force_now()

    def initUI(self):
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(750, 480)
        self.setStyleSheet("""
            QWidget { background-color: #1e1e1e; color: #f0f0f0; font-family: 'Segoe UI', sans-serif; border-radius: 8px; }
            QLabel { font-weight: bold; color: #888; margin: 5px 0; }
            QListWidget { background-color: #252526; border: 1px solid #333; border-radius: 4px; outline: none; }
            QListWidget::item { border-bottom: 1px solid #303030; margin: 0px; }
            QListWidget::item:selected { background-color: #37373d; border: 1px solid #007acc; }
            QScrollBar:vertical { border: none; background: #252526; width: 10px; }
            QScrollBar::handle:vertical { background: #424242; min-height: 20px; border-radius: 5px; }
            QLineEdit { 
                background-color: #2d2d2d; 
                color: #e0e0e0; 
                border: 1px solid #3d3d3d; 
                border-radius: 4px; 
                padding: 4px 8px; 
                font-size: 10pt; 
            }
            QLineEdit:focus { border: 1px solid #aa8030; }
        """)
        outer_layout = QVBoxLayout()
        outer_layout.setContentsMargins(10, 10, 10, 10)
        outer_layout.setSpacing(6)

        # --- Search bar row with Clear buttons on each side ---
        search_row = QHBoxLayout()
        search_row.setSpacing(5)

        btn_clear_h = QPushButton("Clear")
        btn_clear_h.setFixedSize(40, 20)
        btn_clear_h.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clear_h.setStyleSheet(
            "QPushButton { background: #333; border: none; border-radius: 3px; color: #888; font-size: 7pt; } QPushButton:hover { background: #444; color: #eee; }"
        )
        btn_clear_h.setToolTip("Clear History")
        btn_clear_h.clicked.connect(lambda: self.clear_all_list(False))
        search_row.addWidget(btn_clear_h)

        self.search_input = SearchLineEdit()  # Custom with triple-click support
        self.search_input.setPlaceholderText("\U0001f50d Search...")
        self.search_input.setFixedHeight(28)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        search_row.addWidget(self.search_input, stretch=1)

        btn_clear_p = QPushButton("Clear")
        btn_clear_p.setFixedSize(40, 20)
        btn_clear_p.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clear_p.setStyleSheet(
            "QPushButton { background: #333; border: none; border-radius: 3px; color: #888; font-size: 7pt; } QPushButton:hover { background: #444; color: #eee; }"
        )
        btn_clear_p.setToolTip("Clear Pinned")
        btn_clear_p.clicked.connect(lambda: self.clear_all_list(True))
        search_row.addWidget(btn_clear_p)

        # Debounce timer for search
        self.search_debounce_timer = QTimer()
        self.search_debounce_timer.setSingleShot(True)
        self.search_debounce_timer.timeout.connect(self._do_search)

        outer_layout.addLayout(search_row)

        # --- Two-column area ---
        columns_layout = QHBoxLayout()
        columns_layout.setSpacing(10)

        # HISTORY column
        col_h = QVBoxLayout()
        self.list_history = SmoothListWidget()
        self.list_history.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.list_history.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.list_history.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.list_history.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_history.itemClicked.connect(self.on_item_clicked)
        col_h.addWidget(self.list_history)

        # PINNED column
        col_p = QVBoxLayout()
        self.list_pinned = SmoothListWidget()
        self.list_pinned.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.list_pinned.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.list_pinned.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.list_pinned.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_pinned.itemClicked.connect(self.on_item_clicked)
        col_p.addWidget(self.list_pinned)

        # Connect scroll for pagination
        self.list_history.verticalScrollBar().valueChanged.connect(
            self._on_history_scroll
        )
        self.list_pinned.verticalScrollBar().valueChanged.connect(
            self._on_pinned_scroll
        )

        columns_layout.addLayout(col_h, 1)
        columns_layout.addLayout(col_p, 1)
        outer_layout.addLayout(columns_layout)
        self.setLayout(outer_layout)

    def _on_search_text_changed(self, text):
        """Debounced search - waits 300ms after typing stops."""
        self.current_search_query = text.strip()
        self.search_debounce_timer.start(300)  # 300ms debounce

    def _do_search(self):
        """Execute the actual search after debounce — filters both lists."""
        self._is_refreshing = True
        try:
            self.setUpdatesEnabled(False)

            # Refresh history with search filter
            self.list_history.blockSignals(True)
            self.list_history.clear()
            self.history_offset = 0
            self.history_has_more = True
            if self.current_search_query:
                history_clips = self.storage.search_history(self.current_search_query)
                self._append_items(history_clips, self.list_history, False)
                self.history_has_more = False  # search returns all matches
            else:
                history_clips = self.storage.get_history(limit=PAGE_SIZE_HISTORY, offset=0)
                if len(history_clips) < PAGE_SIZE_HISTORY:
                    self.history_has_more = False
                self._append_items(history_clips, self.list_history, False)
                self.history_offset = len(history_clips)
            self.list_history.blockSignals(False)

            # Refresh pinned with search filter
            self.list_pinned.blockSignals(True)
            self.refresh_pinned_list()
            self.list_pinned.blockSignals(False)

            self.setUpdatesEnabled(True)
        finally:
            self._is_refreshing = False

    def on_search_changed(self, text):
        """Handle search input change (legacy, not used with debounce)."""
        self.current_search_query = text.strip()
        self.refresh_pinned_list()

    def _on_history_scroll(self, value):
        """Load more history items when scrolling to bottom."""
        if not self.history_has_more:
            return
        scrollbar = self.list_history.verticalScrollBar()
        if value >= scrollbar.maximum() - 50:  # Near bottom
            self._load_more_history()

    def _on_pinned_scroll(self, value):
        """Load more pinned items when scrolling to bottom."""
        if not self.pinned_has_more:
            return
        scrollbar = self.list_pinned.verticalScrollBar()
        if value >= scrollbar.maximum() - 50:  # Near bottom
            self._load_more_pinned()

    def _load_more_history(self):
        """Load next page of history items."""
        clips = self.storage.get_history(
            limit=PAGE_SIZE_HISTORY, offset=self.history_offset
        )
        if len(clips) < PAGE_SIZE_HISTORY:
            self.history_has_more = False
        if clips:
            self._append_items(clips, self.list_history, False)
            self.history_offset += len(clips)

    def _load_more_pinned(self):
        """Load next page of pinned items."""
        clips = self.storage.get_pinned(
            limit=PAGE_SIZE_PINNED, offset=self.pinned_offset
        )
        if len(clips) < PAGE_SIZE_PINNED:
            self.pinned_has_more = False
        if clips:
            # Append ungrouped only
            ungrouped = [c for c in clips if not c.get("group_name")]
            self._append_items(ungrouped, self.list_pinned, True)
            self.pinned_offset += len(clips)

    def _append_items(self, clips, widget, is_pinned, is_grouped=False):
        """Append items to list without clearing."""
        width = widget.viewport().width()
        if width <= 10:
            width = (self.width() // 2) - 25
        for clip in clips:
            item = QListWidgetItem()
            ui = ClipItemWidget(clip, is_pinned, self, is_grouped)
            item.setSizeHint(QSize(width, ui.height()))
            widget.addItem(item)
            widget.setItemWidget(item, ui)
            item.setData(Qt.ItemDataRole.UserRole, clip)

    def expand_group(self, group_name):
        """Expand a group to show its children."""
        if group_name in self.expanded_groups:
            return

        self.expanded_groups.add(group_name)

        # Find the group header item
        if group_name not in self.group_headers:
            return

        header_item = self.group_headers[group_name]
        header_row = self.list_pinned.row(header_item)

        # Get clips in this group
        clips = self.storage.get_clips_by_group(group_name)

        # Insert after header
        width = self.list_pinned.viewport().width()
        if width <= 10:
            width = (self.width() // 2) - 25

        for i, clip in enumerate(clips):
            item = QListWidgetItem()
            ui = ClipItemWidget(clip, True, self, is_grouped=True)
            item.setSizeHint(QSize(width, ui.height()))
            item.setData(Qt.ItemDataRole.UserRole, clip)
            item.setData(
                Qt.ItemDataRole.UserRole + 1, group_name
            )  # Mark as group child
            self.list_pinned.insertItem(header_row + 1 + i, item)
            self.list_pinned.setItemWidget(item, ui)

    def collapse_group(self, group_name):
        """Collapse a group to hide its children."""
        if group_name not in self.expanded_groups:
            return

        self.expanded_groups.discard(group_name)

        # Remove all items that belong to this group
        items_to_remove = []
        for i in range(self.list_pinned.count()):
            item = self.list_pinned.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole + 1) == group_name:
                items_to_remove.append(i)

        # Remove in reverse order to maintain indices
        for i in reversed(items_to_remove):
            self.list_pinned.takeItem(i)

    def changeEvent(self, e):
        if (
            e.type() == QEvent.Type.ActivationChange
            and not self.isActiveWindow()
            and not self._is_refreshing
        ):
            self.hide()
        super().changeEvent(e)

    def hide_if_visible(self):
        if self.isVisible():
            self.hide()

    def toggle_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.show_at_cursor()

    def show_at_cursor(self):
        self.input_locked = True
        QTimer.singleShot(150, lambda: setattr(self, "input_locked", False))
        if sys.platform == "win32":
            try:
                self.last_active_window_handle = (
                    ctypes.windll.user32.GetForegroundWindow()
                )
            except:
                pass
        if self.is_ui_dirty:
            self.refresh_lists()
            self.is_ui_dirty = False
        cp = QCursor.pos()
        w, h = self.width(), self.height()
        sc = QGuiApplication.screenAt(cp) or QGuiApplication.primaryScreen()
        geo = sc.geometry()
        m = UI_EDGE_MARGIN
        x = max(geo.x() + m, min(cp.x() - w // 3, geo.x() + geo.width() - w - m))
        y = max(geo.y() + m, min(cp.y() - h // 4, geo.y() + geo.height() - h - m))
        self.move(x, y)
        self.show()
        if sys.platform == "win32":
            try:
                our_hwnd = int(self.winId())
                user32 = ctypes.windll.user32
                f_hwnd = user32.GetForegroundWindow()
                if f_hwnd != our_hwnd:
                    ft, at = (
                        user32.GetWindowThreadProcessId(f_hwnd, None),
                        user32.GetWindowThreadProcessId(our_hwnd, None),
                    )
                    user32.AttachThreadInput(ft, at, True)
                    user32.SetForegroundWindow(our_hwnd)
                    user32.SetFocus(our_hwnd)
                    user32.AttachThreadInput(ft, at, False)
            except:
                pass
        self.raise_()
        self.activateWindow()
        self.list_history.setFocus()
        if self.list_history.count() > 0:
            self.list_history.setCurrentRow(0)

    def on_item_clicked(self, item):
        if self.input_locked:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if data and isinstance(data, dict) and "content" in data:
            self.handle_paste(data)

    def handle_paste(self, data):
        self.ignore_clipboard_change = True

        # Clear search after paste
        self.search_input.clear()
        self.current_search_query = ""

        if data["type"] == "text":
            self.clipboard.setText(data["content"])
        else:
            p = os.path.join(IMAGE_DIR, data["content"])
            if os.path.exists(p):
                self.clipboard.setPixmap(QPixmap(p))
        QApplication.processEvents()
        self.hide()

        # Reset scroll to top so next open starts at the beginning
        self.list_history.verticalScrollBar().setValue(0)
        self.list_pinned.verticalScrollBar().setValue(0)
        if sys.platform == "win32" and self.last_active_window_handle:
            try:
                ctypes.windll.user32.SetForegroundWindow(self.last_active_window_handle)
            except:
                pass
        QTimer.singleShot(150, self._perform_keyboard_paste)

    def _perform_keyboard_paste(self):
        try:
            self.kb_controller.release(Key.ctrl)
            self.kb_controller.release(Key.alt)
            with self.kb_controller.pressed(Key.ctrl):
                self.kb_controller.press("v")
                self.kb_controller.release("v")
        except Exception:
            pass
        finally:
            QTimer.singleShot(
                500, lambda: setattr(self, "ignore_clipboard_change", False)
            )

    def handle_move(self, clip_id, direction, is_pinned):
        """Move clip up/down."""
        self.storage.move_clip(clip_id, direction, is_pinned)
        self.refresh_lists()

    def clear_all_list(self, is_pinned):
        if is_pinned:
            count = self.storage.get_pinned_count()
            if count == 0:
                return
            if (
                QMessageBox.question(
                    self,
                    "Xác nhận",
                    "Xóa tất cả mục đã GHIM?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
            self.storage.clear_pinned()
        else:
            self.storage.clear_history()
        self.refresh_lists()

    def on_clipboard_change(self):
        if self.ignore_clipboard_change:
            return
        mime = self.clipboard.mimeData()
        clip_type = None
        content = None

        if mime.hasImage():
            img = QImage(mime.imageData())
            if not img.isNull():
                clip_type = "image"
                content = self.save_image_if_new(img)
        elif mime.hasText():
            t = mime.text()
            if t and t.strip():
                clip_type = "text"
                content = t

        if not clip_type or not content:
            return

        # Add to SQLite (handles dedup internally)
        clip_id, is_new = self.storage.add_clip(clip_type, content)

        # Update UI
        if self.isVisible():
            self.refresh_lists()
        else:
            self.is_ui_dirty = True

    def save_image_if_new(self, img):
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        ih = hashlib.md5(ba.data()).hexdigest()
        fn = f"{ih}.png"
        fp = os.path.join(IMAGE_DIR, fn)
        if not os.path.exists(fp):
            img.save(fp, "PNG")
        return fn

    def refresh_lists(self):
        """Refresh both lists from SQLite with pagination reset."""
        h_s, p_s = (
            self.list_history.verticalScrollBar().value(),
            self.list_pinned.verticalScrollBar().value(),
        )
        self._is_refreshing = True
        try:
            self.setUpdatesEnabled(False)

            # Reset pagination
            self.history_offset = 0
            self.pinned_offset = 0
            self.history_has_more = True
            self.pinned_has_more = True
            self.expanded_groups.clear()
            self.group_headers.clear()

            # Clear lists
            self.list_history.clear()
            self.list_pinned.clear()

            # Load initial page of history
            history_clips = self.storage.get_history(limit=PAGE_SIZE_HISTORY, offset=0)
            if len(history_clips) < PAGE_SIZE_HISTORY:
                self.history_has_more = False
            self._append_items(history_clips, self.list_history, False)
            self.history_offset = len(history_clips)

            # Refresh pinned with grouping
            self.refresh_pinned_list()

            self.list_history.verticalScrollBar().setValue(h_s)
            self.list_pinned.verticalScrollBar().setValue(p_s)
            self.setUpdatesEnabled(True)
        finally:
            self._is_refreshing = False

    def refresh_pinned_list(self):
        """Refresh pinned list with groups and search."""
        p_s = self.list_pinned.verticalScrollBar().value()

        # Save currently expanded groups to restore later
        previously_expanded = self.expanded_groups.copy()

        self.list_pinned.clear()
        self.group_headers.clear()

        # Reset pagination
        self.pinned_offset = 0
        self.pinned_has_more = True

        width = self.list_pinned.viewport().width()
        if width <= 10:
            width = (self.width() // 2) - 25

        if self.current_search_query:
            # Search mode - show flat results
            clips = self.storage.search_pinned(self.current_search_query)
            self._append_items(clips, self.list_pinned, True)
        else:
            # Normal mode - show groups + ungrouped
            groups = self.storage.get_groups()

            # Add group headers
            for group_name in groups:
                clips_in_group = self.storage.get_clips_by_group(group_name)
                if clips_in_group:
                    item = QListWidgetItem()
                    header = GroupHeaderWidget(group_name, len(clips_in_group), self)

                    # Restore expanded state
                    if group_name in previously_expanded:
                        header.set_expanded(True)

                    item.setSizeHint(QSize(width, 45))
                    self.list_pinned.addItem(item)
                    self.list_pinned.setItemWidget(item, header)
                    self.group_headers[group_name] = item

                    # If was expanded, add children immediately
                    if group_name in previously_expanded:
                        self.expanded_groups.add(group_name)
                        for clip in clips_in_group:
                            child_item = QListWidgetItem()
                            ui = ClipItemWidget(clip, True, self, is_grouped=True)
                            child_item.setSizeHint(QSize(width, ui.height()))
                            child_item.setData(Qt.ItemDataRole.UserRole, clip)
                            child_item.setData(Qt.ItemDataRole.UserRole + 1, group_name)
                            self.list_pinned.addItem(child_item)
                            self.list_pinned.setItemWidget(child_item, ui)

            # Add ungrouped clips
            ungrouped = self.storage.get_ungrouped_pinned(
                limit=PAGE_SIZE_PINNED, offset=0
            )
            if len(ungrouped) < PAGE_SIZE_PINNED:
                self.pinned_has_more = False
            self._append_items(ungrouped, self.list_pinned, True)
            self.pinned_offset = len(ungrouped)

        self.list_pinned.verticalScrollBar().setValue(p_s)

    def handle_copy_only(self, data):
        self.ignore_clipboard_change = True
        if data["type"] == "text":
            self.clipboard.setText(data["content"])
        else:
            p = os.path.join(IMAGE_DIR, data["content"])
            if os.path.exists(p):
                self.clipboard.setPixmap(QPixmap(p))
        QTimer.singleShot(800, lambda: setattr(self, "ignore_clipboard_change", False))

    def handle_star(self, clip_id, should_pin):
        """Pin or unpin a clip."""
        if should_pin:
            self.storage.pin_clip(clip_id)
        else:
            self.storage.unpin_clip(clip_id)
        self.refresh_lists()

    def handle_add_tag(self, clip_id, tag):
        """Update tag for a clip."""
        self.storage.update_tag(clip_id, tag)
        self.refresh_lists()

    def handle_set_group(self, clip_id, group_name):
        """Set group for a clip."""
        self.storage.update_group(clip_id, group_name)
        self.refresh_lists()

    def handle_delete(self, clip_id):
        """Delete a clip."""
        self.storage.delete_clip(clip_id)
        self.refresh_lists()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.hide()
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            fw = QApplication.focusWidget()
            if isinstance(fw, QListWidget):
                ci = fw.currentItem()
                if ci:
                    data = ci.data(Qt.ItemDataRole.UserRole)
                    if data and isinstance(data, dict) and "content" in data:
                        self.handle_paste(data)
        super().keyPressEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
    app.setPalette(palette)
    window = ClientApp()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

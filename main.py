# Disable HF Hub warnings globally before any imports
import os

os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["HF_HUB_OFFLINE"] = "0"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"

# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "PyQt6",
#     "PyQt6-WebEngine",
#     "sentence-transformers",
#     "numpy",
# ]
# ///
import sys
import os
import hashlib
import ctypes
import ctypes.wintypes
import atexit
import faulthandler
import logging
import threading
import time
import traceback
import argparse
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

# Pure Win32 clipboard monitor & hotkey (no pynput, no keyboard hooks)
from win32_monitor import Win32ClipboardMonitor, VK_CONTROL, VK_MENU, simulate_paste

# Import storage and backup modules
from storage import get_storage, ClipboardStorage
from backup_manager import (
    create_backup,
    find_valid_backup,
    import_legacy_json,
    BackupScheduler,
)

# Neural Memory modules — QtWebEngineWidgets MUST be imported before QApplication
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401 (must come first)
    from neural.engine import NeuralEngine
    from neural.ui import SidecarWindow

    HAS_NEURAL_SUPPORT = True
except Exception as _neural_err:
    HAS_NEURAL_SUPPORT = False
    print(f"[Neural] Import failed: {_neural_err}", file=sys.stderr)

    class QWebEngineView:
        pass

    class NeuralEngine:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    class SidecarWindow:
        class MockBridge:
            node_clicked = type("MockSignal", (), {"connect": lambda self, x: None})()

        class MockSearchBar:
            textChanged = type("MockSignal", (), {"connect": lambda self, x: None})()

        def __init__(self, *args, **kwargs):
            self.bridge = self.MockBridge()
            self.search_bar = self.MockSearchBar()

        def show(self):
            pass

        def hide(self):
            pass

        def close(self):
            pass

        def move(self, *args):
            pass

        def update_data(self, *args):
            pass

        def focus_node(self, *args):
            pass


# --- Cấu hình ---
DATA_FILE = os.path.join(os.path.dirname(__file__), "data.json")
IMAGE_DIR = os.path.join(os.path.dirname(__file__), "images")
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
DEBUG_LOG_FILE = os.path.join(LOG_DIR, "Advance Clipboard.debug.log")
FAULT_LOG_FILE = os.path.join(LOG_DIR, "Advance Clipboard.fault.log")

# Pagination config
PAGE_SIZE_HISTORY = 20
PAGE_SIZE_PINNED = 50
MAX_DISPLAY_CHARS = 300
THUMB_SIZE = QSize(80, 60)
UI_EDGE_MARGIN = 150  # Minimum distance from screen edges

# Ensure image directory exists
if not os.path.exists(IMAGE_DIR):
    os.makedirs(IMAGE_DIR)
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)


logger = logging.getLogger("advance_clipboard")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    file_handler = logging.FileHandler(DEBUG_LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    logger.addHandler(file_handler)
    logger.propagate = False


_fault_log_handle = open(FAULT_LOG_FILE, "a", encoding="utf-8")
faulthandler.enable(_fault_log_handle, all_threads=True)


def _log_unhandled_exception(exc_type, exc_value, exc_tb):
    logger.critical(
        "Unhandled exception:\n%s",
        "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
    )
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def _log_thread_exception(args):
    logger.critical(
        "Unhandled thread exception in %s:\n%s",
        getattr(args.thread, "name", "unknown-thread"),
        "".join(
            traceback.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback
            )
        ),
    )


sys.excepthook = _log_unhandled_exception
threading.excepthook = _log_thread_exception


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

        # Keyboard navigation handlers (set by ClientApp)
        self._on_up = None
        self._on_down = None
        self._on_left = None
        self._on_right = None
        self._on_enter = None

    def set_key_handlers(
        self, *, on_up=None, on_down=None, on_left=None, on_right=None, on_enter=None
    ):
        """Register callbacks for navigation keys.

        Normal text entry/backspace should still be handled by QLineEdit.
        """
        self._on_up = on_up
        self._on_down = on_down
        self._on_left = on_left
        self._on_right = on_right
        self._on_enter = on_enter

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

    def keyPressEvent(self, event):
        k = event.key()

        # Forward only navigation/activation keys to handlers.
        # Everything else should behave like a normal QLineEdit.
        # NOTE: Left/Right are intentionally treated as "switch column" (history/pinned)
        # while this field is focused. This is a tradeoff: caret movement within the
        # search text is sacrificed in favor of fast two-column navigation, while
        # keeping keyboard ownership in the search field.
        if k == Qt.Key.Key_Up and self._on_up:
            self._on_up()
            event.accept()
            return
        if k == Qt.Key.Key_Down and self._on_down:
            self._on_down()
            event.accept()
            return
        if k == Qt.Key.Key_Left and self._on_left:
            self._on_left()
            event.accept()
            return
        if k == Qt.Key.Key_Right and self._on_right:
            self._on_right()
            event.accept()
            return
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._on_enter:
            self._on_enter()
            event.accept()
            return

        super().keyPressEvent(event)


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

        # 2. Tag row (below content, not overlapping)
        tag_text = self.item_data.get("tag", "")
        group_name = self.item_data.get("group_name", "")
        badge_text = tag_text or (
            f"[{group_name}]" if group_name and not is_grouped else ""
        )

        self.has_tag = bool(badge_text)
        self.tag_height = 0
        if self.has_tag:
            self.lbl_tag = QLabel(badge_text)
            self.lbl_tag.setStyleSheet("""
                QLabel {
                    color: #d18616; 
                    font-size: 8pt; 
                    font-style: italic;
                    font-weight: normal;
                    background: rgba(209, 134, 22, 0.15); 
                    border-radius: 3px;
                    padding: 1px 6px;
                    margin: 0px;
                }
            """)
            tag_font = QFont("Segoe UI", 8)
            tag_fm = QFontMetrics(tag_font)
            self.tag_height = tag_fm.height() + 6  # padding
            self.lbl_tag.setFixedHeight(self.tag_height)
            self.lbl_tag.setMaximumWidth(200)
            self.lbl_tag.setSizePolicy(
                QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
            )
            self.content_layout.addWidget(
                self.lbl_tag,
                1,
                0,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
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

        # Span both rows (content + tag) if tag exists
        row_span = 2 if self.has_tag else 1
        self.content_layout.addWidget(
            self.btn_v_widget, 0, 1, row_span, 1, Qt.AlignmentFlag.AlignTop
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
        total_h = self.display_height + self.tag_height
        self.setFixedHeight(max(total_h, min_widget_h) + 10)

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
    def __init__(self, *, enable_monitor: bool = True, init_data: bool = True):
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
        self.pending_clipboard_guard = None
        self.is_ui_dirty = True
        self.input_locked = False
        self.last_active_window_handle = None
        self.current_search_query = ""
        self._is_refreshing = False  # Guard for changeEvent during refresh
        self._paste_in_progress = False

        # Keyboard navigation state
        self.active_side = "history"  # default to history when UI opens

        # Neural Memory state
        self.neural_enabled = False
        self._galaxy_loaded = False  # True after first full galaxy load into sidecar
        self._clips_since_last_rewarm = 0  # Counter for batched index rebuild
        self._rewarm_threshold = 10  # Rebuild search index every N new clips

        # Prevent PyTorch from taking over all threads to avoid lagging the host UI and system
        os.environ["OMP_NUM_THREADS"] = "1"
        os.environ["MKL_NUM_THREADS"] = "1"
        os.environ["OPENBLAS_NUM_THREADS"] = "1"
        os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
        os.environ["NUMEXPR_NUM_THREADS"] = "1"

        self.neural_engine = NeuralEngine(
            self.storage,
            os.path.join(os.path.dirname(__file__), "neural", "config.json"),
        )
        if HAS_NEURAL_SUPPORT:
            # Delay the start of the neural engine slightly to let UI initialize first
            QTimer.singleShot(2000, self.neural_engine.start)
            # NO parent — sidecar manages its own lifecycle to avoid hide-loop
            self.sidecar = SidecarWindow(self.storage, main_window=self)
        else:
            self.sidecar = None

        # Connect neural signals
        if self.sidecar:
            self.sidecar.bridge.node_clicked.connect(self._on_node_clicked)
            self.sidecar.search_bar.textChanged.connect(self._on_sidecar_search_changed)

        # Init UI
        self.initUI()

        self.neural_status_timer = QTimer(self)
        self.neural_status_timer.timeout.connect(self._refresh_neural_status)
        self.neural_status_timer.start(500)

        # Load data with disaster recovery
        if init_data:
            self._init_data()

        # Trigger daily RAG index rebuild in background (non-blocking)
        # If already rebuilt today, skips instantly. Otherwise builds in ~10s background.
        # Search works immediately with lexical-only fallback while index builds.
        self.storage.trigger_daily_rebuild()

        # Qt clipboard object — used to READ clipboard content only
        self.clipboard = QApplication.clipboard()

        # Win32 Clipboard Monitor — dedicated hidden window that NEVER gets
        # destroyed. This replaces both Qt's dataChanged signal and the old
        # AddClipboardFormatListener on self.winId().
        # Also handles Ctrl+Alt+V hotkey via RegisterHotKey (no keyboard hooks).
        self.win32_monitor = None
        if enable_monitor:
            self.win32_monitor = Win32ClipboardMonitor()
            self.win32_monitor.clipboard_changed.connect(
                self.on_clipboard_change_delayed, Qt.ConnectionType.QueuedConnection
            )
            self.win32_monitor.hotkey_toggle.connect(
                self.toggle_visibility, Qt.ConnectionType.QueuedConnection
            )
            self.win32_monitor.start()

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
        logger.info("cleanup_on_exit storage_need_backup=%s", self.storage.need_backup)
        # Stop Win32 monitor thread
        if getattr(self, "win32_monitor", None):
            monitor = self.win32_monitor
            if monitor is not None:
                monitor.stop()
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

        btn_clear_h = QPushButton("✕")
        btn_clear_h.setFixedSize(24, 20)
        btn_clear_h.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clear_h.setStyleSheet(
            "QPushButton { background: #333; border: none; border-radius: 3px; color: #888; font-size: 9pt; } QPushButton:hover { background: #444; color: #eee; }"
        )
        btn_clear_h.setToolTip("Clear search text")
        btn_clear_h.clicked.connect(lambda: self.search_input.clear())
        search_row.addWidget(btn_clear_h)

        self.search_input = SearchLineEdit()  # Custom with triple-click support
        self.search_input.setPlaceholderText("\U0001f50d Search...")
        self.search_input.setFixedHeight(28)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        self.search_input.set_key_handlers(
            on_up=self._nav_up,
            on_down=self._nav_down,
            on_left=self._nav_left,
            on_right=self._nav_right,
            on_enter=self._activate_current,
        )
        search_row.addWidget(self.search_input, stretch=1)

        # Neural Toggle
        self.btn_neural = QPushButton("Neural")
        self.btn_neural.setCheckable(True)
        self.btn_neural.setFixedSize(60, 24)
        self.btn_neural.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_neural.setStyleSheet("""
            QPushButton { 
                background: #001100; border: 1px solid #00ff41; 
                color: #00ff41; font-size: 8pt; font-weight: bold; border-radius: 3px;
            }
            QPushButton:checked { background: #00ff41; color: #000; }
            QPushButton:hover { background: #003300; }
            QPushButton:disabled { background: #222; border-color: #555; color: #555; }
        """)
        self.btn_neural.setCheckable(False)
        self.btn_neural.clicked.connect(self._show_neural_floating)
        if not HAS_NEURAL_SUPPORT:
            self.btn_neural.setEnabled(False)
            self.btn_neural.setToolTip(
                "Neural support requires PyQt6-WebEngine and sentence-transformers"
            )
        search_row.addWidget(self.btn_neural)

        self.chk_map = QPushButton("Map OFF")
        self.chk_map.setCheckable(True)
        self.chk_map.setFixedSize(70, 24)
        self.chk_map.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chk_map.setStyleSheet("""
            QPushButton {
                background: #222; border: 1px solid #555;
                color: #aaa; font-size: 8pt; border-radius: 3px;
            }
            QPushButton:checked {
                background: #00ff41; border: 1px solid #00ff41; color: #000;
            }
        """)
        self.chk_map.toggled.connect(self._toggle_neural)
        self.chk_map.setEnabled(HAS_NEURAL_SUPPORT)
        search_row.addWidget(self.chk_map)

        btn_clear_p = QPushButton("✕")
        btn_clear_p.setFixedSize(24, 20)
        btn_clear_p.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clear_p.setStyleSheet(
            "QPushButton { background: #333; border: none; border-radius: 3px; color: #888; font-size: 9pt; } QPushButton:hover { background: #444; color: #eee; }"
        )
        btn_clear_p.setToolTip("Clear search text")
        btn_clear_p.clicked.connect(lambda: self.search_input.clear())
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
        # Keep focus on the search input for keyboard navigation
        self.list_history.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        # Keep focus on the search input for keyboard navigation
        self.list_pinned.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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

    def _on_ui_opened(self):
        """Initialize keyboard navigation state when the UI becomes visible."""
        # Default to history when UI opens.
        self.set_active_side("history")
        self._ensure_current_item()
        self.search_input.setFocus()

    def set_active_side(self, side: str) -> bool:
        """Set the active side for keyboard actions.

        Deterministic behavior: if the target side has no pasteable clip rows
        (e.g. pinned has only group headers), we keep the previous active side
        and selection so Enter continues to work.

        Returns:
            True if the side switch was applied, False if it was a no-op.
        """
        if side not in ("history", "pinned"):
            return False
        if side == self.active_side:
            # Still ensure a valid selection on this side.
            self._ensure_current_item()
            self.search_input.setFocus()
            return True

        prev_side = self.active_side
        prev_widget = self._active_list()
        prev_row = prev_widget.currentRow() if prev_widget else -1

        target_widget = self.list_history if side == "history" else self.list_pinned
        target_first = self._first_pasteable_row(target_widget, 0)
        if target_first is None:
            # No pasteable clip on target side; keep previous side/selection.
            self.active_side = prev_side
            if prev_widget and prev_row >= 0:
                prev_widget.setCurrentRow(prev_row)
            self._ensure_current_item()
            self.search_input.setFocus()
            return False

        self.active_side = side
        # Prefer keeping current selection if already pasteable; otherwise first pasteable.
        if not self._is_pasteable_item(target_widget.currentItem()):
            target_widget.setCurrentRow(target_first)
        self._ensure_current_item()
        self.search_input.setFocus()
        return True

    def _active_list(self):
        return self.list_history if self.active_side == "history" else self.list_pinned

    def _is_pasteable_item(self, item) -> bool:
        if not item:
            return False
        data = item.data(Qt.ItemDataRole.UserRole)
        return bool(data and isinstance(data, dict) and "content" in data)

    def _first_pasteable_row(self, widget, start_row: int = 0):
        if widget.count() == 0:
            return None
        start_row = max(0, start_row)
        for r in range(start_row, widget.count()):
            if self._is_pasteable_item(widget.item(r)):
                return r
        return None

    def _next_pasteable_row(self, widget, from_row: int, direction: int):
        if widget.count() == 0:
            return None
        if direction == 0:
            return None
        step = 1 if direction > 0 else -1
        r = from_row
        if r < 0:
            r = 0 if step > 0 else widget.count() - 1
        else:
            r = r + step

        while 0 <= r < widget.count():
            if self._is_pasteable_item(widget.item(r)):
                return r
            r += step
        return None

    def _select_with_fallback_rules(self, widget, prev_clip_id, prev_row: int) -> bool:
        """Apply selection fallback rules after a list refresh/filter.

        Rules:
            1) If previously active clip still exists, keep it.
            2) Else choose next selectable after previous row position.
            3) Else choose previous selectable.
            4) Else choose first selectable.

        Returns True if a selection was applied.
        """
        if widget.count() == 0:
            return False

        # 1) Keep same clip if it still exists
        if prev_clip_id is not None:
            for r in range(widget.count()):
                it = widget.item(r)
                if not self._is_pasteable_item(it):
                    continue
                data = it.data(Qt.ItemDataRole.UserRole)
                if isinstance(data, dict) and data.get("id") == prev_clip_id:
                    widget.setCurrentRow(r)
                    widget.scrollToItem(it)
                    return True

        # Normalize previous row hint relative to new widget.
        hint = prev_row
        if hint < 0:
            hint = -1
        if hint >= widget.count():
            hint = widget.count() - 1

        # 2) Next selectable after previous row position.
        # If previous selection was at row 0, we also allow re-selecting row 0
        # (since "after 0" in a 1-row list still needs to land on 0).
        start_next = hint + 1
        if hint == 0:
            start_next = 0
        if start_next < 0:
            start_next = 0
        for r in range(start_next, widget.count()):
            it = widget.item(r)
            if self._is_pasteable_item(it):
                widget.setCurrentRow(r)
                widget.scrollToItem(it)
                return True

        # 3) Previous selectable before previous row position
        start_prev = min(hint - 1, widget.count() - 1)
        for r in range(start_prev, -1, -1):
            it = widget.item(r)
            if self._is_pasteable_item(it):
                widget.setCurrentRow(r)
                widget.scrollToItem(it)
                return True

        # 4) First selectable
        first = self._first_pasteable_row(widget, 0)
        if first is not None:
            it = widget.item(first)
            widget.setCurrentRow(first)
            if it is not None:
                widget.scrollToItem(it)
            return True
        return False

    def _ensure_current_item(self):
        w = self._active_list()
        if w.count() == 0:
            return
        r = w.currentRow()
        if r < 0 or not self._is_pasteable_item(w.currentItem()):
            first = self._first_pasteable_row(w, 0)
            if first is not None:
                w.setCurrentRow(first)

    def _nav_up(self):
        w = self._active_list()
        if w.count() == 0:
            return
        self._ensure_current_item()
        r = self._next_pasteable_row(w, w.currentRow(), -1)
        if r is not None:
            w.setCurrentRow(r)
        self.search_input.setFocus()

    def _nav_down(self):
        w = self._active_list()
        if w.count() == 0:
            return
        self._ensure_current_item()
        r = self._next_pasteable_row(w, w.currentRow(), 1)
        if r is not None:
            w.setCurrentRow(r)
        self.search_input.setFocus()

    def _nav_left(self):
        # Left/Right in the search field switch between columns (history/pinned).
        # This is an explicit tradeoff vs caret movement within the search text.
        self.set_active_side("history")

    def _nav_right(self):
        # Left/Right in the search field switch between columns (history/pinned).
        # This is an explicit tradeoff vs caret movement within the search text.
        self.set_active_side("pinned")

    def _activate_current(self):
        """Paste the currently active item (not based on focus widget)."""
        w = self._active_list()
        if not w:
            return

        # Ensure we don't stop on non-clip rows (eg. pinned group headers)
        self._ensure_current_item()
        ci = w.currentItem()
        if not self._is_pasteable_item(ci):
            return

        data = ci.data(Qt.ItemDataRole.UserRole)
        self.handle_paste(data)
        self.search_input.setFocus()

    def _on_search_text_changed(self, text):
        """Debounced search - waits 200ms after typing stops."""
        self.current_search_query = text.strip()
        print(
            f"[MainUI] _on_search_text_changed: {text.strip()!r}, neural_enabled={self.neural_enabled}, sidecar={self.sidecar is not None}"
        )
        if self.neural_enabled and self.sidecar:
            self.sidecar.search_bar.blockSignals(True)
            self.sidecar.search_bar.setText(text)
            self.sidecar.search_bar.blockSignals(False)
            # focusQuery is called after debounce in _do_search, not here
        self.search_debounce_timer.start(200)  # 200ms debounce

    def _do_search(self):
        """Execute the actual search after debounce — filters both lists."""
        print(f"[MainUI] _do_search: query={self.current_search_query!r}")
        t0 = time.time()
        # Capture active selection before filtering so we can preserve it.
        active_widget = self._active_list()
        prev_row = active_widget.currentRow() if active_widget else -1
        prev_clip_id = None
        if active_widget and self._is_pasteable_item(active_widget.currentItem()):
            d = active_widget.currentItem().data(Qt.ItemDataRole.UserRole)
            if isinstance(d, dict):
                prev_clip_id = d.get("id")

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
                history_clips = self.storage.get_history(
                    limit=PAGE_SIZE_HISTORY, offset=0
                )
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

            # Re-apply selection fallback rules on the active side after filtering.
            # (Keep focus in the search input so typing can continue uninterrupted.)
            active_widget = self._active_list()
            if active_widget is not None:
                self._select_with_fallback_rules(active_widget, prev_clip_id, prev_row)

            # focusQuery cho map — delay 500ms để user gõ xong
            if self.neural_enabled and self.sidecar:
                if hasattr(self, "_focus_query_timer"):
                    self._focus_query_timer.stop()
                else:
                    self._focus_query_timer = QTimer(self)
                    self._focus_query_timer.setSingleShot(True)
                self._focus_query_timer.timeout.connect(
                    lambda q=self.current_search_query: self.sidecar.focus_query(q)
                    if self.sidecar
                    else None
                )
                self._focus_query_timer.start(500)
            self.search_input.setFocus()
            print(f"[MainUI] _do_search completed in {time.time() - t0:.3f}s")
        finally:
            self._is_refreshing = False

    def _on_history_scroll(self, value):
        """Load more history items when scrolling to bottom."""
        if not self.history_has_more:
            return
        scrollbar = self.list_history.verticalScrollBar()
        if value >= scrollbar.maximum() - 50:
            self._load_more_history()

    def _on_pinned_scroll(self, value):
        """Load more pinned items when scrolling to bottom."""
        if not self.pinned_has_more:
            return
        scrollbar = self.list_pinned.verticalScrollBar()
        if value >= scrollbar.maximum() - 50:
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
        if group_name not in self.group_headers:
            return
        header_item = self.group_headers[group_name]
        header_row = self.list_pinned.row(header_item)
        clips = self.storage.get_clips_by_group(group_name)
        width = self.list_pinned.viewport().width()
        if width <= 10:
            width = (self.width() // 2) - 25
        for i, clip in enumerate(clips):
            item = QListWidgetItem()
            ui = ClipItemWidget(clip, True, self, is_grouped=True)
            item.setSizeHint(QSize(width, ui.height()))
            item.setData(Qt.ItemDataRole.UserRole, clip)
            item.setData(Qt.ItemDataRole.UserRole + 1, group_name)
            self.list_pinned.insertItem(header_row + 1 + i, item)
            self.list_pinned.setItemWidget(item, ui)

    def collapse_group(self, group_name):
        """Collapse a group to hide its children."""
        if group_name not in self.expanded_groups:
            return
        self.expanded_groups.discard(group_name)
        items_to_remove = []
        for i in range(self.list_pinned.count()):
            item = self.list_pinned.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole + 1) == group_name:
                items_to_remove.append(i)
        for i in reversed(items_to_remove):
            self.list_pinned.takeItem(i)

    def hideEvent(self, event):
        """When Main UI hides, hide the Map too (only if docked)."""
        if hasattr(self, "sidecar") and self.sidecar and self.sidecar._docked_mode:
            self.sidecar.hide()
        super().hideEvent(event)

    def changeEvent(self, e):
        if (
            e.type() == QEvent.Type.ActivationChange
            and not self.isActiveWindow()
            and not self._is_refreshing
        ):
            # Only skip hide if user genuinely clicked INTO the sidecar
            sidecar_has_focus = (
                hasattr(self, "sidecar")
                and self.sidecar is not None
                and self.sidecar.isActiveWindow()
            )
            if not sidecar_has_focus:
                self.hide()
        super().changeEvent(e)

    def hide_if_visible(self):
        if self.isVisible():
            self.hide()

    def toggle_visibility(self):
        print(
            f"[MainUI] toggle_visibility: visible={self.isVisible()}, map_checked={self.chk_map.isChecked()}"
        )
        if self.isVisible():
            self.hide()
            if self.sidecar and self.chk_map.isChecked() and self.sidecar._docked_mode:
                self.sidecar.hide()
        else:
            self.show_at_cursor()
            if self.sidecar and self.chk_map.isChecked():
                self._show_map_docked()
            # Re-focus main UI after map docked (map.show() steals focus)
            self.raise_()
            self.activateWindow()
            self.search_input.setFocus()

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
        self.active_side = "history"
        self.refresh_lists(force_reset_selection=True)
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
        self.search_input.setFocus()
        self.search_input.setCursorPosition(len(self.search_input.text()))
        self._on_ui_opened()

    def closeEvent(self, event):
        """Clean up background threads on close."""
        if hasattr(self, "neural_engine"):
            self.neural_engine.stop()
        if hasattr(self, "sidecar") and self.sidecar is not None:
            self.sidecar.close()
        super().closeEvent(event)

    def _show_map_docked(self):
        """Show the map docked near the main UI without overlapping it."""
        print(f"[MainUI] _show_map_docked called, sidecar={self.sidecar is not None}")
        if not self.sidecar:
            return
        self.sidecar._docked_mode = True

        # Reload config + force reload nodes every time docked map opens
        self.sidecar.reload_config()
        self._galaxy_loaded = False
        geo = self.geometry()
        map_w = geo.width()
        map_h = int(geo.height() * 2 / 3)

        screen = QGuiApplication.screenAt(geo.center())
        if not screen:
            screen = QGuiApplication.primaryScreen()
        screen_geo = screen.availableGeometry() if screen else None

        if screen_geo:
            # Try ABOVE main UI first
            target_x = geo.x()
            target_y = geo.y() - map_h

            if target_y < screen_geo.top():
                # Not enough space above → try BELOW
                target_y = geo.y() + geo.height()

            if target_y + map_h > screen_geo.bottom():
                # Not enough space below either → dock to RIGHT, matching height
                map_w = int(geo.width() * 2 / 3)
                map_h = geo.height()
                target_x = geo.x() + geo.width()
                target_y = geo.y()

                if target_x + map_w > screen_geo.right():
                    # Not enough space right → dock to LEFT
                    target_x = geo.x() - map_w
                    if target_x < screen_geo.left():
                        target_x = screen_geo.left()

            # Final horizontal bounds check
            if target_x < screen_geo.left():
                target_x = screen_geo.left()
            if target_x + map_w > screen_geo.right():
                map_w = screen_geo.right() - target_x
        else:
            target_x = geo.x()
            target_y = geo.y() - map_h

        self.sidecar.setGeometry(target_x, target_y, map_w, map_h)
        self.sidecar.show()
        # Delay galaxy load to give QWebEngineView time to acquire real dimensions
        QTimer.singleShot(500, self._load_full_galaxy_into_sidecar)

    def _show_neural_floating(self):
        """Open Neural Map as independent floating window at cursor (like test_neural_show.py)."""
        print(
            f"[MainUI] _show_neural_floating called, sidecar={self.sidecar is not None}"
        )
        if not self.sidecar:
            return
        self.sidecar._docked_mode = False  # Independent — never auto-hide

        # If already visible, just bring to front
        if self.sidecar.isVisible():
            self.sidecar.activateWindow()
            self.sidecar.raise_()
            return

        # Reload config every time map opens
        self.sidecar.reload_config()
        self._galaxy_loaded = False  # Force reload nodes (may have new clips)

        cursor_pos = QCursor.pos()
        map_w = 700
        map_h = 500

        target_x = cursor_pos.x() - map_w // 2
        target_y = cursor_pos.y() - map_h // 2

        # Bound to screen
        screen = QGuiApplication.screenAt(cursor_pos)
        if not screen:
            screen = QGuiApplication.primaryScreen()
        if screen:
            sg = screen.availableGeometry()
            if target_x < sg.left():
                target_x = sg.left()
            elif target_x + map_w > sg.right():
                target_x = sg.right() - map_w
            if target_y < sg.top():
                target_y = sg.top()
            elif target_y + map_h > sg.bottom():
                target_y = sg.bottom() - map_h

        self.sidecar.setGeometry(target_x, target_y, map_w, map_h)
        self.sidecar.show()
        # Delay galaxy load to give QWebEngineView time to acquire real dimensions
        QTimer.singleShot(500, self._load_full_galaxy_into_sidecar)

    def _load_full_galaxy_into_sidecar(self, force=False):
        """Load the full neural galaxy (all indexed nodes) into sidecar — like test_neural_show.py does.
        Skips if already loaded unless force=True."""
        print(
            f"[MainUI] _load_full_galaxy: force={force}, _galaxy_loaded={self._galaxy_loaded}"
        )
        if not self.sidecar:
            return
        if self._galaxy_loaded and not force:
            # Already loaded, just do focus if needed
            if self.current_search_query:
                self.sidecar.focus_query(self.current_search_query)
            return
        indexed_ids = self.storage.get_all_clip_ids_with_vectors(limit=500)
        if not indexed_ids:
            return
        nodes, links = self.storage.get_neural_data(indexed_ids)
        formatted_links = [
            {
                "source": l["source_id"],
                "target": l["target_id"],
                "weight": float(l["weight"]),
            }
            for l in links
        ]
        self.sidecar.update_data(nodes, formatted_links)
        self._galaxy_loaded = True
        if self.current_search_query:
            self.sidecar.focus_query(self.current_search_query)

    def _toggle_neural(self, checked):
        self.neural_enabled = checked
        print(
            f"[MainUI] _toggle_neural: checked={checked}, sidecar={self.sidecar is not None}"
        )
        if checked and self.sidecar:
            self._show_map_docked()
        elif self.sidecar:
            self.sidecar.hide()

    def _refresh_neural_status(self):
        if not HAS_NEURAL_SUPPORT:
            self.chk_map.setText("Map OFF")
            return
        if not hasattr(self, "neural_engine") or self.neural_engine is None:
            self.chk_map.setText("Map OFF")
            return
        if self.chk_map.isChecked():
            self.chk_map.setText("Map ON")
        else:
            self.chk_map.setText("Map OFF")

    def _on_sidecar_search_changed(self, text):
        if self.search_input.text() != text:
            self.search_input.setText(text)
        if self.sidecar:
            self.sidecar.focus_query(text.strip())

    def _on_node_clicked(self, clip_id):
        """Jump to clip in UI when node is clicked in graph.
        If clip is not in current list view, load it directly from DB."""
        # First try to find in current lists
        for widget in [self.list_history, self.list_pinned]:
            for r in range(widget.count()):
                it = widget.item(r)
                data = it.data(Qt.ItemDataRole.UserRole)
                if isinstance(data, dict) and data.get("id") == clip_id:
                    self.active_side = (
                        "history" if widget == self.list_history else "pinned"
                    )
                    widget.setCurrentRow(r)
                    widget.scrollToItem(it)
                    self.show()
                    self.activateWindow()
                    return

        # Not in current view — load clip directly from DB and put in search
        clip_data = self.storage.get_clip_by_id(clip_id)
        if clip_data and clip_data.get("content"):
            # Use first few words as search to bring the clip into view
            content = clip_data["content"]
            search_term = content[:30].strip().split("\n")[0]
            self.search_input.setText(search_term)
            self.show()
            self.activateWindow()

    def _update_sidecar_graph(self):
        """Fetch all nodes in the neural window and sync sidecar."""
        if not self.sidecar or not hasattr(self.sidecar, "update_data"):
            return

        # Get all IDs in the neural window (not just the current UI list)
        window_ids = []
        if getattr(self.neural_engine, "index_pinned_always", True):
            window_ids.extend(self.storage.get_all_pinned_ids())
        window_ids.extend(
            self.storage.get_recent_history_ids(
                getattr(self.neural_engine, "max_recent_index", 200)
            )
        )

        # De-duplicate
        seen = set()
        deduped_ids = []
        for cid in window_ids:
            if cid not in seen:
                seen.add(cid)
                deduped_ids.append(cid)

        if deduped_ids:
            nodes, links = self.storage.get_neural_data(deduped_ids)
            formatted_links = [
                {
                    "source": l["source_id"],
                    "target": l["target_id"],
                    "weight": l["weight"],
                }
                for l in links
            ]
            self.sidecar.update_data(nodes, formatted_links)

            if self.current_search_query:
                self.sidecar.focus_query(self.current_search_query)
            else:
                active_widget = self._active_list()
                if active_widget.currentItem():
                    d = active_widget.currentItem().data(Qt.ItemDataRole.UserRole)
                    if isinstance(d, dict) and d.get("id"):
                        self.sidecar.focus_node(d["id"])

    def on_item_clicked(self, item):
        if self.input_locked:
            logger.info("item_click_ignored reason=input_locked")
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if data and isinstance(data, dict) and "content" in data:
            logger.info(
                "item_clicked clip_id=%s type=%s preview=%r",
                data.get("id"),
                data.get("type"),
                str(data.get("content", ""))[:80],
            )
            self.handle_paste(data)

    def handle_paste(self, data):
        if self._paste_in_progress or not data or "content" not in data:
            logger.info(
                "handle_paste_skipped in_progress=%s has_data=%s has_content=%s",
                self._paste_in_progress,
                bool(data),
                bool(data and "content" in data),
            )
            return

        self._paste_in_progress = True
        self.input_locked = True
        logger.info(
            "handle_paste_start clip_id=%s type=%s target_hwnd=%s",
            data.get("id"),
            data.get("type"),
            self.last_active_window_handle,
        )

        self.hide()
        QTimer.singleShot(0, self._reset_ui_after_paste_request)
        QTimer.singleShot(0, lambda: self._prepare_clipboard_and_paste(data, 0))

    def _prepare_clipboard_and_paste(self, data, attempt_index):
        retry_delays = (0, 30, 70, 140)
        logger.info(
            "prepare_clipboard attempt=%s clip_id=%s type=%s",
            attempt_index,
            data.get("id"),
            data.get("type"),
        )

        if not self._write_clipboard_payload(data):
            next_attempt = attempt_index + 1
            logger.warning(
                "prepare_clipboard_failed attempt=%s clip_id=%s next_attempt=%s",
                attempt_index,
                data.get("id"),
                next_attempt,
            )
            if next_attempt < len(retry_delays):
                QTimer.singleShot(
                    retry_delays[next_attempt],
                    lambda: self._prepare_clipboard_and_paste(data, next_attempt),
                )
            else:
                logger.error(
                    "prepare_clipboard_exhausted clip_id=%s type=%s",
                    data.get("id"),
                    data.get("type"),
                )
                self._finish_paste_attempt(clear_guard=True)
            return

        self._set_pending_clipboard_guard(data)
        logger.info("prepare_clipboard_success clip_id=%s", data.get("id"))
        QTimer.singleShot(10, lambda: self._restore_focus_and_paste(12))

    def _write_clipboard_payload(self, data):
        try:
            if data["type"] == "text":
                self.clipboard.setText(data["content"])
                ok = self.clipboard.text() == data["content"]
                logger.info(
                    "write_clipboard_text clip_id=%s success=%s length=%s",
                    data.get("id"),
                    ok,
                    len(data["content"]),
                )
                return ok

            p = os.path.join(IMAGE_DIR, data["content"])
            if not os.path.exists(p):
                logger.error(
                    "write_clipboard_image_missing clip_id=%s path=%s",
                    data.get("id"),
                    p,
                )
                return False

            pixmap = QPixmap(p)
            if pixmap.isNull():
                logger.error(
                    "write_clipboard_image_invalid clip_id=%s path=%s",
                    data.get("id"),
                    p,
                )
                return False

            self.clipboard.setPixmap(pixmap)
            mime = self.clipboard.mimeData()
            if not mime or not mime.hasImage():
                logger.error("write_clipboard_image_no_mime clip_id=%s", data.get("id"))
                return False

            img = QImage(mime.imageData())
            ok = not img.isNull() and self._image_storage_name(img) == data["content"]
            logger.info(
                "write_clipboard_image clip_id=%s success=%s", data.get("id"), ok
            )
            return ok
        except Exception as e:
            logger.error("write_clipboard_failed: %s", e)
            return False

    def _restore_focus_and_paste(self, attempts_remaining):
        ready = self._ready_to_paste()
        logger.info(
            "restore_focus attempts_remaining=%s ready=%s",
            attempts_remaining,
            ready,
        )
        if attempts_remaining > 0 and not ready:
            QTimer.singleShot(
                20, lambda: self._restore_focus_and_paste(attempts_remaining - 1)
            )
            return
        self._perform_keyboard_paste()

    def _ready_to_paste(self):
        if sys.platform != "win32":
            return True
        user32 = ctypes.windll.user32
        ctrl_down = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
        alt_down = bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000)
        if ctrl_down or alt_down:
            return False
        target_hwnd = self.last_active_window_handle
        if target_hwnd:
            return user32.GetForegroundWindow() == target_hwnd
        return True

    def _perform_keyboard_paste(self):
        logger.info(
            "perform_keyboard_paste target_hwnd=%s", self.last_active_window_handle
        )
        simulate_paste()
        self._finish_paste_attempt()

    def _finish_paste_attempt(self, clear_guard=False):
        self._paste_in_progress = False
        self.input_locked = False
        if clear_guard:
            self.pending_clipboard_guard = None

    def _reset_ui_after_paste_request(self):
        self.search_input.clear()
        self.current_search_query = ""

    def _image_storage_name(self, img):
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        ih = hashlib.md5(ba.data()).hexdigest()
        return f"{ih}.png"

    def _set_pending_clipboard_guard(self, data):
        self.pending_clipboard_guard = {
            "type": data["type"],
            "content": data["content"],
            "expires_at": time.monotonic() + 1.5,
        }

    def _clear_pending_clipboard_guard_if_expired(self):
        if (
            self.pending_clipboard_guard
            and time.monotonic() > self.pending_clipboard_guard["expires_at"]
        ):
            self.pending_clipboard_guard = None

    def _should_ignore_clipboard_update(self, mime):
        self._clear_pending_clipboard_guard_if_expired()
        guard = self.pending_clipboard_guard
        if not guard or not mime:
            return False
        if guard["type"] == "text" and mime.hasText():
            if mime.text() == guard["content"]:
                self.pending_clipboard_guard = None
                return True
        elif guard["type"] == "image" and mime.hasImage():
            img = QImage(mime.imageData())
            if not img.isNull() and self._image_storage_name(img) == guard["content"]:
                self.pending_clipboard_guard = None
                return True
        self.pending_clipboard_guard = None
        return False

    def _get_current_selection_info(self, widget):
        row = widget.currentRow() if widget else -1
        clip_id = None
        if widget and self._is_pasteable_item(widget.currentItem()):
            d = widget.currentItem().data(Qt.ItemDataRole.UserRole)
            if isinstance(d, dict):
                clip_id = d.get("id")
        return clip_id, row

    def refresh_lists(self, force_reset_selection=False):
        """Refresh both lists from SQLite with pagination reset."""
        active_widget = (
            self.list_history if self.active_side == "history" else self.list_pinned
        )
        if force_reset_selection:
            prev_clip_id, prev_row = None, -1
        else:
            prev_clip_id, prev_row = self._get_current_selection_info(active_widget)

        h_s, p_s = (
            self.list_history.verticalScrollBar().value(),
            self.list_pinned.verticalScrollBar().value(),
        )
        self._is_refreshing = True
        try:
            self.setUpdatesEnabled(False)
            self.history_offset = 0
            self.pinned_offset = 0
            self.history_has_more = True
            self.pinned_has_more = True
            self.expanded_groups.clear()
            self.group_headers.clear()
            self.list_history.clear()
            self.list_pinned.clear()
            history_clips = self.storage.get_history(limit=PAGE_SIZE_HISTORY, offset=0)
            if len(history_clips) < PAGE_SIZE_HISTORY:
                self.history_has_more = False
            self._append_items(history_clips, self.list_history, False)
            self.history_offset = len(history_clips)
            self.refresh_pinned_list()
            self._select_with_fallback_rules(active_widget, prev_clip_id, prev_row)
            self.list_history.verticalScrollBar().setValue(h_s)
            self.list_pinned.verticalScrollBar().setValue(p_s)
            self.setUpdatesEnabled(True)
        finally:
            self._is_refreshing = False

    def refresh_pinned_list(self):
        """Refresh pinned list with groups and search."""
        p_s = self.list_pinned.verticalScrollBar().value()
        previously_expanded = self.expanded_groups.copy()
        self.list_pinned.clear()
        self.group_headers.clear()
        self.pinned_offset = 0
        self.pinned_has_more = True
        width = self.list_pinned.viewport().width()
        if width <= 10:
            width = (self.width() // 2) - 25
        if self.current_search_query:
            clips = self.storage.search_pinned(self.current_search_query)
            self._append_items(clips, self.list_pinned, True)
        else:
            groups = self.storage.get_groups()
            for group_name in groups:
                clips_in_group = self.storage.get_clips_by_group(group_name)
                if clips_in_group:
                    item = QListWidgetItem()
                    header = GroupHeaderWidget(group_name, len(clips_in_group), self)
                    if group_name in previously_expanded:
                        header.set_expanded(True)
                    item.setSizeHint(QSize(width, 45))
                    self.list_pinned.addItem(item)
                    self.list_pinned.setItemWidget(item, header)
                    self.group_headers[group_name] = item
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
            ungrouped = self.storage.get_ungrouped_pinned(
                limit=PAGE_SIZE_PINNED, offset=0
            )
            if len(ungrouped) < PAGE_SIZE_PINNED:
                self.pinned_has_more = False
            self._append_items(ungrouped, self.list_pinned, True)
            self.pinned_offset = len(ungrouped)
        self.list_pinned.verticalScrollBar().setValue(p_s)

    def handle_copy_only(self, data):
        self._set_pending_clipboard_guard(data)
        if data["type"] == "text":
            self.clipboard.setText(data["content"])
        else:
            p = os.path.join(IMAGE_DIR, data["content"])
            if os.path.exists(p):
                self.clipboard.setPixmap(QPixmap(p))

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
        super().keyPressEvent(e)

    def on_clipboard_change_delayed(self):
        """Delay reading clipboard slightly so source apps can finish publishing data."""
        QTimer.singleShot(15, lambda: self._process_clipboard_data_retry(0))

    def _process_clipboard_data(self):
        self._process_clipboard_data_retry(0)

    def _process_clipboard_data_retry(self, attempt_index):
        retry_delays = (15, 35, 75, 120)
        mime = self.clipboard.mimeData()
        if self._should_ignore_clipboard_update(mime):
            return
        has_content = False
        if mime:
            if mime.hasImage() and not mime.imageData().isNull():
                has_content = True
            elif mime.hasText() and mime.text().strip():
                has_content = True
        if not has_content:
            next_attempt = attempt_index + 1
            if next_attempt < len(retry_delays):
                QTimer.singleShot(
                    retry_delays[next_attempt],
                    lambda: self._process_clipboard_data_retry(next_attempt),
                )
            else:
                print(
                    "[Win32Monitor] Warning: Received clipboard event but content is empty or unreadable after retries."
                )
            return
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
        clip_id, is_new = self.storage.add_clip(clip_type, content)
        if self.isVisible():
            self.refresh_lists()
        else:
            self.is_ui_dirty = True

    def save_image_if_new(self, img):
        fn = self._image_storage_name(img)
        fp = os.path.join(IMAGE_DIR, fn)
        if not os.path.exists(fp):
            img.save(fp, "PNG")
        return fn

    def on_search_changed(self, text):
        """Handle search input change (legacy, not used with debounce)."""
        self.current_search_query = text.strip()
        self.refresh_pinned_list()

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

    def handle_move(self, clip_id, direction, is_pinned):
        """Move clip up/down."""
        self.storage.move_clip(clip_id, direction, is_pinned)
        self.refresh_lists()


def main():
    parser = argparse.ArgumentParser(description="Advance Clipboard Manager")
    parser.add_argument(
        "--index-all",
        type=int,
        metavar="COUNT",
        help="Force index N clips using Neural Engine and exit",
    )
    args = parser.parse_args()

    # If --index-all is passed, run indexing and exit
    if args.index_all:
        print(f"--- Force Indexing {args.index_all} Clips ---")
        from neural.engine import NeuralEngine
        from storage import get_storage

        store = get_storage()
        config_path = os.path.join(os.path.dirname(__file__), "neural", "config.json")
        engine = NeuralEngine(store, config_path)

        try:
            from sentence_transformers import SentenceTransformer
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                engine.model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
        except ImportError:
            print("ERROR: sentence-transformers not installed.")
            sys.exit(1)

        unindexed = store.get_unindexed_ids_within_window(
            recent_limit=engine.max_recent_index,
            include_pinned=engine.index_pinned_always,
            limit=args.index_all,
        )
        indexed_in_window, total_in_window = store.get_neural_window_totals(
            recent_limit=engine.max_recent_index,
            include_pinned=engine.index_pinned_always,
        )
        total = len(unindexed)
        if total == 0:
            print(
                f"No unindexed clips found inside neural window ({indexed_in_window}/{total_in_window})."
            )
            sys.exit(0)

        print(
            f"Found {total} unindexed clips inside neural window. Current progress: {indexed_in_window}/{total_in_window}. Starting..."
        )

        # Process in batches of 10 for better progress reporting
        batch_size = 10
        for i in range(0, total, batch_size):
            batch = unindexed[i : i + batch_size]
            t0 = time.time()
            engine._index_clips(batch)
            dt = time.time() - t0
            print(
                f"Indexed {min(i + batch_size, total)}/{total} clips... (batch time: {dt:.2f}s)"
            )

        print("Indexing complete.")
        sys.exit(0)

    logger.info("main_start pid=%s", os.getpid())
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
    app.setPalette(palette)

    # Allow Ctrl+C in terminal to kill the app
    import signal

    signal.signal(signal.SIGINT, lambda *args: app.quit())
    # Timer to let Python process signals (Qt blocks the Python signal handler otherwise)
    _signal_timer = QTimer()
    _signal_timer.start(200)
    _signal_timer.timeout.connect(lambda: None)

    window = ClientApp()
    exit_code = app.exec()
    logger.info("main_exit exit_code=%s", exit_code)
    _fault_log_handle.flush()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

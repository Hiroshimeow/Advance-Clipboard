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
    QLabel,
    QPushButton,
    QAbstractItemView,
    QMessageBox,
    QMenu,
    QInputDialog,
)
from PyQt6.QtCore import (
    Qt,
    QTimer,
    QSize,
    QEvent,
    QByteArray,
    QBuffer,
    QIODevice,
)
from PyQt6.QtGui import (
    QCursor,
    QGuiApplication,
    QColor,
    QPalette,
    QPixmap,
    QImage,
)

# Pure Win32 clipboard monitor & hotkey (no pynput, no keyboard hooks)
from win32_monitor import Win32ClipboardMonitor, VK_CONTROL, VK_MENU, simulate_paste

# Import storage and backup modules
from storage import get_storage
from backup_manager import (
    create_backup,
    find_valid_backup,
    import_legacy_json,
    BackupScheduler,
)
from ui_widgets import (
    SmoothListWidget,
    LineInfoPopup,
    SearchLineEdit,
    GroupHeaderWidget,
    ClipItemWidget,
    PAGE_SIZE_HISTORY,
    PAGE_SIZE_PINNED,
)
from clipboard_browser_controller import ClipboardBrowserController

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


class ClientApp(QWidget):
    def __init__(self, *, enable_monitor: bool = True, init_data: bool = True):
        super().__init__()
        # SQLite storage - single source of truth
        self.storage = get_storage()

        # UI state
        self.pending_clipboard_guard = None
        self.is_ui_dirty = True
        self.input_locked = False
        self.last_active_window_handle = None
        self._paste_in_progress = False

        # Browser Controller (Search, Nav, Pagination)
        self.browser = ClipboardBrowserController(self)

        # Neural Memory state
        self.neural_enabled = False
        self._galaxy_loaded = False  # True after first full galaxy load into sidecar

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
        self.storage.set_neural_event_callback(self._on_neural_storage_event)

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

    def _on_neural_storage_event(self, event_type, clip_id):
        if not getattr(self, "neural_engine", None):
            return
        if event_type == "new_clip":
            self.neural_engine.enqueue_new_clip(clip_id)
        elif event_type == "pin_state_changed":
            self.neural_engine.enqueue_priority_reindex(clip_id)

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
        self.search_input.setPlaceholderText("🔍 Search...")
        self.search_input.setFixedHeight(28)
        self.search_input.textChanged.connect(self.browser.on_search_text_changed)
        self.search_input.set_key_handlers(
            on_up=self.browser.nav_up,
            on_down=self.browser.nav_down,
            on_left=self.browser.nav_left,
            on_right=self.browser.nav_right,
            on_enter=self.browser.activate_current,
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
                color: #00ff41; font-size: 8pt; font-weight: bold; border-radius: 3px;\
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
        self.list_history.setResizeMode(SmoothListWidget.ResizeMode.Adjust)
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
        self.list_pinned.setResizeMode(SmoothListWidget.ResizeMode.Adjust)
        self.list_pinned.itemClicked.connect(self.on_item_clicked)
        col_p.addWidget(self.list_pinned)

        # Connect scroll for pagination
        self.list_history.verticalScrollBar().valueChanged.connect(
            self.browser.on_history_scroll
        )
        self.list_pinned.verticalScrollBar().valueChanged.connect(
            self.browser.on_pinned_scroll
        )

        columns_layout.addLayout(col_h, 1)
        columns_layout.addLayout(col_p, 1)
        outer_layout.addLayout(columns_layout)
        self.setLayout(outer_layout)

    @property
    def active_side(self):
        return self.browser.active_side

    @active_side.setter
    def active_side(self, value):
        self.browser.active_side = value

    @property
    def current_search_query(self):
        return self.browser.current_search_query

    @current_search_query.setter
    def current_search_query(self, value):
        self.browser.current_search_query = value

    def set_active_side(self, side):
        return self.browser.set_active_side(side)

    def _do_search(self):
        return self.browser._do_search()

    def _active_list(self):
        return self.browser._active_list()

    def _is_pasteable_item(self, item):
        return self.browser._is_pasteable_item(item)

    def _ensure_current_item(self):
        return self.browser._ensure_current_item()

    def _select_with_fallback_rules(self, widget, prev_clip_id, prev_row):
        return self.browser._select_with_fallback_rules(prev_clip_id, prev_row)

    def _on_ui_opened(self):
        self.browser.on_ui_opened()

    def expand_group(self, group_name):
        self.browser.expand_group(group_name)

    def collapse_group(self, group_name):
        self.browser.collapse_group(group_name)

    def refresh_lists(self, force_reset_selection=False):
        self.browser.refresh_lists(maintain_selection=not force_reset_selection)

    def refresh_pinned_list(self):
        self.browser.refresh_pinned_list()

    def toggle_visibility(self):
        print(
            f"[MainUI] toggle_visibility: visible={self.isVisible()}, map_checked={self.chk_map.isChecked()}"
        )
        if self.isVisible():
            self.hide()
            if (
                hasattr(self, "sidecar")
                and self.sidecar
                and getattr(self.sidecar, "_docked_mode", False)
            ):
                self.sidecar.hide()
        else:
            self.show_at_cursor()
            if self.sidecar and self.chk_map.isChecked():
                self._show_map_docked()
            # Re-focus main UI after map docked (map.show() steals focus)
            self.raise_()
            self.activateWindow()
            self.search_input.setFocus()
            self._on_ui_opened()

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
        self.browser.active_side = "history"
        self.browser.refresh_lists(maintain_selection=False)
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

        # Force exact size + position (reset any manual resize the user did)
        self.sidecar.setGeometry(target_x, target_y, map_w, map_h)
        self.sidecar.show()
        # Re-apply after show() — on Windows, setGeometry before show() can be
        # ignored when the window was previously shown at a different size.
        self.sidecar.move(target_x, target_y)
        self.sidecar.resize(map_w, map_h)
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
            if self.browser.current_search_query:
                self.sidecar.focus_query(self.browser.current_search_query)
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
        if self.browser.current_search_query:
            self.sidecar.focus_query(self.browser.current_search_query)

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
                    self.browser.active_side = (
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

            if self.browser.current_search_query:
                self.sidecar.focus_query(self.browser.current_search_query)
            else:
                active_widget = self.browser._active_list()
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
        self.browser.current_search_query = ""

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

    def hide_if_visible(self):
        if self.isVisible():
            self.hide()

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

    def handle_move(self, clip_id, direction, is_pinned):
        """Move clip up/down."""
        self.storage.move_clip(clip_id, direction, is_pinned)
        self.refresh_lists()

    def hideEvent(self, event):
        """When Main UI hides, hide the Map too (only if docked)."""
        if (
            hasattr(self, "sidecar")
            and self.sidecar
            and getattr(self.sidecar, "_docked_mode", False)
        ):
            self.sidecar.hide()
        super().hideEvent(event)

    def changeEvent(self, e):
        if (
            e.type() == QEvent.Type.ActivationChange and not self.isActiveWindow()
            # and not self._is_refreshing  # controller owns this now
        ):
            # Only skip hide if user genuinely clicked INTO the sidecar
            sidecar_has_focus = (
                hasattr(self, "sidecar")
                and self.sidecar is not None
                and hasattr(self.sidecar, "isActiveWindow")
                and self.sidecar.isActiveWindow()
            )
            if not sidecar_has_focus:
                self.hide()
        super().changeEvent(e)


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

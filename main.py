# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = [
#     "PyQt6",
# ]
# ///
import os
import sys
import ctypes
import ctypes.wintypes
import atexit
import faulthandler
import logging
import threading
import time
import traceback

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

    QEvent,
)
from PyQt6.QtGui import (
    QCursor,
    QGuiApplication,
    QColor,
    QPalette,
    QPixmap,
    QImage,
)

# Pure Win32 clipboard monitor & hotkey
from core.clipboard_monitor import (
    Win32ClipboardMonitor,
    VK_CONTROL,
    VK_MENU,
    simulate_paste,
)
from core.single_instance import SingleInstanceCoordinator
from core.clipboard_ingest import CaptureJob, ClipboardIngestBridge, ClipboardIngestProcessor

# Import storage and backup modules
from storage import get_storage
from storage.backup import (
    create_backup_in_subprocess,
    find_valid_backup,
    import_legacy_json,
    BackupScheduler,
)
from ui.widgets import (
    SearchLineEdit,
    PAGE_SIZE_HISTORY,
    PAGE_SIZE_PINNED,
)
from ui.clip_list_view import ClipListView
from ui.clip_models import HistoryListModel, PinnedListModel
from ui.clipboard_browser_controller import ClipboardBrowserController




# --- Cấu hình ---
DATA_FILE = os.path.join(os.path.dirname(__file__), "data.json")
IMAGE_DIR = os.path.join(os.path.dirname(__file__), "images")
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
DEBUG_LOG_FILE = os.path.join(LOG_DIR, "Advance Clipboard.debug.log")
FAULT_LOG_FILE = os.path.join(LOG_DIR, "Advance Clipboard.fault.log")

UI_EDGE_MARGIN = 150  # Minimum distance from screen edges
SW_RESTORE = 9
ENABLE_RESTORE_FOCUS_AFTER_BG_CLICK = True
FOCUS_RESTORE_POLL_MS = 8
FOCUS_RESTORE_TIMEOUT_MS = 180

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
        self.ingest_processor = ClipboardIngestProcessor(self.storage, image_dir=IMAGE_DIR)
        self.ingest_bridge = ClipboardIngestBridge(self.ingest_processor)
        self.ingest_bridge.completed.connect(
            self._on_clipboard_ingest_completed, Qt.ConnectionType.QueuedConnection
        )
        self.ingest_bridge.rejected.connect(
            self._on_clipboard_ingest_rejected, Qt.ConnectionType.QueuedConnection
        )

        # UI state
        self.pending_clipboard_guard = None
        self.is_ui_dirty = True
        self.pending_ui_clip_ids = []
        self._requires_full_ui_refresh = True
        self.input_locked = False
        self.last_active_window_handle = None
        self.last_focus_window_handle = None
        self._ui_opening_until = 0.0
        self._paste_in_progress = False
        self._last_clipboard_capture_at = 0.0
        self._last_ingested_clipboard_key = None
        self._last_ingested_clipboard_at = 0.0
        self._pending_clipboard_keys = set()
        self._clipboard_capture_timer = QTimer(self)
        self._clipboard_capture_timer.setSingleShot(True)
        self._clipboard_capture_timer.setInterval(35)
        self._clipboard_capture_timer.timeout.connect(
            lambda: self._process_clipboard_data_retry(0)
        )
        self._hidden_refresh_timer = QTimer(self)
        self._hidden_refresh_timer.setSingleShot(True)
        self._hidden_refresh_timer.timeout.connect(self._refresh_hidden_ui_cache)
        self._pending_delete_clip_ids = set()
        self._delete_flush_timer = QTimer(self)
        self._delete_flush_timer.setSingleShot(True)
        self._delete_flush_timer.timeout.connect(self._flush_pending_deletes)

        # Browser Controller (Search, Nav, Pagination)
        self.browser = ClipboardBrowserController(self)



        # Init UI
        self.initUI()


        # Load data with disaster recovery
        if init_data:
            self._init_data()
            self.is_ui_dirty = False



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
        """Create backup outside the UI process to avoid GIL stalls."""
        if create_backup_in_subprocess():
            self.storage.clear_backup_flag()


    def _cleanup_on_exit(self):
        """Cleanup when app exits."""
        logger.info("cleanup_on_exit storage_need_backup=%s", self.storage.need_backup)
        if getattr(self, "browser", None):
            self.browser.shutdown_search()
        # Stop Win32 monitor thread
        if getattr(self, "win32_monitor", None):
            monitor = self.win32_monitor
            if monitor is not None:
                monitor.stop()
        if getattr(self, "ingest_bridge", None):
            self.ingest_bridge.stop()
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
            QListView { background-color: #202020; border: 1px solid #3a3a3a; border-radius: 6px; outline: none; padding: 6px; }
            QListView::item { border-bottom: 1px solid #303030; margin: 3px 0px; }
            QListView::item:selected { background-color: transparent; }
            QScrollBar:vertical { border: none; background: #1f1f1f; width: 8px; margin: 4px 0px 4px 0px; }
            QScrollBar::handle:vertical { background: #555; min-height: 24px; border-radius: 4px; }
            QScrollBar::handle:vertical:hover { background: #666; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
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
            on_enter=self.browser.activate_current,
        )
        search_row.addWidget(self.search_input, stretch=1)



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
        self.list_history = ClipListView(HistoryListModel(self))
        # Keep focus on the search input for keyboard navigation
        self.list_history.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list_history.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.list_history.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.list_history.itemClicked.connect(self.on_item_clicked)
        self.list_history.rowActivated.connect(self.handle_paste)
        self.list_history.copyRequested.connect(self.handle_copy_only)
        self.list_history.pinToggleRequested.connect(self.handle_star)
        self.list_history.deleteRequested.connect(self.handle_delete)
        self.list_history.expandToggleRequested.connect(self.handle_toggle_expand)
        col_h.addWidget(self.list_history)

        # PINNED column
        col_p = QVBoxLayout()
        self.list_pinned = ClipListView(PinnedListModel(self))
        # Keep focus on the search input for keyboard navigation
        self.list_pinned.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list_pinned.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.list_pinned.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.list_pinned.itemClicked.connect(self.on_item_clicked)
        self.list_pinned.rowActivated.connect(self.handle_paste)
        self.list_pinned.copyRequested.connect(self.handle_copy_only)
        self.list_pinned.pinToggleRequested.connect(self.handle_star)
        self.list_pinned.deleteRequested.connect(self.handle_delete)
        self.list_pinned.expandToggleRequested.connect(self.handle_toggle_expand)
        self.list_pinned.groupToggleRequested.connect(
            lambda group, expanded: self.expand_group(group)
            if expanded
            else self.collapse_group(group)
        )
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
        self.browser.bind_viewports()
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

    def eventFilter(self, watched, event):
        self.browser.handle_viewport_event(watched, event)
        return super().eventFilter(watched, event)

    def expand_group(self, group_name):
        self.browser.expand_group(group_name)

    def collapse_group(self, group_name):
        self.browser.collapse_group(group_name)

    def refresh_lists(self, force_reset_selection=False):
        self.browser.refresh_lists(maintain_selection=not force_reset_selection)
        self.pending_ui_clip_ids.clear()
        self._requires_full_ui_refresh = False
        self.is_ui_dirty = False

    def refresh_pinned_list(self):
        self.browser.refresh_pinned_list()

    def toggle_visibility(self):
        print(f"[MainUI] toggle_visibility: visible={self.isVisible()}")
        if self.isVisible():
            self.hide()
        else:
            self.show_at_cursor()

    def activate_from_secondary(self):
        if not self.isVisible():
            self.show_at_cursor()
            return
        self.raise_()
        self.activateWindow()

    def show_at_cursor(self):
        self.input_locked = True
        QTimer.singleShot(30, lambda: setattr(self, "input_locked", False))
        self._ui_opening_until = time.monotonic() + 0.25
        if sys.platform == "win32":
            try:
                self.last_active_window_handle = (
                    ctypes.windll.user32.GetForegroundWindow()
                )
                self.last_focus_window_handle = self._get_focus_window_handle(
                    self.last_active_window_handle
                )
            except:
                pass
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
        if self.is_ui_dirty:
            self.browser.active_side = "history"
            self.browser.expanded_clip_ids.clear()
            self.list_history.setCurrentRow(-1)
            self.list_pinned.setCurrentRow(-1)
        else:
            self.browser.reset_for_hotkey_open(refresh=False)
            self._on_ui_opened()
        if self.is_ui_dirty:
            QTimer.singleShot(50, self._refresh_after_show)

    def _refresh_after_show(self):
        refresh_started_at = time.perf_counter()
        if not self.isVisible() or not self.is_ui_dirty:
            return
        self.browser.active_side = "history"
        self.browser.expanded_clip_ids.clear()

        if not self._requires_full_ui_refresh and self.pending_ui_clip_ids:
            applied = self.browser.apply_pending_history_updates(self.pending_ui_clip_ids)
            if applied:
                self.pending_ui_clip_ids.clear()
                self.is_ui_dirty = False
                logger.info(
                    "ui_incremental_refresh_after_show elapsed_ms=%.2f",
                    (time.perf_counter() - refresh_started_at) * 1000,
                )
                self._on_ui_opened()
                return

        self.browser.start_background_refresh()
        logger.info(
            "ui_background_refresh_after_show_scheduled elapsed_ms=%.2f",
            (time.perf_counter() - refresh_started_at) * 1000,
        )

    def _schedule_hidden_ui_refresh(self, clip_id=None, *, full_refresh=False):
        # Hot path: never rebuild rendered rows while the popup is hidden.
        # Clipboard events can arrive in bursts; rendering the hidden UI on each
        # event is the main reason the app feels progressively laggier.
        self.is_ui_dirty = True
        if full_refresh:
            self._requires_full_ui_refresh = True
            self.pending_ui_clip_ids.clear()
        elif clip_id is not None and not self._requires_full_ui_refresh:
            if clip_id in self.pending_ui_clip_ids:
                self.pending_ui_clip_ids.remove(clip_id)
            self.pending_ui_clip_ids.insert(0, clip_id)
            # Keep the queue small; opening the UI only needs visible first page.
            del self.pending_ui_clip_ids[PAGE_SIZE_HISTORY:]
        self._hidden_refresh_timer.stop()

    def _refresh_hidden_ui_cache(self):
        if self.isVisible() or not self.is_ui_dirty:
            return
        self.browser.active_side = "history"
        self.refresh_lists()
        self.is_ui_dirty = False

    def closeEvent(self, event):
        """Clean up on close."""
        super().closeEvent(event)


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

        self._prepare_clipboard_and_paste(data, 0)
        self._promote_after_clipboard_action(data)

    def _promote_after_clipboard_action(self, data):
        clip_id = data.get("id") if data else None
        if clip_id is None:
            return
        clip_id, _ = self.storage.add_clip(
            data["type"],
            data["content"],
            data.get("tag", ""),
        )
        if data.get("type") == "image" and data.get("is_pinned"):
            self._schedule_hidden_ui_refresh(clip_id)
            return
        self._promote_history_clip_async(clip_id)

    def _promote_history_clip_async(self, clip_id):
        if clip_id is None:
            return
        QTimer.singleShot(0, lambda: self._promote_history_clip(clip_id))

    def _promote_history_clip(self, clip_id):
        if self.isVisible():
            promoted = self.browser.apply_pending_history_updates([clip_id])
            if promoted:
                if clip_id in self.pending_ui_clip_ids:
                    self.pending_ui_clip_ids.remove(clip_id)
                self.is_ui_dirty = False
                return
        self._schedule_hidden_ui_refresh(clip_id)

    def _prepare_clipboard_and_paste(self, data, attempt_index):
        retry_delays = (0, 8, 16, 32)
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
        self.hide()
        # Hide first, then poll until Windows actually gives foreground back to
        # the original target. This avoids a fixed sleep that is sometimes too
        # short for slow apps and unnecessarily long for fast ones.
        QTimer.singleShot(0, self._begin_restore_focus_and_paste)

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

            ok = True
            logger.info(
                "write_clipboard_image clip_id=%s success=%s", data.get("id"), ok
            )
            return ok
        except Exception as e:
            logger.error("write_clipboard_failed: %s", e)
            return False

    def _begin_restore_focus_and_paste(self):
        self._restore_focus_started_at = time.perf_counter()
        self._restore_focus_and_paste()

    def _restore_focus_and_paste(self):
        if sys.platform != "win32":
            self._perform_keyboard_paste()
            return

        user32 = ctypes.windll.user32
        target_hwnd = self.last_active_window_handle
        ready = self._ready_to_paste()
        foreground = user32.GetForegroundWindow()
        elapsed_ms = (time.perf_counter() - self._restore_focus_started_at) * 1000
        logger.info(
            "restore_focus_poll ready=%s target=%s foreground=%s elapsed_ms=%.1f",
            ready,
            target_hwnd,
            foreground,
            elapsed_ms,
        )

        if ready and (not target_hwnd or foreground == target_hwnd):
            self._perform_keyboard_paste()
            return

        if elapsed_ms >= FOCUS_RESTORE_TIMEOUT_MS:
            logger.warning(
                "restore_focus_timeout target=%s foreground=%s elapsed_ms=%.1f",
                target_hwnd,
                foreground,
                elapsed_ms,
            )
            # Last attempt before giving up: ask Windows once more, then paste
            # only if foreground is finally correct.
            if target_hwnd:
                self.restore_foreground_hwnd(target_hwnd)
                if user32.GetForegroundWindow() == target_hwnd:
                    self._perform_keyboard_paste()
                    return
            self._finish_paste_attempt(clear_guard=False)
            return

        if target_hwnd:
            self.restore_foreground_hwnd(target_hwnd)
        QTimer.singleShot(FOCUS_RESTORE_POLL_MS, self._restore_focus_and_paste)

    def _get_focus_window_handle(self, foreground_hwnd):
        if sys.platform != "win32" or not foreground_hwnd:
            return None
        try:
            user32 = ctypes.windll.user32
            thread_id = user32.GetWindowThreadProcessId(foreground_hwnd, None)

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            class GUITHREADINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.wintypes.DWORD),
                    ("flags", ctypes.wintypes.DWORD),
                    ("hwndActive", ctypes.wintypes.HWND),
                    ("hwndFocus", ctypes.wintypes.HWND),
                    ("hwndCapture", ctypes.wintypes.HWND),
                    ("hwndMenuOwner", ctypes.wintypes.HWND),
                    ("hwndMoveSize", ctypes.wintypes.HWND),
                    ("hwndCaret", ctypes.wintypes.HWND),
                    ("rcCaret", RECT),
                ]

            info = GUITHREADINFO()
            info.cbSize = ctypes.sizeof(GUITHREADINFO)
            if user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
                return int(info.hwndFocus or info.hwndCaret or 0) or None
        except Exception as exc:
            logger.debug("get_focus_window_handle_failed: %s", exc)
        return None

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
            self._restore_target_focus(target_hwnd)
            return user32.GetForegroundWindow() == target_hwnd
        return True

    def restore_foreground_hwnd(self, hwnd):
        if not ENABLE_RESTORE_FOCUS_AFTER_BG_CLICK:
            return False
        if sys.platform != "win32" or not hwnd:
            return False
        try:
            user32 = ctypes.windll.user32
            user32.SetForegroundWindow(int(hwnd))
            return user32.GetForegroundWindow() == int(hwnd)
        except Exception as exc:
            logger.debug("restore_foreground_hwnd_failed hwnd=%s error=%s", hwnd, exc)
            return False

    def _restore_target_focus(self, target_hwnd):
        if sys.platform != "win32" or not target_hwnd:
            return True
        user32 = ctypes.windll.user32
        try:
            if hasattr(user32, "IsWindow") and not user32.IsWindow(target_hwnd):
                logger.warning("restore_target_focus_invalid hwnd=%s", target_hwnd)
                return False

            current_foreground = user32.GetForegroundWindow()
            if current_foreground == target_hwnd:
                return True

            if hasattr(user32, "IsIconic") and user32.IsIconic(target_hwnd):
                user32.ShowWindow(target_hwnd, SW_RESTORE)

            kernel32 = ctypes.windll.kernel32
            current_thread = kernel32.GetCurrentThreadId()
            focus_hwnd = getattr(self, "last_focus_window_handle", None)
            if focus_hwnd and hasattr(user32, "IsWindow") and not user32.IsWindow(focus_hwnd):
                focus_hwnd = None

            target_thread = user32.GetWindowThreadProcessId(target_hwnd, None)
            focus_thread = (
                user32.GetWindowThreadProcessId(focus_hwnd, None) if focus_hwnd else 0
            )
            foreground_thread = (
                user32.GetWindowThreadProcessId(current_foreground, None)
                if current_foreground
                else 0
            )
            attached = []

            def attach(thread_id):
                if thread_id and thread_id != current_thread:
                    user32.AttachThreadInput(current_thread, thread_id, True)
                    attached.append(thread_id)

            attach(foreground_thread)
            attach(target_thread)
            attach(focus_thread)
            try:
                if hasattr(user32, "BringWindowToTop"):
                    user32.BringWindowToTop(target_hwnd)
                user32.SetForegroundWindow(target_hwnd)
                if hasattr(user32, "SetActiveWindow"):
                    user32.SetActiveWindow(target_hwnd)
                if hasattr(user32, "SetFocus"):
                    user32.SetFocus(focus_hwnd or target_hwnd)
            finally:
                for thread_id in reversed(attached):
                    user32.AttachThreadInput(current_thread, thread_id, False)

            return user32.GetForegroundWindow() == target_hwnd
        except Exception as exc:
            logger.warning(
                "restore_target_focus_failed hwnd=%s error=%s", target_hwnd, exc
            )
            return False

    def _perform_keyboard_paste(self):
        logger.info(
            "perform_keyboard_paste target_hwnd=%s", self.last_active_window_handle
        )
        target_hwnd = self.last_active_window_handle
        if target_hwnd:
            self._restore_target_focus(target_hwnd)
        try:
            if sys.platform == "win32" and target_hwnd:
                foreground = ctypes.windll.user32.GetForegroundWindow()
                if foreground != target_hwnd:
                    logger.warning(
                        "paste_aborted_focus_mismatch target=%s foreground=%s",
                        target_hwnd,
                        foreground,
                    )
                    self._finish_paste_attempt(clear_guard=False)
                    return
            simulate_paste()
            logger.info("simulate_paste_done target_hwnd=%s", target_hwnd)
        except Exception as exc:
            logger.error("perform_keyboard_paste_failed: %s", exc)
        self._finish_paste_attempt()
        QTimer.singleShot(0, self._reset_ui_after_paste_request)

    def _finish_paste_attempt(self, clear_guard=False):
        self._paste_in_progress = False
        self.input_locked = False
        if clear_guard:
            self.pending_clipboard_guard = None

    def _reset_ui_after_paste_request(self):
        self.search_input.clear()
        self.browser.current_search_query = ""

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
            self.pending_clipboard_guard = None
            return True
        self.pending_clipboard_guard = None
        return False

    def handle_copy_only(self, data):
        self._set_pending_clipboard_guard(data)
        if data["type"] == "text":
            self.clipboard.setText(data["content"])
        else:
            p = os.path.join(IMAGE_DIR, data["content"])
            if os.path.exists(p):
                self._write_clipboard_payload(data)
        self._promote_after_clipboard_action(data)

    def handle_star(self, clip_id, should_pin):
        """Pin or unpin a clip."""
        if should_pin:
            self.storage.pin_clip(clip_id)
        else:
            self.storage.unpin_clip(clip_id)
        self.refresh_lists(force_reset_selection=True)

    def handle_add_tag(self, clip_id, tag):
        """Update tag for a clip."""
        self.storage.update_tag(clip_id, tag)
        self.refresh_lists()

    def handle_set_group(self, clip_id, group_name):
        """Set group for a clip."""
        clip = self.storage.get_clip_by_id(clip_id)
        if clip and not clip.get("is_pinned"):
            self.storage.pin_clip(clip_id)
        self.storage.update_group(clip_id, group_name)
        self.browser.active_side = "pinned"
        self.refresh_lists(force_reset_selection=True)

    def handle_toggle_expand(self, clip_id):
        self.browser.toggle_clip_expanded(clip_id)

    def handle_fix_clip(self, clip_id, new_content):
        if not new_content:
            raise ValueError("Clip content cannot be empty.")
        if not new_content.strip():
            raise ValueError("Clip content cannot be blank.")
        self.storage.update_clip_content(clip_id, new_content)
        self.browser.active_side = "pinned"
        self.refresh_lists()

    def handle_delete(self, clip_id):
        """Delete a clip, coalescing rapid delete clicks into one backend flush."""
        if not clip_id or clip_id in self._pending_delete_clip_ids:
            return
        self._pending_delete_clip_ids.add(clip_id)
        self.browser.remove_clip_from_ui(clip_id)
        if not self._delete_flush_timer.isActive():
            self._delete_flush_timer.start(25)

    def _flush_pending_deletes(self):
        clip_ids = list(self._pending_delete_clip_ids)
        if not clip_ids:
            return
        self._pending_delete_clip_ids.clear()
        for clip_id in clip_ids:
            self.storage.delete_clip(clip_id)
        self.refresh_lists()
        self.browser.reset_after_delete_refresh()


    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.hide()
        super().keyPressEvent(e)

    def on_clipboard_change_delayed(self):
        """Coalesce clipboard format bursts before reading the final payload."""
        if not self._clipboard_capture_timer.isActive():
            self._last_clipboard_capture_at = time.perf_counter()
        self._clipboard_capture_timer.start()

    def _process_clipboard_data(self):
        self._process_clipboard_data_retry(0)

    def _process_clipboard_data_retry(self, attempt_index):
        retry_delays = (5, 15, 35, 75)
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
        job = None
        pending_key = None
        if mime.hasImage():
            image = QImage(mime.imageData()).copy()
            if not image.isNull():
                job = CaptureJob.image(image)
        elif mime.hasText():
            text = mime.text()
            if text and text.strip():
                job = CaptureJob.text(text)
                pending_key = ("text", text)
        if job is None:
            return

        now = time.monotonic()
        if pending_key in self._pending_clipboard_keys:
            logger.debug("clipboard_ingest_skipped reason=duplicate_pending")
            return
        if (
            pending_key is not None
            and pending_key == self._last_ingested_clipboard_key
            and now - self._last_ingested_clipboard_at < 1.0
        ):
            logger.debug("clipboard_ingest_skipped reason=duplicate_burst")
            return
        if pending_key is not None:
            self._pending_clipboard_keys.add(pending_key)
        if not self.ingest_bridge.submit(job):
            if pending_key is not None:
                self._pending_clipboard_keys.discard(pending_key)
            logger.warning("clipboard_ingest_rejected reason=queue_full")

    def _on_clipboard_ingest_completed(self, result):
        clipboard_key = (result.clip_type, result.content)
        self._pending_clipboard_keys.discard(clipboard_key)
        self._last_ingested_clipboard_key = clipboard_key
        self._last_ingested_clipboard_at = time.monotonic()
        clip_id = result.clip_id
        if self.isVisible():
            self.browser.apply_pending_history_updates([clip_id]) or self.refresh_lists()
        else:
            if self.browser.apply_pending_history_updates([clip_id]):
                self.pending_ui_clip_ids.clear()
                self._requires_full_ui_refresh = False
                self.is_ui_dirty = False
            else:
                self._schedule_hidden_ui_refresh(clip_id)
        logger.info(
            "clipboard_ingest_done clip_id=%s is_new=%s elapsed_ms=%.2f",
            clip_id,
            result.is_new,
            (time.perf_counter() - self._last_clipboard_capture_at) * 1000,
        )

    def _on_clipboard_ingest_rejected(self, result):
        if result.clip_type == "text":
            self._pending_clipboard_keys.discard(("text", result.content))
        logger.warning(
            "clipboard_ingest_rejected reason=%s byte_count=%s pixel_count=%s",
            result.reason,
            result.byte_count,
            result.pixel_count,
        )

    def handle_move(self, clip_id, direction, is_pinned):
        """Move clip up/down."""
        self.storage.move_clip(clip_id, direction, is_pinned)
        self.refresh_lists()

    def hideEvent(self, event):
        super().hideEvent(event)

    def changeEvent(self, e):
        if e.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            self.hide()
        super().changeEvent(e)


def main():

    logger.info("main_start pid=%s", os.getpid())
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
    app.setPalette(palette)

    coordinator = SingleInstanceCoordinator()
    if not coordinator.acquire_or_notify():
        coordinator.close()
        logger.info("secondary_instance_notified_primary")
        _fault_log_handle.flush()
        return 0

    window = ClientApp()
    coordinator.activate_requested.connect(window.activate_from_secondary)
    exit_code = app.exec()
    coordinator.close()
    logger.info("main_exit exit_code=%s", exit_code)
    _fault_log_handle.flush()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

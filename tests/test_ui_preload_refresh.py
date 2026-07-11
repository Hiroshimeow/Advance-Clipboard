import os
import sys
import time
import unittest
import ctypes
import types
from unittest.mock import MagicMock
from unittest.mock import patch


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if not hasattr(ctypes, "WINFUNCTYPE"):
    ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE

_clipboard_monitor_mod = types.ModuleType("core.clipboard_monitor")
_clipboard_monitor_mod.Win32ClipboardMonitor = MagicMock()
_clipboard_monitor_mod.VK_CONTROL = 0x11
_clipboard_monitor_mod.VK_MENU = 0x12
_clipboard_monitor_mod.simulate_paste = MagicMock()
sys.modules["core.clipboard_monitor"] = _clipboard_monitor_mod

from PyQt6.QtCore import QMimeData
from PyQt6.QtWidgets import QApplication

import main
from main import ClientApp


_APP: QApplication | None = None


def _get_qapp() -> QApplication:
    global _APP
    if _APP is not None:
        return _APP

    inst = QApplication.instance()
    if isinstance(inst, QApplication):
        _APP = inst
        return _APP

    _APP = QApplication([])
    _APP.setQuitOnLastWindowClosed(False)
    return _APP


def _wait_until(predicate, timeout_ms=1200):
    end = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    QApplication.processEvents()
    return bool(predicate())


class _StubStorage:
    def __init__(self):
        self.added = []
        self.need_backup = False
        self._clips = []

    def is_db_valid(self):
        return True

    def get_clip_count(self):
        return 0


    def set_backup_callback(self, callback):
        self.backup_callback = callback

    def clear_backup_flag(self):
        self.need_backup = False

    def add_clip(self, clip_type, content, tag=""):
        clip_id = len(self.added) + 1
        self.added.append((clip_type, content, tag))
        self._clips.insert(0, {"id": clip_id, "type": clip_type, "content": content, "tag": tag})
        return clip_id, True

    def search_history(self, query, limit=None, ranked=True):
        return []

    def get_history(self, limit=20, offset=0):
        return self._clips[offset : offset + limit]

    def search_pinned(self, query, limit=None, ranked=True):
        return []

    def get_groups(self):
        return []

    def get_ungrouped_pinned(self, limit=50, offset=0):
        return []

    def get_clips_by_group(self, group_name):
        return []

    def get_clip_by_id(self, clip_id):
        for clip in self._clips:
            if clip["id"] == clip_id:
                return dict(clip)
        return None


class UiPreloadRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_qapp()

    def _make_app(self):
        self.storage = _StubStorage()
        storage_patch = patch.object(main, "get_storage", return_value=self.storage)
        storage_patch.start()
        self.addCleanup(storage_patch.stop)
        app = ClientApp(enable_monitor=False, init_data=False)
        self.addCleanup(app.backup_scheduler.cancel)
        self.addCleanup(app.close)
        return app

    def test_hidden_clipboard_update_preloads_history_row_without_full_open_refresh(self):
        app = self._make_app()
        mime = QMimeData()
        mime.setText("fresh clip")

        refresh_calls = []

        def fake_refresh(force_reset_selection=False):
            refresh_calls.append(force_reset_selection)

        app.refresh_lists = fake_refresh
        app.clipboard.mimeData = lambda: mime

        app._process_clipboard_data_retry(0)

        QApplication.processEvents()
        self.assertEqual([], refresh_calls)
        self.assertFalse(app.is_ui_dirty)
        self.assertFalse(app._requires_full_ui_refresh)
        self.assertEqual(app.list_history.count(), 1)
        item = app.list_history.item(0)
        self.assertEqual(item.data(main.Qt.ItemDataRole.UserRole)["content"], "fresh clip")

    def test_show_schedules_single_refresh_when_hidden_update_is_dirty(self):
        app = self._make_app()
        app.is_ui_dirty = True
        app.pending_ui_clip_ids = [1]

        single_shot_calls = []
        original_single_shot = main.QTimer.singleShot

        def tracking_single_shot(delay, callback):
            single_shot_calls.append((delay, getattr(callback, "__name__", repr(callback))))
            return original_single_shot(delay, callback)

        with patch.object(main.QTimer, "singleShot", side_effect=tracking_single_shot):
            app.show_at_cursor()
            QApplication.processEvents()

        refresh_delays = [
            delay
            for delay, callback_name in single_shot_calls
            if callback_name == "_refresh_after_show"
        ]
        self.assertEqual([50], refresh_delays)

    def test_clipboard_change_events_are_coalesced(self):
        app = self._make_app()
        calls = []
        app._process_clipboard_data_retry = lambda attempt: calls.append(attempt)

        app.on_clipboard_change_delayed()
        app.on_clipboard_change_delayed()
        app.on_clipboard_change_delayed()

        self.assertTrue(_wait_until(lambda: len(calls) == 1))
        self.assertEqual([0], calls)

    def test_duplicate_clipboard_payload_is_ingested_once_per_burst(self):
        app = self._make_app()
        mime = QMimeData()
        mime.setText("same clipboard payload")
        app.clipboard.mimeData = lambda: mime

        app._process_clipboard_data_retry(0)
        app._process_clipboard_data_retry(0)

        self.assertEqual(
            [("text", "same clipboard payload", "")],
            self.storage.added,
        )


if __name__ == "__main__":
    unittest.main()

import os
import sys
import time
import unittest
from unittest.mock import patch


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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

    def is_db_valid(self):
        return True

    def get_clip_count(self):
        return 0

    def trigger_daily_rebuild(self):
        pass

    def set_backup_callback(self, callback):
        self.backup_callback = callback

    def set_neural_event_callback(self, callback):
        self.neural_callback = callback

    def clear_backup_flag(self):
        self.need_backup = False

    def add_clip(self, clip_type, content, tag=""):
        clip_id = len(self.added) + 1
        self.added.append((clip_type, content, tag))
        return clip_id, True

    def search_history(self, query):
        return []

    def get_history(self, limit=20, offset=0):
        return []

    def search_pinned(self, query):
        return []

    def get_groups(self):
        return []

    def get_ungrouped_pinned(self, limit=50, offset=0):
        return []


class UiPreloadRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_qapp()

    def _make_app(self):
        self.storage = _StubStorage()
        storage_patch = patch.object(main, "get_storage", return_value=self.storage)
        storage_patch.start()
        self.addCleanup(storage_patch.stop)
        app = ClientApp(
            enable_monitor=False,
            init_data=False,
            enable_background_jobs=False,
        )
        self.addCleanup(app.backup_scheduler.cancel)
        self.addCleanup(app.close)
        return app

    def test_hidden_clipboard_update_preloads_refresh_before_show(self):
        app = self._make_app()
        mime = QMimeData()
        mime.setText("fresh clip")

        refresh_calls = []

        def fake_refresh(force_reset_selection=False):
            refresh_calls.append(force_reset_selection)

        app.refresh_lists = fake_refresh
        app.clipboard.mimeData = lambda: mime

        app._process_clipboard_data_retry(0)

        self.assertTrue(_wait_until(lambda: len(refresh_calls) == 1))
        self.assertEqual([False], refresh_calls)
        self.assertFalse(app.is_ui_dirty)

    def test_show_does_not_schedule_extra_refresh_after_preload(self):
        app = self._make_app()
        app.is_ui_dirty = False

        single_shot_calls = []
        original_single_shot = main.QTimer.singleShot

        def tracking_single_shot(delay, callback):
            single_shot_calls.append((delay, getattr(callback, "__name__", repr(callback))))
            return original_single_shot(delay, callback)

        with patch.object(main.QTimer, "singleShot", side_effect=tracking_single_shot):
            app.show_at_cursor()
            QApplication.processEvents()

        self.assertFalse(any(delay == 25 for delay, _ in single_shot_calls))


if __name__ == "__main__":
    unittest.main()

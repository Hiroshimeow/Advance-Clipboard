import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.modules.setdefault("core.clipboard_monitor", MagicMock())



with patch("ctypes.WINFUNCTYPE", create=True, new=MagicMock()), \
    patch("ctypes.windll", create=True, new=MagicMock()):
    import main
    from main import ClientApp

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

_APP = None


def _get_qapp():
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


class _StubStorage:
    def __init__(self, clips):
        self.clips = {clip["id"]: dict(clip) for clip in clips}
        self.need_backup = False

    def is_db_valid(self):
        return True

    def get_clip_count(self):
        return len(self.clips)

    def trigger_daily_rebuild(self):
        pass

    def set_backup_callback(self, callback):
        self.backup_callback = callback

    def clear_backup_flag(self):
        self.need_backup = False

    def get_history(self, limit=20, offset=0):
        ordered = list(self.clips.values())
        return ordered[offset : offset + limit]

    def get_clip_by_id(self, clip_id):
        clip = self.clips.get(clip_id)
        return dict(clip) if clip else None

    def search_history(self, query):
        return []

    def search_pinned(self, query):
        return []

    def get_groups(self):
        return []

    def get_ungrouped_pinned(self, limit=50, offset=0):
        return []


class CopyPastePromotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_qapp()

    def _make_app(self, clips=None):
        clips = clips or [
            {"id": 1, "type": "text", "content": "older"},
            {"id": 2, "type": "text", "content": "newer"},
        ]
        self.storage = _StubStorage(clips)
        storage_patch = patch.object(main, "get_storage", return_value=self.storage)
        storage_patch.start()
        self.addCleanup(storage_patch.stop)
        app = ClientApp(enable_monitor=False, init_data=False)
        self.addCleanup(app.backup_scheduler.cancel)
        self.addCleanup(app.close)
        app.refresh_lists()
        return app

    def _history_ids(self, app):
        ids = []
        for row in range(app.list_history.count()):
            item = app.list_history.item(row)
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and "id" in data:
                ids.append(data["id"])
        return ids

    def test_copy_promotes_existing_history_item_to_top(self):
        app = self._make_app()
        app.show()
        QApplication.processEvents()

        self.assertEqual([1, 2], self._history_ids(app))

        with patch.object(main.QTimer, "singleShot", side_effect=lambda delay, cb: cb()):
            app.handle_copy_only({"id": 2, "type": "text", "content": "newer"})

        self.assertEqual([2, 1], self._history_ids(app))
        self.assertEqual(0, app.list_history.currentRow())

    def test_paste_schedules_promotion_after_paste_request(self):
        app = self._make_app()
        events = []

        def fake_prepare(data, attempt_index):
            events.append(("prepare", data["id"], attempt_index))

        def fake_timer(delay, callback):
            events.append(("timer", delay))
            callback()

        def fake_promote(clip_id):
            events.append(("promote", clip_id))

        with patch.object(app, "_prepare_clipboard_and_paste", side_effect=fake_prepare), \
            patch.object(app, "_promote_history_clip", side_effect=fake_promote), \
            patch.object(main.QTimer, "singleShot", side_effect=fake_timer):
            app.handle_paste({"id": 2, "type": "text", "content": "newer"})

        self.assertEqual(
            [("prepare", 2, 0), ("timer", 0), ("promote", 2)],
            events,
        )

    def test_pinned_image_paste_defers_history_promotion_until_after_paste(self):
        app = self._make_app(
            clips=[{"id": 9, "type": "image", "content": "missing-test-image.png", "is_pinned": 1}]
        )
        events = []

        with patch.object(app, "_prepare_clipboard_and_paste", side_effect=lambda data, attempt: events.append(("prepare", data["id"], attempt))), \
            patch.object(app, "_schedule_hidden_ui_refresh", side_effect=lambda clip_id: events.append(("dirty", clip_id))), \
            patch.object(app.browser, "apply_pending_history_updates", side_effect=lambda clip_ids: events.append(("apply", list(clip_ids))) or True):
            app.handle_paste({"id": 9, "type": "image", "content": "missing-test-image.png", "is_pinned": 1})

        self.assertEqual([("prepare", 9, 0), ("dirty", 9)], events)

    def test_image_clipboard_write_does_not_hash_roundtrip_image_data(self):
        app = self._make_app()
        class _FakeMime:
            def hasImage(self):
                return True

        class _FakeClipboard:
            def setPixmap(self, pixmap):
                self.pixmap = pixmap

            def mimeData(self):
                return _FakeMime()

        app.clipboard = _FakeClipboard()
        with patch("main.os.path.exists", return_value=True), \
            patch("main.QPixmap") as pixmap_cls, \
            patch.object(app, "_image_storage_name", side_effect=AssertionError("image hash verification should not run")):
            pixmap_cls.return_value.isNull.return_value = False
            self.assertTrue(app._write_clipboard_payload({"id": 10, "type": "image", "content": "fake.png"}))

    def test_image_clipboard_guard_does_not_hash_roundtrip_image_data(self):
        app = self._make_app()

        class _FakeMime:
            def hasImage(self):
                return True

            def hasText(self):
                return False

        app._set_pending_clipboard_guard({"id": 11, "type": "image", "content": "fake.png"})
        with patch.object(app, "_image_storage_name", side_effect=AssertionError("image guard hash should not run")):
            self.assertTrue(app._should_ignore_clipboard_update(_FakeMime()))

    def test_hidden_promotion_marks_ui_dirty_for_next_show(self):
        app = self._make_app()
        app.hide()

        with patch.object(main.QTimer, "singleShot", side_effect=lambda delay, cb: cb()):
            app.handle_copy_only({"id": 2, "type": "text", "content": "newer"})

        self.assertTrue(app.is_ui_dirty)
        self.assertEqual([2], app.pending_ui_clip_ids)


if __name__ == "__main__":
    unittest.main()

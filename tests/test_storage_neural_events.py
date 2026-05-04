import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

# Add project root to path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

import storage as storage_module
from storage import db as storage_db


class StorageNeuralEventTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = storage_db.DB_FILE
        self.old_local = storage_db._local
        storage_db.DB_FILE = os.path.join(self.tmp.name, "test_clipboard.db")
        storage_db._local = type("Local", (), {})()
        self.storage = storage_module.ClipboardStorage()
        self.callback = MagicMock()
        self.storage.set_neural_event_callback(self.callback)

    def tearDown(self):
        conn = getattr(storage_db._local, "conn", None)
        if conn is not None:
            conn.close()
            storage_db._local.conn = None
        storage_db.DB_FILE = self.old_db
        storage_db._local = self.old_local
        self.tmp.cleanup()

    def test_add_clip_emits_new_clip_event_only_for_new_insert(self):
        clip_id, is_new = self.storage.add_clip("text", "hello world")
        self.assertTrue(is_new)
        self.callback.assert_called_with("new_clip", clip_id)

        self.callback.reset_mock()
        same_id, is_new = self.storage.add_clip("text", "hello world")
        self.assertFalse(is_new)
        self.assertEqual(clip_id, same_id)
        self.callback.assert_not_called()

    def test_recopied_pinned_clip_still_appears_in_recent_history(self):
        clip_id, _ = self.storage.add_clip("text", "reused pinned")
        self.storage.pin_clip(clip_id)

        same_id, is_new = self.storage.add_clip("text", "reused pinned")

        self.assertEqual(clip_id, same_id)
        self.assertFalse(is_new)
        history_ids = [clip["id"] for clip in self.storage.get_history()]
        pinned_ids = [clip["id"] for clip in self.storage.get_pinned()]
        self.assertIn(clip_id, history_ids)
        self.assertIn(clip_id, pinned_ids)

    def test_pin_and_unpin_emit_priority_event(self):
        clip_id, _ = self.storage.add_clip("text", "pin me")
        self.callback.reset_mock()

        self.storage.pin_clip(clip_id)
        self.callback.assert_called_with("pin_state_changed", clip_id)

        self.callback.reset_mock()
        self.storage.unpin_clip(clip_id)
        self.callback.assert_called_with("pin_state_changed", clip_id)


if __name__ == "__main__":
    unittest.main()

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

        history_ids_before = [clip["id"] for clip in self.storage.get_history()]
        self.assertNotIn(clip_id, history_ids_before)

        same_id, is_new = self.storage.add_clip("text", "reused pinned")

        self.assertEqual(clip_id, same_id)
        self.assertFalse(is_new)
        history_ids = [clip["id"] for clip in self.storage.get_history()]
        pinned_ids = [clip["id"] for clip in self.storage.get_pinned()]
        self.assertIn(clip_id, history_ids)
        self.assertIn(clip_id, pinned_ids)

    def test_unpinned_clip_returns_to_history(self):
        clip_id, _ = self.storage.add_clip("text", "temporary pin")
        self.storage.pin_clip(clip_id)
        self.storage.unpin_clip(clip_id)

        history_ids = [clip["id"] for clip in self.storage.get_history()]
        self.assertIn(clip_id, history_ids)

    def test_search_history_uses_same_pinned_visibility_rule(self):
        hidden_id, _ = self.storage.add_clip("text", "hidden pinned text")
        self.storage.pin_clip(hidden_id)

        visible_id, _ = self.storage.add_clip("text", "visible pinned text")
        self.storage.pin_clip(visible_id)
        self.storage.add_clip("text", "visible pinned text")

        results = self.storage.search_history("pinned text", limit=20)
        result_ids = [row["id"] for row in results]
        self.assertIn(visible_id, result_ids)
        self.assertNotIn(hidden_id, result_ids)

    def test_migration_sets_pinned_at_for_existing_pinned_rows(self):
        old_db = storage_db.DB_FILE
        old_local = storage_db._local
        legacy_path = os.path.join(self.tmp.name, "legacy_clipboard.db")
        storage_db.DB_FILE = legacy_path
        storage_db._local = type("Local", (), {})()
        try:
            conn = storage_db.get_connection()
            conn.execute(
                """CREATE TABLE clips (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL CHECK(type IN ('text', 'image')),
                    content TEXT NOT NULL,
                    hash TEXT NOT NULL UNIQUE,
                    tag TEXT DEFAULT '',
                    group_name TEXT DEFAULT '',
                    is_pinned INTEGER DEFAULT 0,
                    pin_order INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                """INSERT INTO clips
                   (type, content, hash, tag, group_name, is_pinned, pin_order, created_at, updated_at)
                   VALUES (?, ?, ?, '', '', 1, 1, ?, ?)""",
                (
                    "text",
                    "legacy pinned",
                    storage_module.ClipboardStorage.compute_hash("legacy pinned"),
                    "2026-01-01",
                    "2026-01-02",
                ),
            )
            conn.commit()
            conn.close()
            storage_db._local.conn = None

            migrated = storage_module.ClipboardStorage()
            legacy = migrated.get_pinned()[0]
            self.assertEqual(legacy.get("pinned_at"), "2026-01-02")
            history_ids = [clip["id"] for clip in migrated.get_history()]
            self.assertNotIn(legacy["id"], history_ids)
        finally:
            conn = getattr(storage_db._local, "conn", None)
            if conn is not None:
                conn.close()
                storage_db._local.conn = None
            storage_db.DB_FILE = old_db
            storage_db._local = old_local

    def test_update_clip_content_rejects_duplicate_content(self):
        first_id, _ = self.storage.add_clip("text", "first content")
        second_id, _ = self.storage.add_clip("text", "second content")
        self.storage.pin_clip(first_id)

        with self.assertRaises(ValueError):
            self.storage.update_clip_content(first_id, "second content")

        clip = self.storage.get_clip_by_id(first_id)
        self.assertEqual(clip["content"], "first content")

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

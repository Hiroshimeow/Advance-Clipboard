import os
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

import storage as storage_module
from storage import db as storage_db


class StorageRetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = storage_db.DB_FILE
        self.old_local = storage_db._local
        storage_db.DB_FILE = os.path.join(self.tmp.name, "test_clipboard.db")
        storage_db._local = type("Local", (), {})()
        self.storage = storage_module.ClipboardStorage()

    def tearDown(self):
        conn = getattr(storage_db._local, "conn", None)
        if conn is not None:
            conn.close()
            storage_db._local.conn = None
        storage_db.DB_FILE = self.old_db
        storage_db._local = self.old_local
        self.tmp.cleanup()

    def test_prune_history_keeps_newest_unpinned_only(self):
        ids = []
        for i in range(5):
            cid, _ = self.storage.add_clip("text", f"history {i}")
            ids.append(cid)

        deleted = self.storage.prune_history(3)

        self.assertEqual(deleted, 2)
        history_ids = [clip["id"] for clip in self.storage.get_history(limit=10)]
        self.assertEqual(history_ids, list(reversed(ids[-3:])))
        self.assertIsNone(self.storage.get_clip_by_id(ids[0]))
        self.assertIsNone(self.storage.get_clip_by_id(ids[1]))

    def test_prune_history_never_deletes_pinned_clips(self):
        old_history_id, _ = self.storage.add_clip("text", "old history")
        pinned_id, _ = self.storage.add_clip("text", "protected pinned")
        self.storage.pin_clip(pinned_id)
        for i in range(3):
            self.storage.add_clip("text", f"new history {i}")

        deleted = self.storage.prune_history(3)

        self.assertEqual(deleted, 1)
        self.assertIsNone(self.storage.get_clip_by_id(old_history_id))
        pinned = self.storage.get_clip_by_id(pinned_id)
        self.assertIsNotNone(pinned)
        self.assertEqual(pinned["is_pinned"], 1)

    def test_prune_history_removes_neural_orphans(self):
        ids = []
        for i in range(4):
            cid, _ = self.storage.add_clip("text", f"clip {i}")
            ids.append(cid)
        conn = storage_db.get_connection()
        conn.execute("INSERT OR REPLACE INTO neural_vectors (clip_id, vector) VALUES (?, ?)", (ids[0], b"abc"))
        conn.execute("INSERT OR REPLACE INTO neural_links (source_id, target_id, weight) VALUES (?, ?, ?)", (ids[0], ids[-1], 0.5))
        conn.commit()

        self.storage.prune_history(3)

        self.assertIsNone(conn.execute("SELECT 1 FROM neural_vectors WHERE clip_id = ?", (ids[0],)).fetchone())
        self.assertIsNone(conn.execute("SELECT 1 FROM neural_links WHERE source_id = ? OR target_id = ?", (ids[0], ids[0])).fetchone())


if __name__ == "__main__":
    unittest.main()

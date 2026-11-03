import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))


class ClipContentUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "clipboard.db"
        patcher = patch("storage.db.DB_FILE", str(self.db_path))
        patcher.start()
        self.addCleanup(patcher.stop)

        import storage.db as db

        db._local = type("Local", (), {})()
        from storage import ClipboardStorage

        self.storage = ClipboardStorage()

    def tearDown(self):
        import storage.db as db

        conn = getattr(db._local, "conn", None)
        if conn is not None:
            conn.close()
            db._local.conn = None
        self.tmp.cleanup()

    def test_pinned_edit_replaces_duplicate_history_row(self):
        pinned_id, _ = self.storage.add_clip("text", "old pinned", "important")
        self.storage.pin_clip(pinned_id)
        self.storage.update_group(pinned_id, "commands")
        history_id, _ = self.storage.add_clip("text", "edited content")

        self.assertTrue(self.storage.update_clip_content(pinned_id, "edited content"))

        pinned = self.storage.get_clip_by_id(pinned_id)
        self.assertIsNotNone(pinned)
        self.assertEqual(pinned["content"], "edited content")
        self.assertTrue(pinned["is_pinned"])
        self.assertEqual(pinned["tag"], "important")
        self.assertEqual(pinned["group_name"], "commands")
        self.assertIsNone(self.storage.get_clip_by_id(history_id))

    def test_pinned_edit_still_blocks_duplicate_pinned_row(self):
        first_id, _ = self.storage.add_clip("text", "first")
        second_id, _ = self.storage.add_clip("text", "second")
        self.storage.pin_clip(first_id)
        self.storage.pin_clip(second_id)

        with self.assertRaisesRegex(ValueError, "Another pinned clip"):
            self.storage.update_clip_content(first_id, "second")

        self.assertEqual(self.storage.get_clip_by_id(first_id)["content"], "first")
        self.assertEqual(self.storage.get_clip_by_id(second_id)["content"], "second")


if __name__ == "__main__":
    unittest.main()

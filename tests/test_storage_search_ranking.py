import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))


class StorageSearchRankingTests(unittest.TestCase):
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
        self.tmp.cleanup()

    def _insert_clip(self, content, updated_at, *, pinned=False, tag="", group=""):
        clip_id, is_new = self.storage.add_clip("text", content, tag)
        if pinned:
            self.storage.pin_clip(clip_id)
        self.storage.update_group(clip_id, group)
        conn = self.storage.clips.get_clip_by_id  # keep import side-effect simple
        import storage.db as db
        with db.transaction() as sql:
            sql.execute(
                "UPDATE clips SET updated_at = ?, tag = ?, group_name = ? WHERE id = ?",
                (updated_at.isoformat(), tag, group, clip_id),
            )
        return clip_id

    def test_recent_matching_history_clip_wins_tie(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        old_id = self._insert_clip("deploy server config alpha", base)
        recent_id = self._insert_clip("deploy server config beta", base + timedelta(minutes=5))

        rows = self.storage.search_history("deploy server", limit=10, semantic=False)

        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], recent_id)
        self.assertEqual(rows[1]["id"], old_id)

    def test_exact_match_beats_recent_partial_match(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        exact_id = self._insert_clip("token", base)
        self._insert_clip("token renewal script for production", base + timedelta(minutes=5))

        rows = self.storage.search_history("token", limit=10, semantic=False)

        self.assertEqual(rows[0]["id"], exact_id)


if __name__ == "__main__":
    unittest.main()

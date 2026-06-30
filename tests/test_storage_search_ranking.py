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
        import storage.db as db

        conn = getattr(db._local, "conn", None)
        if conn is not None:
            conn.close()
            db._local.conn = None
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

        rows = self.storage.search_history("deploy server", limit=10, ranked=False)

        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], recent_id)
        self.assertEqual(rows[1]["id"], old_id)

    def test_exact_match_beats_recent_partial_match(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        exact_id = self._insert_clip("token", base)
        self._insert_clip("token renewal script for production", base + timedelta(minutes=5))

        rows = self.storage.search_history("token", limit=10, ranked=False)

        self.assertEqual(rows[0]["id"], exact_id)

    def test_history_search_includes_tag_and_group_metadata(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        tagged_id = self._insert_clip("unrelated body", base, tag="linux", group="workspace tools")

        tag_rows = self.storage.search_history("linux", limit=10, ranked=False)
        group_rows = self.storage.search_history("workspace", limit=10, ranked=False)

        self.assertEqual([row["id"] for row in tag_rows], [tagged_id])
        self.assertEqual([row["id"] for row in group_rows], [tagged_id])

    def test_tag_prefix_search_filters_to_tag_only_and_prefers_recent(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        older = self._insert_clip("body mentions proxy", base, tag="proxy")
        newer = self._insert_clip("unrelated body", base + timedelta(minutes=5), tag="proxy-tools")
        self._insert_clip("tag word only in content", base + timedelta(minutes=10), tag="")

        rows = self.storage.search_history("tag proxy", limit=10, ranked=False)

        self.assertEqual([row["id"] for row in rows], [newer, older])

    def test_tags_prefix_search_matches_partial_tag_keyword(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        tagged_id = self._insert_clip("unrelated body", base, tag="workspace-tools")
        self._insert_clip("workspace-tools appears only in body", base + timedelta(minutes=5), tag="")

        rows = self.storage.search_history("tags work", limit=10, ranked=False)

        self.assertEqual([row["id"] for row in rows], [tagged_id])

    def test_literal_like_wildcards_do_not_match_everything(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        literal_id = self._insert_clip("literal 100% proxy_token", base)
        self._insert_clip("ordinary unrelated clip", base + timedelta(minutes=5))

        rows = self.storage.search_history("%", limit=10, ranked=False)

        self.assertEqual([row["id"] for row in rows], [literal_id])


if __name__ == "__main__":
    unittest.main()

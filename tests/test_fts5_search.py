import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


class FTS5MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "clipboard.db"
        self.db_patch = patch("storage.db.DB_FILE", str(self.db_path))
        self.db_patch.start()
        import storage.db as db
        db._local = threading.local()
        self.db = db

    def tearDown(self):
        conn = getattr(self.db._local, "conn", None)
        if conn is not None:
            conn.close()
            self.db._local.conn = None
        self.db_patch.stop()
        self.tmp.cleanup()

    def _create_v2_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE clips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL CHECK(type IN ('text', 'image')),
                content TEXT NOT NULL,
                hash TEXT NOT NULL UNIQUE,
                tag TEXT DEFAULT '',
                group_name TEXT DEFAULT '',
                is_pinned INTEGER DEFAULT 0,
                pin_order INTEGER DEFAULT 0,
                pinned_at TEXT DEFAULT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX idx_pinned ON clips(is_pinned);
            CREATE INDEX idx_updated ON clips(updated_at DESC);
            CREATE INDEX idx_group ON clips(group_name);
            INSERT INTO clips
              (type, content, hash, tag, group_name, created_at, updated_at)
            VALUES
              ('text', 'deploy preserved body', 'h1', 'legacy-tag', 'ops-group', '2026-01-01', '2026-01-01');
            PRAGMA user_version = 2;
        """)
        conn.commit()
        conn.close()

    def test_v2_migration_preserves_rows_and_builds_fts(self):
        self._create_v2_db()
        before_conn = sqlite3.connect(self.db_path)
        before = before_conn.execute(
            "SELECT id,type,content,hash,tag,group_name,is_pinned,pin_order,pinned_at,created_at,updated_at FROM clips"
        ).fetchall()
        before_conn.close()

        self.db.init_db()
        conn = self.db.get_connection()
        after = conn.execute(
            "SELECT id,type,content,hash,tag,group_name,is_pinned,pin_order,pinned_at,created_at,updated_at FROM clips"
        ).fetchall()
        objects = {
            (row[0], row[1])
            for row in conn.execute(
                "SELECT name,type FROM sqlite_master WHERE name IN ('clips_fts','clips_fts_ai','clips_fts_ad','clips_fts_au')"
            )
        }

        self.assertEqual(before, [tuple(row) for row in after])
        self.assertEqual(3, conn.execute("PRAGMA user_version").fetchone()[0])
        self.assertIn(("clips_fts", "table"), objects)
        self.assertEqual(
            {"clips_fts_ai", "clips_fts_ad", "clips_fts_au"},
            {name for name, kind in objects if kind == "trigger"},
        )
        self.assertEqual(
            [1],
            [row[0] for row in conn.execute("SELECT rowid FROM clips_fts WHERE clips_fts MATCH ?", ('"ploy"',))],
        )

    def test_fts_triggers_follow_insert_update_delete_and_reopen_is_idempotent(self):
        self.db.init_db()
        conn = self.db.get_connection()
        conn.execute(
            "INSERT INTO clips(type,content,hash,tag,group_name,created_at,updated_at) VALUES ('text','alpha deploy','h1','work','server','2026','2026')"
        )
        clip_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        self.assertEqual(clip_id, conn.execute("SELECT rowid FROM clips_fts WHERE clips_fts MATCH ?", ('"ploy"',)).fetchone()[0])

        conn.execute("UPDATE clips SET content='changed body', tag='proxy-tools', group_name='ops' WHERE id=?", (clip_id,))
        conn.commit()
        self.assertIsNone(conn.execute("SELECT rowid FROM clips_fts WHERE clips_fts MATCH ?", ('"ploy"',)).fetchone())
        self.assertEqual(clip_id, conn.execute("SELECT rowid FROM clips_fts WHERE clips_fts MATCH ?", ('tag : "proxy"',)).fetchone()[0])

        conn.execute("ANALYZE")
        conn.execute("UPDATE sqlite_stat1 SET stat='sentinel' WHERE tbl='clips' AND idx='idx_updated'")
        conn.commit()
        self.db.init_db()
        self.assertEqual('sentinel', conn.execute("SELECT stat FROM sqlite_stat1 WHERE tbl='clips' AND idx='idx_updated'").fetchone()[0])

        conn.execute("DELETE FROM clips WHERE id=?", (clip_id,))
        conn.commit()
        self.assertIsNone(conn.execute("SELECT rowid FROM clips_fts WHERE clips_fts MATCH ?", ('tag : "proxy"',)).fetchone())


if __name__ == "__main__":
    unittest.main()

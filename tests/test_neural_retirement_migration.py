import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


CURRENT_SCHEMA_VERSION = 3
REQUIRED_INDEXES = {"idx_pinned", "idx_updated", "idx_group"}


class NeuralRetirementMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "clipboard.db"
        self.db_patch = patch("storage.db.DB_FILE", str(self.db_path))
        self.db_patch.start()

        import storage.db as db

        db._local = threading.local()
        self.db = db

    def tearDown(self):
        self._close_thread_connection()
        self.db_patch.stop()
        self.tmp.cleanup()

    def _close_thread_connection(self):
        conn = getattr(self.db._local, "conn", None)
        if conn is not None:
            conn.close()
            self.db._local.conn = None

    def _create_legacy_db(self, *, user_version=1):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
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

            CREATE TABLE neural_vectors (
                clip_id INTEGER PRIMARY KEY,
                vector BLOB
            );
            CREATE TABLE neural_links (
                source_id INTEGER,
                target_id INTEGER,
                weight REAL,
                PRIMARY KEY (source_id, target_id)
            );
            """
        )
        conn.execute(
            """INSERT INTO clips
               (type, content, hash, tag, group_name, is_pinned, pin_order,
                pinned_at, created_at, updated_at)
               VALUES ('text', 'preserved clip', 'legacy-hash', 'legacy', 'ops', 1, 3,
                       '2026-01-01T00:00:00', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"""
        )
        conn.execute("INSERT INTO neural_vectors (clip_id, vector) VALUES (1, ?)", (b"vector",))
        conn.execute("INSERT INTO neural_links (source_id, target_id, weight) VALUES (1, 2, 0.75)")
        conn.execute(f"PRAGMA user_version = {user_version}")
        conn.commit()
        conn.close()

    @staticmethod
    def _table_names(conn):
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    @staticmethod
    def _index_names(conn):
        return {row[1] for row in conn.execute("PRAGMA index_list(clips)").fetchall()}

    def test_v1_migration_drops_only_neural_tables_and_preserves_live_data(self):
        self._create_legacy_db(user_version=1)

        self.db.init_db()
        conn = self.db.get_connection()

        tables = self._table_names(conn)
        self.assertNotIn("neural_vectors", tables)
        self.assertNotIn("neural_links", tables)
        self.assertIn("clips", tables)
        self.assertEqual(CURRENT_SCHEMA_VERSION, conn.execute("PRAGMA user_version").fetchone()[0])
        self.assertEqual(
            ("preserved clip", "legacy-hash", "legacy", "ops", 1, 3),
            tuple(
                conn.execute(
                    "SELECT content, hash, tag, group_name, is_pinned, pin_order FROM clips"
                ).fetchone()
            ),
        )
        self.assertTrue(REQUIRED_INDEXES.issubset(self._index_names(conn)))
        self.assertTrue(
            any(name.startswith("sqlite_autoindex_clips_") for name in self._index_names(conn))
        )
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO clips
                   (type, content, hash, created_at, updated_at)
                   VALUES ('text', 'duplicate', 'legacy-hash', '2026-01-02', '2026-01-02')"""
            )
        self.assertEqual("ok", conn.execute("PRAGMA integrity_check").fetchone()[0])

    def test_second_init_at_v2_does_not_repeat_neural_drop_ddl(self):
        self._create_legacy_db(user_version=1)
        self.db.init_db()
        conn = self.db.get_connection()
        statements = []
        conn.set_trace_callback(statements.append)

        self.db.init_db()
        conn.set_trace_callback(None)

        self.assertFalse(
            any("DROP TABLE" in statement.upper() for statement in statements),
            statements,
        )
        self.assertEqual(CURRENT_SCHEMA_VERSION, conn.execute("PRAGMA user_version").fetchone()[0])
        self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0])

    def test_fresh_db_initializes_directly_to_v2_without_neural_tables(self):
        self.db.init_db()
        conn = self.db.get_connection()

        tables = self._table_names(conn)
        self.assertNotIn("neural_vectors", tables)
        self.assertNotIn("neural_links", tables)
        self.assertEqual(CURRENT_SCHEMA_VERSION, conn.execute("PRAGMA user_version").fetchone()[0])
        self.assertEqual("ok", conn.execute("PRAGMA integrity_check").fetchone()[0])

    def test_future_schema_version_preserves_neural_tables_and_version(self):
        self._create_legacy_db(user_version=7)

        self.db.init_db()
        conn = self.db.get_connection()

        tables = self._table_names(conn)
        self.assertIn("neural_vectors", tables)
        self.assertIn("neural_links", tables)
        self.assertEqual(7, conn.execute("PRAGMA user_version").fetchone()[0])
        self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0])
        self.assertEqual("ok", conn.execute("PRAGMA integrity_check").fetchone()[0])


if __name__ == "__main__":
    unittest.main()

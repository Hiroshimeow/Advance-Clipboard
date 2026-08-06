import sqlite3
import statistics
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch


CURRENT_SCHEMA_VERSION = 2
REQUIRED_INDEXES = {"idx_pinned", "idx_updated", "idx_group"}


class DBPlannerMigrationTests(unittest.TestCase):
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

    def _create_legacy_db(
        self, *, required_indexes=False, redundant_hash=True, user_version=0
    ):
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
            """
        )
        if redundant_hash:
            conn.execute("CREATE INDEX idx_hash ON clips(hash)")
        if required_indexes:
            conn.executescript(
                """
                CREATE INDEX idx_pinned ON clips(is_pinned);
                CREATE INDEX idx_updated ON clips(updated_at DESC);
                CREATE INDEX idx_group ON clips(group_name);
                """
            )
        conn.execute(
            """INSERT INTO clips
               (type, content, hash, tag, group_name, is_pinned, pin_order,
                pinned_at, created_at, updated_at)
               VALUES ('text', 'preserved clip', 'legacy-hash', 'legacy', '', 0, 0,
                       NULL, '2026-01-01T00:00:00', '2026-01-01T00:00:00')"""
        )
        conn.execute(f"PRAGMA user_version = {user_version}")
        conn.commit()
        conn.close()

    def _index_names(self, conn):
        return {
            row[1]
            for row in conn.execute("PRAGMA index_list(clips)").fetchall()
        }

    def _statistics_rows(self, conn):
        return conn.execute(
            "SELECT tbl, idx, stat FROM sqlite_stat1 WHERE tbl = 'clips'"
        ).fetchall()

    def test_fresh_db_initializes_version_indexes_and_statistics(self):
        self.db.init_db()
        conn = self.db.get_connection()

        indexes = self._index_names(conn)
        self.assertEqual(CURRENT_SCHEMA_VERSION, conn.execute("PRAGMA user_version").fetchone()[0])
        self.assertTrue(REQUIRED_INDEXES.issubset(indexes))
        self.assertNotIn("idx_hash", indexes)
        self.assertTrue(any(name.startswith("sqlite_autoindex_clips_") for name in indexes))
        self.assertIsNotNone(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_stat1'"
            ).fetchone()
        )

    def test_legacy_db_drops_only_redundant_hash_index_and_preserves_data(self):
        self._create_legacy_db()

        self.db.init_db()
        conn = self.db.get_connection()

        indexes = self._index_names(conn)
        self.assertNotIn("idx_hash", indexes)
        self.assertTrue(REQUIRED_INDEXES.issubset(indexes))
        self.assertEqual("preserved clip", conn.execute("SELECT content FROM clips").fetchone()[0])
        self.assertTrue(self._statistics_rows(conn))
        self.assertEqual(CURRENT_SCHEMA_VERSION, conn.execute("PRAGMA user_version").fetchone()[0])
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO clips
                   (type, content, hash, created_at, updated_at)
                   VALUES ('text', 'duplicate hash', 'legacy-hash', '2026-01-02', '2026-01-02')"""
            )

    def test_current_version_without_statistics_is_repaired(self):
        self._create_legacy_db(
            required_indexes=True,
            redundant_hash=False,
            user_version=CURRENT_SCHEMA_VERSION,
        )

        self.db.init_db()
        conn = self.db.get_connection()

        self.assertTrue(self._statistics_rows(conn))
        self.assertNotIn("idx_hash", self._index_names(conn))

    def test_steady_state_reopen_does_not_refresh_statistics(self):
        self._create_legacy_db()
        self.db.init_db()
        conn = self.db.get_connection()
        conn.execute(
            "UPDATE sqlite_stat1 SET stat = 'sentinel' WHERE tbl = 'clips' AND idx = 'idx_updated'"
        )
        conn.commit()
        self.assertEqual(
            "sentinel",
            conn.execute(
                "SELECT stat FROM sqlite_stat1 WHERE tbl = 'clips' AND idx = 'idx_updated'"
            ).fetchone()[0],
        )

        self._close_thread_connection()
        self.db.init_db()
        conn = self.db.get_connection()

        self.assertEqual(
            "sentinel",
            conn.execute(
                "SELECT stat FROM sqlite_stat1 WHERE tbl = 'clips' AND idx = 'idx_updated'"
            ).fetchone()[0],
        )

    def test_history_and_pinned_query_plans_and_ordering_are_preserved(self):
        self.db.init_db()
        conn = self.db.get_connection()
        rows = [
            ("history old", "h1", 0, 0, None, "2026-01-01T00:00:00"),
            ("history new", "h2", 0, 0, None, "2026-01-03T00:00:00"),
            ("pinned lower", "p1", 1, 1, "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
            ("pinned upper", "p2", 1, 2, "2026-01-01T00:00:00", "2026-01-02T00:00:00"),
        ]
        conn.executemany(
            """INSERT INTO clips
               (type, content, hash, is_pinned, pin_order, pinned_at, created_at, updated_at)
               VALUES ('text', ?, ?, ?, ?, ?, ?, ?)""",
            [(content, hash_value, pinned, order, pinned_at, updated_at, updated_at)
             for content, hash_value, pinned, order, pinned_at, updated_at in rows],
        )
        conn.commit()

        history_sql = """SELECT * FROM clips INDEXED BY idx_updated
            WHERE is_pinned = 0 OR (is_pinned = 1 AND pinned_at IS NOT NULL AND updated_at > pinned_at)
            ORDER BY updated_at DESC LIMIT ?"""
        pinned_sql = "SELECT * FROM clips WHERE is_pinned = 1 ORDER BY pin_order DESC LIMIT ?"
        history_plan = " ".join(
            row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + history_sql, (20,))
        )
        pinned_plan = " ".join(
            row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + pinned_sql, (20,))
        )

        self.assertIn("idx_updated", history_plan)
        self.assertIn("idx_pinned", pinned_plan)
        self.assertEqual(
            ["history new", "pinned upper", "history old"],
            [row["content"] for row in conn.execute(history_sql, (20,)).fetchall()],
        )
        self.assertEqual(
            ["pinned upper", "pinned lower"],
            [row["content"] for row in conn.execute(pinned_sql, (20,)).fetchall()],
        )

    def test_representative_sql_performance_budget_for_near_recency_matches(self):
        self.db.init_db()
        conn = self.db.get_connection()
        base = datetime(2026, 1, 1)
        rows = []
        for i in range(3000):
            updated = (base + timedelta(seconds=i)).isoformat()
            content = f"ordinary clipboard row {i}"
            tag = ""
            if i >= 2840:
                content += " near-common"
            if i == 5:
                content += " rare-needle"
            if 100 <= i < 175:
                content += " alpha beta"
            if 400 <= i < 430:
                tag = "ops-tag"
            rows.append(
                ("text", content, f"history-{i}", tag, "", 0, 0, None, updated, updated)
            )
        for i in range(120):
            updated = (base + timedelta(days=1, seconds=i)).isoformat()
            content = f"pinned near-common row {i}"
            rows.append(
                ("text", content, f"pinned-{i}", "", "", 1, i + 1, updated, updated, updated)
            )
        conn.executemany(
            """INSERT INTO clips
               (type, content, hash, tag, group_name, is_pinned, pin_order,
                pinned_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
        conn.execute("ANALYZE")

        history_sql = """SELECT * FROM clips INDEXED BY idx_updated
            WHERE (content LIKE ? OR tag LIKE ? OR group_name LIKE ?)
              AND (is_pinned = 0 OR (is_pinned = 1 AND pinned_at IS NOT NULL AND updated_at > pinned_at))
            ORDER BY updated_at DESC LIMIT ?"""
        pinned_sql = """SELECT * FROM clips
            WHERE is_pinned = 1 AND (content LIKE ? OR tag LIKE ? OR group_name LIKE ?)
            ORDER BY pin_order DESC LIMIT ?"""
        tag_sql = """SELECT * FROM clips
            WHERE tag <> '' AND tag LIKE ?
              AND (is_pinned = 0 OR (is_pinned = 1 AND pinned_at IS NOT NULL AND updated_at > pinned_at))
            ORDER BY updated_at DESC, id DESC LIMIT ?"""

        cases = {
            "history_common": (history_sql, ("%near-common%",) * 3 + (80,)),
            "history_rare": (history_sql, ("%rare-needle%",) * 3 + (80,)),
            "history_absent": (history_sql, ("%does-not-exist%",) * 3 + (80,)),
            "history_multi": (
                history_sql.replace(
                    "(content LIKE ? OR tag LIKE ? OR group_name LIKE ?)",
                    "(content LIKE ? OR tag LIKE ? OR group_name LIKE ?) AND (content LIKE ? OR tag LIKE ? OR group_name LIKE ?)",
                ),
                ("%alpha%",) * 3 + ("%beta%",) * 3 + (80,),
            ),
            "history_tag": (tag_sql, ("%ops-tag%", 80)),
            "pinned_common": (pinned_sql, ("%near-common%",) * 3 + (80,)),
            "pinned_absent": (pinned_sql, ("%does-not-exist%",) * 3 + (80,)),
        }

        timings = {}
        for name, (sql, params) in cases.items():
            for _ in range(5):
                conn.execute(sql, params).fetchall()
            samples = []
            for _ in range(30):
                started = time.perf_counter_ns()
                conn.execute(sql, params).fetchall()
                samples.append((time.perf_counter_ns() - started) / 1_000_000)
            timings[name] = (
                statistics.median(samples),
                statistics.quantiles(samples, n=20)[18],
            )

        for name in ("history_common", "pinned_common"):
            p50, p95 = timings[name]
            self.assertLessEqual(p50, 2.0, f"{name} p50={p50:.3f}ms; all={timings}")
            self.assertLessEqual(p95, 5.0, f"{name} p95={p95:.3f}ms; all={timings}")

        self.assertGreaterEqual(
            timings["history_absent"][0],
            timings["history_common"][0],
            "Leading-wildcard absent search should remain documented as corpus-dependent",
        )


if __name__ == "__main__":
    unittest.main()

import sqlite3
import os
import threading
from contextlib import contextmanager

# DB_FILE is now located inside the storage/ directory
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clipboard.db")

# Thread-local storage for connections
_local = threading.local()

CURRENT_SCHEMA_VERSION = 2


def get_connection() -> sqlite3.Connection:
    """Get thread-local database connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(
            DB_FILE, check_same_thread=False, timeout=5.0
        )
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn.execute("PRAGMA busy_timeout=5000")
    return _local.conn


@contextmanager
def transaction():
    """Context manager for transactions."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db():
    """Initialize and migrate the database schema."""
    with transaction() as conn:
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]

        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'clips'"
        ).fetchone() is not None
        schema_changed = not table_exists

        conn.execute("""
            CREATE TABLE IF NOT EXISTS clips (
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
            )
        """)

        columns = {row[1] for row in conn.execute("PRAGMA table_info(clips)")}
        if "group_name" not in columns:
            conn.execute("ALTER TABLE clips ADD COLUMN group_name TEXT DEFAULT ''")
            schema_changed = True
        if "pinned_at" not in columns:
            conn.execute("ALTER TABLE clips ADD COLUMN pinned_at TEXT DEFAULT NULL")
            schema_changed = True

        conn.execute(
            "UPDATE clips SET pinned_at = updated_at WHERE is_pinned = 1 AND pinned_at IS NULL"
        )

        indexes = {row[1] for row in conn.execute("PRAGMA index_list(clips)")}
        if "idx_hash" in indexes:
            conn.execute("DROP INDEX idx_hash")
            schema_changed = True

        required_indexes = {
            "idx_pinned": "CREATE INDEX idx_pinned ON clips(is_pinned)",
            "idx_updated": "CREATE INDEX idx_updated ON clips(updated_at DESC)",
            "idx_group": "CREATE INDEX idx_group ON clips(group_name)",
        }
        for name, sql in required_indexes.items():
            if name not in indexes:
                conn.execute(sql)
                schema_changed = True

        if user_version < 2:
            legacy_neural_tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name IN ('neural_links', 'neural_vectors')"
                )
            }
            if "neural_links" in legacy_neural_tables:
                conn.execute("DROP TABLE IF EXISTS neural_links")
            if "neural_vectors" in legacy_neural_tables:
                conn.execute("DROP TABLE IF EXISTS neural_vectors")
            if legacy_neural_tables:
                schema_changed = True

        stats_table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_stat1'"
        ).fetchone() is not None
        has_clips = conn.execute("SELECT 1 FROM clips LIMIT 1").fetchone() is not None
        stats_present = stats_table_exists and (
            not has_clips
            or conn.execute(
                "SELECT 1 FROM sqlite_stat1 WHERE tbl = 'clips' LIMIT 1"
            ).fetchone() is not None
        )
        if schema_changed or not stats_present:
            conn.execute("ANALYZE")

        if user_version < CURRENT_SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")




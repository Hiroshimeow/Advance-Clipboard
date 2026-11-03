import sqlite3
import os
import threading
from contextlib import contextmanager

# DB_FILE is located inside the storage/ directory
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clipboard.db")

# Thread-local storage for connections
_local = threading.local()

CURRENT_SCHEMA_VERSION = 3


def get_connection() -> sqlite3.Connection:
    """Get thread-local database connection configured for WAL concurrency and fast in-memory cache."""
    conn = getattr(_local, "conn", None)
    conn_path = getattr(_local, "db_file", None)
    if conn is not None and conn_path == DB_FILE:
        try:
            conn.in_transaction
            return conn
        except sqlite3.ProgrammingError:
            _local.conn = None

    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

    conn = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-16000")
    conn.execute("PRAGMA foreign_keys=ON")
    _local.conn = conn
    _local.db_file = DB_FILE
    return conn


def close_connection() -> None:
    """Close thread-local database connection if open."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None


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

        existing_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
            ).fetchall()
        }
        table_exists = "clips" in existing_tables
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
            "idx_pinned_search": "CREATE INDEX idx_pinned_search ON clips(is_pinned, updated_at DESC, pin_order DESC, id DESC)",
        }
        for name, sql in required_indexes.items():
            if name not in indexes:
                conn.execute(sql)
                schema_changed = True

        if user_version < 2:
            legacy_neural_tables = existing_tables.intersection(
                {"neural_links", "neural_vectors"}
            )
            if "neural_links" in legacy_neural_tables:
                conn.execute("DROP TABLE IF EXISTS neural_links")
            if "neural_vectors" in legacy_neural_tables:
                conn.execute("DROP TABLE IF EXISTS neural_vectors")
            if legacy_neural_tables:
                schema_changed = True

        fts_exists = "clips_fts" in existing_tables
        if not fts_exists:
            conn.execute("""
                CREATE VIRTUAL TABLE clips_fts USING fts5(
                    content,
                    tag,
                    group_name,
                    content='clips',
                    content_rowid='id',
                    tokenize='trigram'
                )
            """)
            conn.execute("INSERT INTO clips_fts(clips_fts) VALUES ('rebuild')")
            schema_changed = True

        conn.executescript("""
            CREATE TRIGGER IF NOT EXISTS clips_fts_ai AFTER INSERT ON clips BEGIN
                INSERT INTO clips_fts(rowid, content, tag, group_name)
                VALUES (new.id, new.content, new.tag, new.group_name);
            END;
            CREATE TRIGGER IF NOT EXISTS clips_fts_ad AFTER DELETE ON clips BEGIN
                INSERT INTO clips_fts(clips_fts, rowid, content, tag, group_name)
                VALUES ('delete', old.id, old.content, old.tag, old.group_name);
            END;
            CREATE TRIGGER IF NOT EXISTS clips_fts_au AFTER UPDATE OF content, tag, group_name ON clips BEGIN
                INSERT INTO clips_fts(clips_fts, rowid, content, tag, group_name)
                VALUES ('delete', old.id, old.content, old.tag, old.group_name);
                INSERT INTO clips_fts(rowid, content, tag, group_name)
                VALUES (new.id, new.content, new.tag, new.group_name);
            END;
        """)

        # Focused workspace extensions: clip_shortcuts and clip_actions
        if "clip_shortcuts" not in existing_tables:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS clip_shortcuts (
                    clip_id INTEGER PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
                    shortcut_key TEXT NOT NULL UNIQUE COLLATE NOCASE
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_clip_shortcuts_key ON clip_shortcuts(shortcut_key)")
            schema_changed = True

        if "clip_actions" not in existing_tables:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS clip_actions (
                    clip_id INTEGER PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
                    action_type TEXT NOT NULL,
                    target TEXT NOT NULL,
                    args_json TEXT DEFAULT '[]',
                    is_background INTEGER DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
            """)
            schema_changed = True

        stats_table_exists = "sqlite_stat1" in existing_tables
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

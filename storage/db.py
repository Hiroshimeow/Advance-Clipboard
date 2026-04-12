import sqlite3
import os
import threading
from contextlib import contextmanager

# DB_FILE is now located inside the storage/ directory
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clipboard.db")

# Thread-local storage for connections
_local = threading.local()


def get_connection() -> sqlite3.Connection:
    """Get thread-local database connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA synchronous=NORMAL")
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
    """Initialize database schema if not exists."""
    with transaction() as conn:
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
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # Migration: add group_name column if not exists
        try:
            conn.execute("ALTER TABLE clips ADD COLUMN group_name TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass

        # Create indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hash ON clips(hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pinned ON clips(is_pinned)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_updated ON clips(updated_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_group ON clips(group_name)")


def init_neural_tables():
    """Initialize neural search tables."""
    with transaction() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS neural_vectors (
                clip_id INTEGER PRIMARY KEY,
                vector BLOB
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS neural_links (
                source_id INTEGER,
                target_id INTEGER,
                weight REAL,
                PRIMARY KEY (source_id, target_id)
            )
        """)

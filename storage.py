"""
SQLite Storage Layer for Clipboard Manager
- Single source of truth
- MD5 hash dedup
- Pagination support
- Thread-safe operations
"""

import sqlite3
import hashlib
import os
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager

DB_FILE = os.path.join(os.path.dirname(__file__), "clipboard.db")

# Thread-local storage for connections
_local = threading.local()


def _get_connection() -> sqlite3.Connection:
    """Get thread-local database connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA synchronous=NORMAL")
    return _local.conn


@contextmanager
def _transaction():
    """Context manager for transactions."""
    conn = _get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


class ClipboardStorage:
    """SQLite-backed clipboard storage with hash deduplication."""

    _need_backup = False
    _backup_callback = None

    def __init__(self):
        self._init_db()

    def _init_db(self):
        """Initialize database schema if not exists."""
        with _transaction() as conn:
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

            # Migrate: add group_name column if not exists (for existing DBs)
            try:
                conn.execute("ALTER TABLE clips ADD COLUMN group_name TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # Create indexes after migration
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hash ON clips(hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pinned ON clips(is_pinned)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_updated ON clips(updated_at DESC)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_group ON clips(group_name)")

    @staticmethod
    def compute_hash(content: str) -> str:
        """Compute MD5 hash for content."""
        return hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest()

    def set_backup_callback(self, callback):
        """Set callback to trigger backup when data changes."""
        self._backup_callback = callback

    def _mark_dirty(self):
        """Mark that backup is needed."""
        ClipboardStorage._need_backup = True
        if self._backup_callback:
            self._backup_callback()

    @property
    def need_backup(self) -> bool:
        return ClipboardStorage._need_backup

    def clear_backup_flag(self):
        ClipboardStorage._need_backup = False

    # ==================== WRITE OPERATIONS ====================

    def add_clip(self, clip_type: str, content: str, tag: str = "") -> Tuple[int, bool]:
        """
        Add new clip or update timestamp if duplicate.
        Returns (clip_id, is_new).
        """
        content_hash = self.compute_hash(content)
        now = datetime.now().isoformat()

        with _transaction() as conn:
            # Check for duplicate
            existing = conn.execute(
                "SELECT id, is_pinned FROM clips WHERE hash = ?", (content_hash,)
            ).fetchone()

            if existing:
                # Duplicate found - update timestamp to push to top
                conn.execute(
                    "UPDATE clips SET updated_at = ? WHERE id = ?",
                    (now, existing["id"]),
                )
                self._mark_dirty()
                return existing["id"], False

            # New clip - insert
            cursor = conn.execute(
                """INSERT INTO clips (type, content, hash, tag, is_pinned, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 0, ?, ?)""",
                (clip_type, content, content_hash, tag, now, now),
            )
            self._mark_dirty()
            new_id = cursor.lastrowid if cursor.lastrowid else 0
            return new_id, True

    def pin_clip(self, clip_id: int) -> bool:
        """Pin a clip (move to pinned section)."""
        now = datetime.now().isoformat()
        with _transaction() as conn:
            # Get max pin_order
            max_order = conn.execute(
                "SELECT COALESCE(MAX(pin_order), 0) FROM clips WHERE is_pinned = 1"
            ).fetchone()[0]

            conn.execute(
                "UPDATE clips SET is_pinned = 1, pin_order = ?, updated_at = ? WHERE id = ?",
                (max_order + 1, now, clip_id),
            )
            self._mark_dirty()
            return True

    def unpin_clip(self, clip_id: int) -> bool:
        """Unpin a clip (move back to history)."""
        now = datetime.now().isoformat()
        with _transaction() as conn:
            conn.execute(
                "UPDATE clips SET is_pinned = 0, pin_order = 0, updated_at = ? WHERE id = ?",
                (now, clip_id),
            )
            self._mark_dirty()
            return True

    def delete_clip(self, clip_id: int) -> bool:
        """Delete a clip by ID."""
        with _transaction() as conn:
            conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
            self._mark_dirty()
            return True

    def update_tag(self, clip_id: int, tag: str) -> bool:
        """Update tag for a clip."""
        with _transaction() as conn:
            conn.execute("UPDATE clips SET tag = ? WHERE id = ?", (tag, clip_id))
            self._mark_dirty()
            return True

    def update_group(self, clip_id: int, group_name: str) -> bool:
        """Update group for a clip."""
        with _transaction() as conn:
            conn.execute(
                "UPDATE clips SET group_name = ? WHERE id = ?", (group_name, clip_id)
            )
            self._mark_dirty()
            return True

    def get_groups(self) -> List[str]:
        """Get all unique group names (non-empty)."""
        conn = _get_connection()
        rows = conn.execute(
            "SELECT DISTINCT group_name FROM clips WHERE group_name != '' AND is_pinned = 1 ORDER BY group_name"
        ).fetchall()
        return [r["group_name"] for r in rows]

    def get_clips_by_group(self, group_name: str) -> List[Dict[str, Any]]:
        """Get all pinned clips in a group."""
        conn = _get_connection()
        rows = conn.execute(
            """SELECT id, type, content, hash, tag, group_name, created_at, updated_at
               FROM clips WHERE is_pinned = 1 AND group_name = ?
               ORDER BY pin_order DESC""",
            (group_name,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_ungrouped_pinned(
        self, limit: int = 50, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Get pinned clips without a group."""
        conn = _get_connection()
        rows = conn.execute(
            """SELECT id, type, content, hash, tag, group_name, created_at, updated_at
               FROM clips WHERE is_pinned = 1 AND (group_name = '' OR group_name IS NULL)
               ORDER BY pin_order DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def move_clip(self, clip_id: int, direction: int, is_pinned: bool) -> bool:
        """Move clip up/down in its list."""
        with _transaction() as conn:
            if is_pinned:
                # Get current order
                current = conn.execute(
                    "SELECT pin_order FROM clips WHERE id = ?", (clip_id,)
                ).fetchone()
                if not current:
                    return False

                current_order = current["pin_order"]
                new_order = current_order + direction

                # Find clip at target position
                target = conn.execute(
                    "SELECT id FROM clips WHERE is_pinned = 1 AND pin_order = ?",
                    (new_order,),
                ).fetchone()

                if target:
                    # Swap positions
                    conn.execute(
                        "UPDATE clips SET pin_order = ? WHERE id = ?",
                        (new_order, clip_id),
                    )
                    target_id: int = target["id"]
                    conn.execute(
                        "UPDATE clips SET pin_order = ? WHERE id = ?",
                        (current_order, target_id),
                    )
                    conn.execute(
                        "UPDATE clips SET pin_order = ? WHERE id = ?",
                        (current_order, target["id"]),
                    )
            else:
                # For history, reorder by updated_at
                clips = list(
                    conn.execute(
                        "SELECT id FROM clips WHERE is_pinned = 0 ORDER BY updated_at DESC"
                    ).fetchall()
                )

                clip_ids = [c["id"] for c in clips]
                if clip_id not in clip_ids:
                    return False

                idx = clip_ids.index(clip_id)
                new_idx = idx + direction
                if 0 <= new_idx < len(clip_ids):
                    clip_ids[idx], clip_ids[new_idx] = clip_ids[new_idx], clip_ids[idx]
                    # Update timestamps to reflect new order
                    base_time = datetime.now()
                    for i, cid in enumerate(clip_ids):
                        new_time = base_time.timestamp() - i * 0.001
                        conn.execute(
                            "UPDATE clips SET updated_at = ? WHERE id = ?",
                            (datetime.fromtimestamp(new_time).isoformat(), cid),
                        )

            self._mark_dirty()
            return True

    def clear_history(self) -> int:
        """Clear all non-pinned clips. Returns count deleted."""
        with _transaction() as conn:
            cursor = conn.execute("DELETE FROM clips WHERE is_pinned = 0")
            self._mark_dirty()
            return cursor.rowcount

    def clear_pinned(self) -> int:
        """Clear all pinned clips. Returns count deleted."""
        with _transaction() as conn:
            cursor = conn.execute("DELETE FROM clips WHERE is_pinned = 1")
            self._mark_dirty()
            return cursor.rowcount

    # ==================== READ OPERATIONS ====================

    def get_history(self, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        """Get history clips with pagination."""
        conn = _get_connection()
        rows = conn.execute(
            """SELECT id, type, content, hash, tag, created_at, updated_at
               FROM clips WHERE is_pinned = 0
               ORDER BY updated_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_pinned(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Get pinned clips with pagination."""
        conn = _get_connection()
        rows = conn.execute(
            """SELECT id, type, content, hash, tag, group_name, created_at, updated_at
               FROM clips WHERE is_pinned = 1
               ORDER BY pin_order DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_clip_by_id(self, clip_id: int) -> Optional[Dict[str, Any]]:
        """Get single clip by ID."""
        conn = _get_connection()
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        return dict(row) if row else None

    def get_clip_by_hash(self, content_hash: str) -> Optional[Dict[str, Any]]:
        """Get clip by content hash."""
        conn = _get_connection()
        row = conn.execute(
            "SELECT * FROM clips WHERE hash = ?", (content_hash,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_clips(self) -> List[Dict[str, Any]]:
        """Get all clips (for backup)."""
        conn = _get_connection()
        rows = conn.execute(
            "SELECT * FROM clips ORDER BY is_pinned DESC, updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_history_count(self) -> int:
        """Get total count of history clips."""
        conn = _get_connection()
        return conn.execute(
            "SELECT COUNT(*) FROM clips WHERE is_pinned = 0"
        ).fetchone()[0]

    def get_pinned_count(self) -> int:
        """Get total count of pinned clips."""
        conn = _get_connection()
        return conn.execute(
            "SELECT COUNT(*) FROM clips WHERE is_pinned = 1"
        ).fetchone()[0]

    def search_pinned(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Search pinned clips by content, tag, or group_name."""
        conn = _get_connection()
        query_pattern = f"%{query}%"
        rows = conn.execute(
            """SELECT id, type, content, hash, tag, group_name, created_at, updated_at
               FROM clips WHERE is_pinned = 1 
               AND (content LIKE ? OR tag LIKE ? OR group_name LIKE ?)
               ORDER BY pin_order DESC
               LIMIT ?""",
            (query_pattern, query_pattern, query_pattern, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def search_history(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Search history clips by content."""
        conn = _get_connection()
        query_pattern = f"%{query}%"
        rows = conn.execute(
            """SELECT id, type, content, hash, tag, created_at, updated_at
               FROM clips WHERE is_pinned = 0
               AND content LIKE ?
               ORDER BY updated_at DESC
               LIMIT ?""",
            (query_pattern, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def is_duplicate(self, content: str) -> bool:
        """Check if content already exists."""
        content_hash = self.compute_hash(content)
        conn = _get_connection()
        row = conn.execute(
            "SELECT 1 FROM clips WHERE hash = ?", (content_hash,)
        ).fetchone()
        return row is not None

    # ==================== BULK OPERATIONS ====================

    def import_clips(self, clips: List[Dict[str, Any]]) -> int:
        """
        Import clips from backup. Used for disaster recovery.
        Returns count of imported clips.
        """
        count = 0
        with _transaction() as conn:
            for clip in clips:
                content = clip.get("content", "")
                clip_type = clip.get("type", "text")
                tag = clip.get("tag", "")
                is_pinned = 1 if clip.get("is_pinned", False) else 0
                content_hash = clip.get("hash") or self.compute_hash(content)
                created_at = clip.get("created_at", datetime.now().isoformat())
                updated_at = clip.get("updated_at", created_at)

                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO clips 
                           (type, content, hash, tag, is_pinned, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            clip_type,
                            content,
                            content_hash,
                            tag,
                            is_pinned,
                            created_at,
                            updated_at,
                        ),
                    )
                    count += 1
                except sqlite3.IntegrityError:
                    pass  # Skip duplicates

        return count

    def is_db_valid(self) -> bool:
        """Check if database is valid and readable."""
        try:
            conn = _get_connection()
            conn.execute("SELECT 1 FROM clips LIMIT 1")
            return True
        except Exception:
            return False

    def get_clip_count(self) -> int:
        """Get total clip count."""
        try:
            conn = _get_connection()
            return conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
        except Exception:
            return 0


# Global instance
_storage: Optional[ClipboardStorage] = None


def get_storage() -> ClipboardStorage:
    """Get singleton storage instance."""
    global _storage
    if _storage is None:
        _storage = ClipboardStorage()
    return _storage

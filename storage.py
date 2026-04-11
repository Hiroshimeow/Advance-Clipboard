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
import re
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager

from rag_search import LightRAGRetriever

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
        self._search_revision = 0
        self._retriever = LightRAGRetriever()
        self._init_db()
        self._init_neural_tables()

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

    def _init_neural_tables(self):
        """Initialize neural search tables."""
        with _transaction() as conn:
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

    @staticmethod
    def compute_hash(content: str) -> str:
        """Compute MD5 hash for content."""
        return hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest()

    def set_backup_callback(self, callback):
        """Set callback to trigger backup when data changes."""
        self._backup_callback = callback

    def _mark_dirty(self):
        """Mark that backup is needed. Does NOT invalidate search index."""
        ClipboardStorage._need_backup = True
        self._search_revision += 1
        if self._backup_callback:
            self._backup_callback()

    def _incremental_index_clip(self, clip_id: int):
        """Add a single clip to the search index without rebuilding."""
        clip = self.get_clip_by_id(clip_id)
        if clip and clip.get("type") == "text":
            is_pinned = clip.get("is_pinned", False)
            ns = "pinned" if is_pinned else "history"
            self._retriever.add_record(ns, clip)

    def trigger_daily_rebuild(self):
        """Trigger a background RAG index rebuild if not done today.
        Non-blocking — search uses old/lexical index while rebuilding."""
        from datetime import date

        today = date.today().isoformat()

        # Check last rebuild date from a marker file
        marker_path = os.path.join(os.path.dirname(__file__), ".rag_last_rebuild")
        last_rebuild = ""
        try:
            if os.path.exists(marker_path):
                with open(marker_path, "r") as f:
                    last_rebuild = f.read().strip()
        except Exception:
            pass

        if last_rebuild == today:
            print(f"[RAG] Already rebuilt today ({today}), skipping")
            return

        print(f"[RAG] Daily rebuild triggered (last={last_rebuild}, today={today})")

        def _do_rebuild():
            # Rebuild history index
            history_rows = self._get_all_history_for_search()
            self._retriever.rebuild_async(
                namespace="history",
                revision=self._search_revision,
                records=history_rows,
            )
            # Rebuild pinned index
            pinned_rows = self._get_all_pinned_for_search()
            self._retriever.rebuild_async(
                namespace="pinned",
                revision=self._search_revision,
                records=pinned_rows,
            )
            # Write marker
            try:
                with open(marker_path, "w") as f:
                    f.write(today)
            except Exception:
                pass

        import threading

        threading.Thread(
            target=_do_rebuild, daemon=True, name="RAG-DailyRebuild"
        ).start()

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
            new_id = cursor.lastrowid if cursor.lastrowid else 0
            self._mark_dirty()
            # Incrementally add to search index (no full rebuild)
            self._incremental_index_clip(new_id)
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

    def _get_all_pinned_for_search(self) -> List[Dict[str, Any]]:
        """Load full pinned corpus for hybrid retrieval."""
        conn = _get_connection()
        rows = conn.execute(
            """SELECT id, type, content, hash, tag, group_name, created_at, updated_at
               FROM clips WHERE is_pinned = 1
               ORDER BY pin_order DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def _get_all_history_for_search(self) -> List[Dict[str, Any]]:
        """Load full history corpus for hybrid retrieval."""
        conn = _get_connection()
        rows = conn.execute(
            """SELECT id, type, content, hash, tag, created_at, updated_at
               FROM clips WHERE is_pinned = 0
               ORDER BY updated_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _split_search_terms(query: str) -> List[str]:
        """Split free-text query into normalized AND-search tokens."""
        return [term for term in re.split(r"\s+", (query or "").strip()) if term]

    def search_pinned(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Hybrid search for pinned clips using lexical + light RAG ranking.
        Never blocks — if RAG index not ready, falls back to lexical-only."""
        terms = self._split_search_terms(query)
        if not terms:
            return []

        lexical_rows = self._search_pinned_sql(query, max(limit * 3, 20))

        if self._retriever.has_index("pinned"):
            all_rows = self._get_all_pinned_for_search()
            ranked_ids = self._retriever.search(
                namespace="pinned",
                revision=self._search_revision,
                records=all_rows,
                query=query,
                limit=max(limit * 3, 20),
                lexical_ids=[row["id"] for row in lexical_rows],
            )
            return self._merge_ranked_results(
                ranked_ids=ranked_ids,
                semantic_rows=all_rows,
                lexical_rows=lexical_rows,
                limit=limit,
            )
        else:
            return lexical_rows[:limit]

    def _search_pinned_sql(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """Baseline SQL search used as one signal in hybrid retrieval."""
        terms = self._split_search_terms(query)
        if not terms:
            return []

        conn = _get_connection()
        where_clauses = []
        params: List[Any] = []
        for term in terms:
            pattern = f"%{term}%"
            where_clauses.append("(content LIKE ? OR tag LIKE ? OR group_name LIKE ?)")
            params.extend([pattern, pattern, pattern])

        rows = conn.execute(
            f"""SELECT id, type, content, hash, tag, group_name, created_at, updated_at
               FROM clips WHERE is_pinned = 1
               AND {" AND ".join(where_clauses)}
               ORDER BY pin_order DESC
               LIMIT ?""",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]

    def search_history(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Hybrid search for history clips using lexical + light RAG ranking.
        Never blocks — if RAG index not ready, falls back to lexical-only."""
        terms = self._split_search_terms(query)
        if not terms:
            return []

        lexical_rows = self._search_history_sql(query, max(limit * 3, 20))

        # If RAG index is available, use it for ranking boost
        if self._retriever.has_index("history"):
            all_rows = self._get_all_history_for_search()
            ranked_ids = self._retriever.search(
                namespace="history",
                revision=self._search_revision,
                records=all_rows,
                query=query,
                limit=max(limit * 3, 20),
                lexical_ids=[row["id"] for row in lexical_rows],
            )
            return self._merge_ranked_results(
                ranked_ids=ranked_ids,
                semantic_rows=all_rows,
                lexical_rows=lexical_rows,
                limit=limit,
            )
        else:
            # No RAG index yet — return lexical results only (instant)
            return lexical_rows[:limit]

    def _search_history_sql(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """Baseline SQL search used as one signal in hybrid retrieval."""
        terms = self._split_search_terms(query)
        if not terms:
            return []

        conn = _get_connection()
        where_clauses = []
        params: List[Any] = []
        for term in terms:
            where_clauses.append("content LIKE ?")
            params.append(f"%{term}%")

        rows = conn.execute(
            f"""SELECT id, type, content, hash, tag, created_at, updated_at
               FROM clips WHERE is_pinned = 0
               AND {" AND ".join(where_clauses)}
               ORDER BY updated_at DESC
               LIMIT ?""",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _merge_ranked_results(
        *,
        ranked_ids: List[int],
        semantic_rows: List[Dict[str, Any]],
        lexical_rows: List[Dict[str, Any]],
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Preserve hybrid ranking while keeping lexical fallback coverage."""
        rows_by_id = {int(row["id"]): row for row in semantic_rows}
        ordered_rows: List[Dict[str, Any]] = []
        seen_ids = set()

        for clip_id in ranked_ids:
            row = rows_by_id.get(int(clip_id))
            if row and clip_id not in seen_ids:
                ordered_rows.append(row)
                seen_ids.add(clip_id)
                if len(ordered_rows) >= limit:
                    return ordered_rows

        for row in lexical_rows:
            clip_id = int(row["id"])
            if clip_id not in seen_ids:
                ordered_rows.append(row)
                seen_ids.add(clip_id)
                if len(ordered_rows) >= limit:
                    break

        return ordered_rows

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
                pin_order = clip.get("pin_order", 0)
                group_name = clip.get("group_name", "")
                content_hash = clip.get("hash") or self.compute_hash(content)
                created_at = clip.get("created_at", datetime.now().isoformat())
                updated_at = clip.get("updated_at", created_at)

                try:
                    cursor = conn.execute(
                        """INSERT OR IGNORE INTO clips 
                           (type, content, hash, tag, group_name, is_pinned, pin_order, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            clip_type,
                            content,
                            content_hash,
                            tag,
                            group_name,
                            is_pinned,
                            pin_order,
                            created_at,
                            updated_at,
                        ),
                    )
                    count += cursor.rowcount
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

    # ==================== NEURAL OPERATIONS ====================

    def save_vector(self, clip_id: int, vector_bytes: bytes):
        """Save neural vector for a clip."""
        with _transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO neural_vectors (clip_id, vector) VALUES (?, ?)",
                (clip_id, vector_bytes),
            )

    def get_vector(self, clip_id: int) -> Optional[bytes]:
        """Get neural vector for a clip."""
        conn = _get_connection()
        row = conn.execute(
            "SELECT vector FROM neural_vectors WHERE clip_id = ?", (clip_id,)
        ).fetchone()
        return row["vector"] if row else None

    def save_links(self, links_list: List[Tuple[int, int, float]]):
        """Save neural links between clips."""
        with _transaction() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO neural_links (source_id, target_id, weight) VALUES (?, ?, ?)",
                links_list,
            )

    def get_links(self, clip_id_list: List[int]) -> List[Dict[str, Any]]:
        """Get links where source or target is in the provided list."""
        if not clip_id_list:
            return []
        conn = _get_connection()
        placeholders = ",".join(["?"] * len(clip_id_list))
        rows = conn.execute(
            f"SELECT source_id, target_id, weight FROM neural_links WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
            clip_id_list + clip_id_list,
        ).fetchall()
        return [dict(r) for r in rows]

    def get_unindexed_clip_ids(self, limit: int = 100) -> List[int]:
        """Get IDs of clips that haven't been indexed yet."""
        conn = _get_connection()
        rows = conn.execute(
            """SELECT id FROM clips 
               WHERE id NOT IN (SELECT clip_id FROM neural_vectors)
               ORDER BY id DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [r["id"] for r in rows]

    def get_recent_history_ids(self, limit: int = 200) -> List[int]:
        """Get IDs of the most recent non-pinned clips."""
        conn = _get_connection()
        rows = conn.execute(
            """SELECT id FROM clips
               WHERE is_pinned = 0
               ORDER BY updated_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [r["id"] for r in rows]

    def get_all_pinned_ids(self) -> List[int]:
        """Get IDs of all pinned clips."""
        conn = _get_connection()
        rows = conn.execute(
            "SELECT id FROM clips WHERE is_pinned = 1 ORDER BY pin_order DESC"
        ).fetchall()
        return [r["id"] for r in rows]

    def get_unindexed_ids_within_window(
        self, recent_limit: int = 200, include_pinned: bool = True, limit: int = 100
    ) -> List[int]:
        """Get unindexed clip IDs only within the bounded neural window."""
        window_ids: List[int] = []
        if include_pinned:
            window_ids.extend(self.get_all_pinned_ids())
        window_ids.extend(self.get_recent_history_ids(recent_limit))

        # De-duplicate while preserving order
        seen = set()
        deduped_ids = []
        for clip_id in window_ids:
            if clip_id not in seen:
                seen.add(clip_id)
                deduped_ids.append(clip_id)

        if not deduped_ids:
            return []

        conn = _get_connection()
        placeholders = ",".join(["?"] * len(deduped_ids))
        rows = conn.execute(
            f"""SELECT id FROM clips
                WHERE id IN ({placeholders})
                AND id NOT IN (SELECT clip_id FROM neural_vectors)
                ORDER BY updated_at DESC
                LIMIT ?""",
            deduped_ids + [limit],
        ).fetchall()
        return [r["id"] for r in rows]

    def get_neural_window_totals(
        self, recent_limit: int = 200, include_pinned: bool = True
    ) -> Tuple[int, int]:
        """Return (indexed_in_window, total_in_window) for progress reporting."""
        window_ids: List[int] = []
        if include_pinned:
            window_ids.extend(self.get_all_pinned_ids())
        window_ids.extend(self.get_recent_history_ids(recent_limit))

        seen = set()
        deduped_ids = []
        for clip_id in window_ids:
            if clip_id not in seen:
                seen.add(clip_id)
                deduped_ids.append(clip_id)

        total = len(deduped_ids)
        if total == 0:
            return 0, 0

        conn = _get_connection()
        placeholders = ",".join(["?"] * len(deduped_ids))
        indexed = conn.execute(
            f"SELECT COUNT(*) FROM neural_vectors WHERE clip_id IN ({placeholders}) AND vector != ''",
            deduped_ids,
        ).fetchone()[0]
        return int(indexed), int(total)

    def get_all_clip_ids_with_vectors(self, limit: int = 500) -> List[int]:
        """Get IDs of clips that have vectors."""
        conn = _get_connection()
        rows = conn.execute(
            "SELECT clip_id FROM neural_vectors WHERE vector != '' ORDER BY clip_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [r["clip_id"] for r in rows]

    def get_neural_data(
        self, clip_ids: List[int]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Get nodes and links for a list of clip IDs, including node degree."""
        if not clip_ids:
            return [], []

        conn = _get_connection()
        placeholders = ",".join(["?"] * len(clip_ids))

        # Get nodes (content limited for graph)
        rows = conn.execute(
            f"SELECT id, type, content, is_pinned FROM clips WHERE id IN ({placeholders})",
            clip_ids,
        ).fetchall()

        # Get links
        links = self.get_links(clip_ids)

        # Calculate degree per node based on these links
        degree_map = {}
        for link in links:
            s, t = link["source_id"], link["target_id"]
            degree_map[s] = degree_map.get(s, 0) + 1
            degree_map[t] = degree_map.get(t, 0) + 1

        nodes = []
        for r in rows:
            content = r["content"]
            full_content = content
            if r["type"] == "text":
                content = content[:50].replace("\n", " ")
            nodes.append(
                {
                    "id": r["id"],
                    "content": content,
                    "full_content": full_content,
                    "type": "pinned" if r["is_pinned"] else "history",
                    "degree": degree_map.get(r["id"], 0),
                }
            )

        return nodes, links


# Global instance
_storage: Optional[ClipboardStorage] = None


def get_storage() -> ClipboardStorage:
    """Get singleton storage instance."""
    global _storage
    if _storage is None:
        _storage = ClipboardStorage()
    return _storage

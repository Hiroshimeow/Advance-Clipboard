"""
SQLite Storage Layer for Clipboard Manager (Facade)
- Single source of truth
- MD5 hash dedup
- Pagination support
- Thread-safe operations
"""

from typing import Optional, List, Dict, Any, Tuple
from storage_db import (
    get_connection,
    transaction,
    init_db,
    init_neural_tables,
    DB_FILE,
)

# Re-export internal helpers for tests
_get_connection = get_connection
_transaction = transaction

from storage_clips import ClipRepository, compute_hash
from storage_search import SearchService
from storage_neural import NeuralRepository


class ClipboardStorage:
    """Facade for clipboard storage subsystem."""

    _need_backup = False
    _backup_callback = None

    def __init__(self):
        self.clips = ClipRepository()
        self.search = SearchService()
        self.neural = NeuralRepository()

        self._neural_event_callback = None

        # Initialize tables
        init_db()
        init_neural_tables()

    def set_backup_callback(self, callback):
        """Set callback to trigger backup when data changes."""
        self._backup_callback = callback

    def set_neural_event_callback(self, callback):
        """Set callback for lightweight neural enqueue events."""
        self._neural_event_callback = callback

    def _mark_dirty(self):
        """Mark that backup is needed."""
        ClipboardStorage._need_backup = True
        self.search.increment_revision()
        if self._backup_callback:
            self._backup_callback()

    def _emit_neural_event(self, event_type: str, clip_id: int):
        if self._neural_event_callback:
            try:
                self._neural_event_callback(event_type, clip_id)
            except Exception:
                pass

    def trigger_daily_rebuild(self):
        self.search.trigger_daily_rebuild(self)

    @property
    def need_backup(self) -> bool:
        return ClipboardStorage._need_backup

    def clear_backup_flag(self):
        ClipboardStorage._need_backup = False

    # ==================== PUBLIC API (delegated) ====================

    @staticmethod
    def compute_hash(content: str) -> str:
        return compute_hash(content)

    def add_clip(self, clip_type: str, content: str, tag: str = "") -> Tuple[int, bool]:
        clip_id, is_new, was_pinned = self.clips.add_clip(clip_type, content, tag)
        if is_new or True:  # always mark dirty for updated_at changes
            self._mark_dirty()

        if is_new:
            # Incrementally add to search index
            clip = self.get_clip_by_id(clip_id)
            if clip and clip.get("type") == "text":
                self.search.add_record("pinned" if was_pinned else "history", clip)
            self._emit_neural_event("new_clip", clip_id)

        return clip_id, is_new

    def pin_clip(self, clip_id: int) -> bool:
        ok = self.clips.pin_clip(clip_id)
        if ok:
            self._mark_dirty()
            self._emit_neural_event("pin_state_changed", clip_id)
        return ok

    def unpin_clip(self, clip_id: int) -> bool:
        ok = self.clips.unpin_clip(clip_id)
        if ok:
            self._mark_dirty()
            self._emit_neural_event("pin_state_changed", clip_id)
        return ok

    def delete_clip(self, clip_id: int) -> bool:
        ok = self.clips.delete_clip(clip_id)
        if ok:
            self._mark_dirty()
        return ok

    def update_tag(self, clip_id: int, tag: str) -> bool:
        ok = self.clips.update_tag(clip_id, tag)
        if ok:
            self._mark_dirty()
        return ok

    def update_group(self, clip_id: int, group_name: str) -> bool:
        ok = self.clips.update_group(clip_id, group_name)
        if ok:
            self._mark_dirty()
        return ok

    def get_groups(self) -> List[str]:
        return self.clips.get_groups()

    def get_clips_by_group(self, group_name: str) -> List[Dict[str, Any]]:
        return self.clips.get_clips_by_group(group_name)

    def get_ungrouped_pinned(
        self, limit: int = 50, offset: int = 0
    ) -> List[Dict[str, Any]]:
        return self.clips.get_ungrouped_pinned(limit, offset)

    def move_clip(self, clip_id: int, direction: int, is_pinned: bool) -> bool:
        ok = self.clips.move_clip(clip_id, direction, is_pinned)
        if ok:
            self._mark_dirty()
        return ok

    def clear_history(self) -> int:
        count = self.clips.clear_history()
        if count > 0:
            self._mark_dirty()
        return count

    def clear_pinned(self) -> int:
        count = self.clips.clear_pinned()
        if count > 0:
            self._mark_dirty()
        return count

    def get_history(self, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        return self.clips.get_history(limit, offset)

    def get_pinned(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        return self.clips.get_pinned(limit, offset)

    def get_clip_by_id(self, clip_id: int) -> Optional[Dict[str, Any]]:
        return self.clips.get_clip_by_id(clip_id)

    def get_clip_by_hash(self, content_hash: str) -> Optional[Dict[str, Any]]:
        return self.clips.get_clip_by_hash(content_hash)

    def get_all_clips(self) -> List[Dict[str, Any]]:
        return self.clips.get_all_clips()

    def get_history_count(self) -> int:
        return self.clips.get_history_count()

    def get_pinned_count(self) -> int:
        return self.clips.get_pinned_count()

    def is_duplicate(self, content: str) -> bool:
        return self.clips.is_duplicate(content)

    def import_clips(self, clips: List[Dict[str, Any]]) -> int:
        return self.clips.import_clips(clips)

    def is_db_valid(self) -> bool:
        return self.clips.is_db_valid()

    def get_clip_count(self) -> int:
        return self.clips.get_clip_count()

    # ==================== SEARCH OPERATIONS (delegated) ====================

    def search_pinned(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        terms = self.search.split_search_terms(query)
        if not terms:
            return []

        lexical_rows = self._search_pinned_sql(query, max(limit * 3, 20))
        if self.search.has_index("pinned"):
            all_rows = self._get_all_pinned_for_search()
            ranked_ids = self.search.search(
                "pinned",
                query,
                all_rows,
                max(limit * 3, 20),
                [r["id"] for r in lexical_rows],
            )
            return self.search.merge_ranked_results(
                ranked_ids=ranked_ids,
                semantic_rows=all_rows,
                lexical_rows=lexical_rows,
                limit=limit,
            )
        return lexical_rows[:limit]

    def _search_pinned_sql(self, query: str, limit: int) -> List[Dict[str, Any]]:
        terms = self.search.split_search_terms(query)
        if not terms:
            return []
        conn = get_connection()
        where_clauses = []
        params = []
        for term in terms:
            pattern = f"%{term}%"
            where_clauses.append("(content LIKE ? OR tag LIKE ? OR group_name LIKE ?)")
            params.extend([pattern, pattern, pattern])
        rows = conn.execute(
            f"SELECT * FROM clips WHERE is_pinned = 1 AND ({' AND '.join(where_clauses)}) ORDER BY pin_order DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]

    def search_history(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        terms = self.search.split_search_terms(query)
        if not terms:
            return []

        lexical_rows = self._search_history_sql(query, max(limit * 3, 20))
        if self.search.has_index("history"):
            all_rows = self._get_all_history_for_search()
            ranked_ids = self.search.search(
                "history",
                query,
                all_rows,
                max(limit * 3, 20),
                [r["id"] for r in lexical_rows],
            )
            return self.search.merge_ranked_results(
                ranked_ids=ranked_ids,
                semantic_rows=all_rows,
                lexical_rows=lexical_rows,
                limit=limit,
            )
        return lexical_rows[:limit]

    def _search_history_sql(self, query: str, limit: int) -> List[Dict[str, Any]]:
        terms = self.search.split_search_terms(query)
        if not terms:
            return []
        conn = get_connection()
        where_clauses = []
        params = []
        for term in terms:
            where_clauses.append("content LIKE ?")
            params.append(f"%{term}%")
        rows = conn.execute(
            f"SELECT * FROM clips WHERE is_pinned = 0 AND ({' AND '.join(where_clauses)}) ORDER BY updated_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]

    def _get_all_pinned_for_search(self) -> List[Dict[str, Any]]:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM clips WHERE is_pinned = 1 ORDER BY pin_order DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def _get_all_history_for_search(self) -> List[Dict[str, Any]]:
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM clips WHERE is_pinned = 0 ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ==================== NEURAL OPERATIONS (delegated) ====================

    def save_vector(self, clip_id: int, vector_bytes: bytes):
        self.neural.save_vector(clip_id, vector_bytes)

    def get_vector(self, clip_id: int) -> Optional[bytes]:
        return self.neural.get_vector(clip_id)

    def save_links(self, links_list: List[Tuple[int, int, float]]):
        self.neural.save_links(links_list)

    def get_links(self, clip_id_list: List[int]) -> List[Dict[str, Any]]:
        return self.neural.get_links(clip_id_list)

    def get_unindexed_clip_ids(self, limit: int = 100) -> List[int]:
        return self.neural.get_unindexed_clip_ids(limit)

    def get_recent_history_ids(self, limit: int = 200) -> List[int]:
        return self.neural.get_recent_history_ids(limit)

    def get_all_pinned_ids(self) -> List[int]:
        return self.neural.get_all_pinned_ids()

    def get_unindexed_ids_within_window(
        self, recent_limit: int = 200, include_pinned: bool = True, limit: int = 100
    ) -> List[int]:
        return self.neural.get_unindexed_ids_within_window(
            recent_limit, include_pinned, limit
        )

    def get_neural_window_totals(
        self, recent_limit: int = 200, include_pinned: bool = True
    ) -> Tuple[int, int]:
        return self.neural.get_neural_window_totals(recent_limit, include_pinned)

    def get_all_clip_ids_with_vectors(self, limit: int = 500) -> List[int]:
        return self.neural.get_all_clip_ids_with_vectors(limit)

    def get_neural_data(
        self, clip_ids: List[int]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        return self.neural.get_neural_data(clip_ids)


# Global instance
_storage: Optional[ClipboardStorage] = None


def get_storage() -> ClipboardStorage:
    """Get singleton storage instance."""
    global _storage
    if _storage is None:
        _storage = ClipboardStorage()
    return _storage

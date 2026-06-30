"""
SQLite Storage Layer for Clipboard Manager (Facade)
- Single source of truth
- MD5 hash dedup
- Pagination support
- Thread-safe operations
"""

from typing import Optional, List, Dict, Any, Tuple
from .db import (
    get_connection,
    transaction,
    init_db,

    DB_FILE,
)

# Re-export internal helpers for tests
_get_connection = get_connection
_transaction = transaction

from .clips import ClipRepository, compute_hash
from .search import SearchService



def _lexical_rank(row: Dict[str, Any], query: str, *, include_meta: bool = False) -> int:
    """Fast deterministic UI-search ranking.

    Ranking policy for clipboard UX:
    1. exact content match
    2. prefix content match
    3. phrase/content containment
    4. all-token content match
    5. optional tag/group metadata match
    Recency is handled as a stable tie-breaker by updated_at.
    """
    normalized_query = " ".join((query or "").lower().split())
    if not normalized_query:
        return 0

    content = " ".join(str(row.get("content", "")).lower().split())
    tag = " ".join(str(row.get("tag", "")).lower().split())
    group = " ".join(str(row.get("group_name", "")).lower().split())
    terms = [t for t in normalized_query.split() if t]

    score = 0
    if content == normalized_query:
        score += 10000
    if content.startswith(normalized_query):
        score += 7000
    if normalized_query in content:
        score += 4500
    if terms and all(term in content for term in terms):
        score += 2500
    if terms:
        score += sum(250 for term in terms if term in content)

    if include_meta:
        meta = f"{tag} {group}".strip()
        if meta:
            if normalized_query in meta:
                score += 1800
            if terms and all(term in meta for term in terms):
                score += 900
            score += sum(120 for term in terms if term in meta)

    # Prefer text clips for text queries; image filenames should not outrank text.
    if row.get("type") == "text":
        score += 100
    return score


def _rank_lexical_rows(
    rows: List[Dict[str, Any]],
    query: str,
    limit: int,
    *,
    include_meta: bool = False,
    pinned_tiebreaker: bool = False,
) -> List[Dict[str, Any]]:
    def sort_key(row: Dict[str, Any]):
        return (
            _lexical_rank(row, query, include_meta=include_meta),
            str(row.get("updated_at") or ""),
            int(row.get("pin_order") or 0) if pinned_tiebreaker else 0,
            int(row.get("id") or 0),
        )

    return sorted(rows, key=sort_key, reverse=True)[:limit]


def _parse_tag_search_query(query: str) -> Optional[str]:
    parts = (query or "").strip().split(maxsplit=1)
    if len(parts) != 2:
        return None
    prefix, keyword = parts[0].lower(), parts[1].strip()
    if prefix not in {"tag", "tags"} or not keyword:
        return None
    return keyword


class ClipboardStorage:
    """Facade for clipboard storage subsystem."""

    _need_backup = False
    _backup_callback = None

    def __init__(self):
        self.clips = ClipRepository()
        self.search = SearchService()




        # Initialize tables
        init_db()


    def set_backup_callback(self, callback):
        """Set callback to trigger backup when data changes."""
        self._backup_callback = callback



    def _mark_dirty(self):
        """Mark that backup is needed."""
        ClipboardStorage._need_backup = True
        self.search.increment_revision()
        if self._backup_callback:
            self._backup_callback()



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


        return clip_id, is_new

    def pin_clip(self, clip_id: int) -> bool:
        ok = self.clips.pin_clip(clip_id)
        if ok:
            self._mark_dirty()

        return ok

    def unpin_clip(self, clip_id: int) -> bool:
        ok = self.clips.unpin_clip(clip_id)
        if ok:
            self._mark_dirty()

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

    def update_clip_content(self, clip_id: int, new_content: str) -> bool:
        ok = self.clips.update_clip_content(clip_id, new_content)
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

    def search_pinned(
        self, query: str, limit: int = 20, *, ranked: bool = True
    ) -> List[Dict[str, Any]]:
        tag_query = _parse_tag_search_query(query)
        if tag_query is not None:
            return self._search_pinned_tags_sql(tag_query, limit)

        terms = self.search.split_search_terms(query)
        if not terms:
            return []

        lexical_rows = self._search_pinned_sql(query, max(limit * 8, 80))
        ranked_lexical_rows = _rank_lexical_rows(
            lexical_rows,
            query,
            limit=max(limit * 3, 20),
            include_meta=True,
            pinned_tiebreaker=True,
        )
        if not ranked:
            return ranked_lexical_rows[:limit]
        if self.search.has_index("pinned"):
            all_rows = self._get_all_pinned_for_search()
            ranked_ids = self.search.search(
                "pinned",
                query,
                all_rows,
                max(limit * 3, 20),
                [r["id"] for r in ranked_lexical_rows],
            )
            return self.search.merge_ranked_results(
                ranked_ids=ranked_ids,
                indexed_rows=all_rows,
                lexical_rows=ranked_lexical_rows,
                limit=limit,
            )
        return ranked_lexical_rows[:limit]

    def _search_pinned_sql(self, query: str, limit: int) -> List[Dict[str, Any]]:
        terms = self.search.split_search_terms(query)
        if not terms:
            return []
        conn = get_connection()
        where_clauses = []
        params = []
        for term in terms:
            pattern = self.search.like_pattern(term)
            where_clauses.append("(content LIKE ? ESCAPE '\\' OR tag LIKE ? ESCAPE '\\' OR group_name LIKE ? ESCAPE '\\')")
            params.extend([pattern, pattern, pattern])
        rows = conn.execute(
            f"SELECT * FROM clips WHERE is_pinned = 1 AND ({' AND '.join(where_clauses)}) ORDER BY pin_order DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]

    def _search_pinned_tags_sql(self, query: str, limit: int) -> List[Dict[str, Any]]:
        terms = self.search.split_search_terms(query)
        if not terms:
            return []
        conn = get_connection()
        where_clauses = []
        params = []
        for term in terms:
            pattern = self.search.like_pattern(term)
            where_clauses.append("tag LIKE ? ESCAPE '\\'")
            params.append(pattern)
        rows = conn.execute(
            f"SELECT * FROM clips WHERE is_pinned = 1 AND tag <> '' AND ({' AND '.join(where_clauses)}) ORDER BY updated_at DESC, pin_order DESC, id DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]

    def search_history(
        self, query: str, limit: int = 20, *, ranked: bool = True
    ) -> List[Dict[str, Any]]:
        tag_query = _parse_tag_search_query(query)
        if tag_query is not None:
            return self._search_history_tags_sql(tag_query, limit)

        terms = self.search.split_search_terms(query)
        if not terms:
            return []

        lexical_rows = self._search_history_sql(query, max(limit * 8, 80))
        ranked_lexical_rows = _rank_lexical_rows(
            lexical_rows,
            query,
            limit=max(limit * 3, 20),
            include_meta=True,
            pinned_tiebreaker=False,
        )
        if not ranked:
            return ranked_lexical_rows[:limit]
        if self.search.has_index("history"):
            all_rows = self._get_all_history_for_search()
            ranked_ids = self.search.search(
                "history",
                query,
                all_rows,
                max(limit * 3, 20),
                [r["id"] for r in ranked_lexical_rows],
            )
            return self.search.merge_ranked_results(
                ranked_ids=ranked_ids,
                indexed_rows=all_rows,
                lexical_rows=ranked_lexical_rows,
                limit=limit,
            )
        return ranked_lexical_rows[:limit]

    def _search_history_sql(self, query: str, limit: int) -> List[Dict[str, Any]]:
        terms = self.search.split_search_terms(query)
        if not terms:
            return []
        conn = get_connection()
        where_clauses = []
        params = []
        for term in terms:
            pattern = self.search.like_pattern(term)
            where_clauses.append("(content LIKE ? ESCAPE '\\' OR tag LIKE ? ESCAPE '\\' OR group_name LIKE ? ESCAPE '\\')")
            params.extend([pattern, pattern, pattern])
        rows = conn.execute(
            f"""SELECT * FROM clips
                WHERE ({' AND '.join(where_clauses)})
                  AND (is_pinned = 0 OR (is_pinned = 1 AND pinned_at IS NOT NULL AND updated_at > pinned_at))
                ORDER BY updated_at DESC LIMIT ?""",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]

    def _search_history_tags_sql(self, query: str, limit: int) -> List[Dict[str, Any]]:
        terms = self.search.split_search_terms(query)
        if not terms:
            return []
        conn = get_connection()
        where_clauses = []
        params = []
        for term in terms:
            pattern = self.search.like_pattern(term)
            where_clauses.append("tag LIKE ? ESCAPE '\\'")
            params.append(pattern)
        rows = conn.execute(
            f"""SELECT * FROM clips
                WHERE tag <> ''
                  AND ({' AND '.join(where_clauses)})
                  AND (is_pinned = 0 OR (is_pinned = 1 AND pinned_at IS NOT NULL AND updated_at > pinned_at))
                ORDER BY updated_at DESC, id DESC LIMIT ?""",
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
            """SELECT * FROM clips
               WHERE is_pinned = 0 OR (is_pinned = 1 AND pinned_at IS NOT NULL AND updated_at > pinned_at)
               ORDER BY updated_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]




# Global instance
_storage: Optional[ClipboardStorage] = None





def get_storage() -> ClipboardStorage:
    """Get singleton storage instance."""
    global _storage
    if _storage is None:
        _storage = ClipboardStorage()
    return _storage

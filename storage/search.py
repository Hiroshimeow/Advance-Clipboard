import re
import os
import time
import threading
from datetime import date
from typing import List, Dict, Any, Optional
from .db import get_connection, transaction
from .hybrid_search import HybridSearchRetriever


class SearchService:
    def __init__(self):
        self._search_revision = 0
        self._retriever = HybridSearchRetriever()

    def add_record(self, namespace: str, record: Dict[str, Any]):
        self._retriever.add_record(namespace, record)

    def has_index(self, namespace: str) -> bool:
        return self._retriever.has_index(namespace)

    def search(
        self,
        namespace: str,
        query: str,
        records: List[Dict[str, Any]],
        limit: int,
        lexical_ids: List[int],
    ) -> List[int]:
        return self._retriever.search(
            namespace=namespace,
            revision=self._search_revision,
            records=records,
            query=query,
            limit=limit,
            lexical_ids=lexical_ids,
        )

    def increment_revision(self):
        self._search_revision += 1

    def trigger_daily_rebuild(self, storage_facade):
        """Trigger a background search index rebuild if not done today."""
        today = date.today().isoformat()
        # Marker file is in the project root (one level up from package)
        BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        marker_path = os.path.join(BASE_DIR, ".search_last_rebuild")

        last_rebuild = ""
        try:
            if os.path.exists(marker_path):
                with open(marker_path, "r") as f:
                    last_rebuild = f.read().strip()
        except Exception:
            pass

        if last_rebuild == today:
            print(f"[Search] Already rebuilt today ({today}), skipping")
            return

        print(f"[Search] Daily rebuild triggered (last={last_rebuild}, today={today})")

        def _do_rebuild():
            # Rebuild history index
            history_rows = storage_facade._get_all_history_for_search()
            self._retriever.rebuild_async(
                namespace="history",
                revision=self._search_revision,
                records=history_rows,
            )
            # Rebuild pinned index
            pinned_rows = storage_facade._get_all_pinned_for_search()
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

        threading.Thread(
            target=_do_rebuild, daemon=True, name="Search-DailyRebuild"
        ).start()

    @staticmethod
    def split_search_terms(query: str) -> List[str]:
        """Split free-text query into normalized AND-search tokens."""
        return [term for term in re.split(r"\s+", (query or "").strip()) if term]

    @staticmethod
    def like_pattern(term: str) -> str:
        """Return a literal contains-match LIKE pattern with wildcard escaping."""
        escaped = (term or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    @staticmethod
    def merge_ranked_results(
        *,
        ranked_ids: List[int],
        indexed_rows: List[Dict[str, Any]],
        lexical_rows: List[Dict[str, Any]],
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Preserve hybrid ranking while keeping lexical fallback coverage."""
        rows_by_id = {int(row["id"]): row for row in indexed_rows}
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

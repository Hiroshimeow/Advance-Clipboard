import re
from typing import List


class SearchService:
    """Small helpers for deterministic SQL-backed clipboard search."""

    def __init__(self):
        self._search_revision = 0

    def increment_revision(self):
        self._search_revision += 1

    @staticmethod
    def split_search_terms(query: str) -> List[str]:
        """Split free-text query into normalized AND-search tokens."""
        return [term for term in re.split(r"\s+", (query or "").strip()) if term]

    @staticmethod
    def like_pattern(term: str) -> str:
        """Return a literal contains-match LIKE pattern with wildcard escaping."""
        escaped = (term or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

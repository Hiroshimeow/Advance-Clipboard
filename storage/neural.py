import sqlite3
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from .db import get_connection, transaction


class NeuralRepository:
    def __init__(self):
        pass

    def save_vector(self, clip_id: int, vector_bytes: bytes):
        """Save neural vector for a clip."""
        with transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO neural_vectors (clip_id, vector) VALUES (?, ?)",
                (clip_id, vector_bytes),
            )

    def get_vector(self, clip_id: int) -> Optional[bytes]:
        """Get neural vector for a clip."""
        conn = get_connection()
        row = conn.execute(
            "SELECT vector FROM neural_vectors WHERE clip_id = ?", (clip_id,)
        ).fetchone()
        return row["vector"] if row else None

    def save_links(self, links_list: List[Tuple[int, int, float]]):
        """Save neural links between clips."""
        with transaction() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO neural_links (source_id, target_id, weight) VALUES (?, ?, ?)",
                links_list,
            )

    def get_links(self, clip_id_list: List[int]) -> List[Dict[str, Any]]:
        """Get links where source or target is in the provided list."""
        if not clip_id_list:
            return []
        conn = get_connection()
        placeholders = ",".join(["?"] * len(clip_id_list))
        rows = conn.execute(
            f"SELECT source_id, target_id, weight FROM neural_links WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
            clip_id_list + clip_id_list,
        ).fetchall()
        return [dict(r) for r in rows]

    def get_unindexed_clip_ids(self, limit: int = 100) -> List[int]:
        """Get IDs of clips that haven't been indexed yet."""
        conn = get_connection()
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
        conn = get_connection()
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
        conn = get_connection()
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

        seen = set()
        deduped_ids = []
        for clip_id in window_ids:
            if clip_id not in seen:
                seen.add(clip_id)
                deduped_ids.append(clip_id)

        if not deduped_ids:
            return []

        conn = get_connection()
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

        conn = get_connection()
        placeholders = ",".join(["?"] * len(deduped_ids))
        indexed = conn.execute(
            f"SELECT COUNT(*) FROM neural_vectors WHERE clip_id IN ({placeholders}) AND vector != ''",
            deduped_ids,
        ).fetchone()[0]
        return int(indexed), int(total)

    def get_all_clip_ids_with_vectors(self, limit: int = 500) -> List[int]:
        """Get IDs of clips that have vectors."""
        conn = get_connection()
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

        conn = get_connection()
        placeholders = ",".join(["?"] * len(clip_ids))

        rows = conn.execute(
            f"SELECT id, type, content, is_pinned FROM clips WHERE id IN ({placeholders})",
            clip_ids,
        ).fetchall()

        links = self.get_links(clip_ids)

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

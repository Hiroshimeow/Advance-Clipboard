import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from .db import get_connection, transaction


def compute_hash(content: str) -> str:
    """Compute MD5 hash for content."""
    return hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest()


class ClipRepository:
    def __init__(self):
        pass

    def add_clip(
        self, clip_type: str, content: str, tag: str = ""
    ) -> Tuple[int, bool, bool]:
        """
        Add new clip or update timestamp if duplicate.
        Returns (clip_id, is_new, was_pinned).
        """
        content_hash = compute_hash(content)
        now = datetime.now().isoformat()

        with transaction() as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO clips
                   (type, content, hash, tag, is_pinned, pinned_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 0, NULL, ?, ?)""",
                (clip_type, content, content_hash, tag, now, now),
            )
            if cursor.rowcount == 1:
                new_id = cursor.lastrowid if cursor.lastrowid else 0
                return new_id, True, False

            existing = conn.execute(
                "SELECT id, is_pinned, updated_at FROM clips WHERE hash = ?",
                (content_hash,),
            ).fetchone()
            if not existing:
                raise RuntimeError("Clip dedupe conflict resolved without an existing row.")

            current_updated_at = existing["updated_at"]
            if current_updated_at:
                try:
                    current_dt = datetime.fromisoformat(current_updated_at)
                    if datetime.fromisoformat(now) <= current_dt:
                        now = (current_dt + timedelta(microseconds=1)).isoformat()
                except ValueError:
                    pass
            conn.execute(
                "UPDATE clips SET updated_at = ? WHERE id = ?",
                (now, existing["id"]),
            )
            return existing["id"], False, bool(existing["is_pinned"])

    def pin_clip(self, clip_id: int) -> bool:
        """Pin a clip (move to pinned section)."""
        now = datetime.now().isoformat()
        with transaction() as conn:
            max_order = conn.execute(
                "SELECT COALESCE(MAX(pin_order), 0) FROM clips WHERE is_pinned = 1"
            ).fetchone()[0]

            conn.execute(
                "UPDATE clips SET is_pinned = 1, pin_order = ?, pinned_at = ?, updated_at = ? WHERE id = ?",
                (max_order + 1, now, now, clip_id),
            )
            return True

    def unpin_clip(self, clip_id: int) -> bool:
        """Unpin a clip (move back to history)."""
        now = datetime.now().isoformat()
        with transaction() as conn:
            conn.execute(
                "UPDATE clips SET is_pinned = 0, pin_order = 0, pinned_at = NULL, updated_at = ? WHERE id = ?",
                (now, clip_id),
            )
            return True

    def delete_clip(self, clip_id: int) -> bool:
        """Delete a clip by ID, cleaning up shortcuts and actions in the same transaction."""
        with transaction() as conn:
            conn.execute("DELETE FROM clip_shortcuts WHERE clip_id = ?", (clip_id,))
            conn.execute("DELETE FROM clip_actions WHERE clip_id = ?", (clip_id,))
            conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
            return True

    def update_tag(self, clip_id: int, tag: str) -> bool:
        """Update tag for a clip."""
        with transaction() as conn:
            conn.execute("UPDATE clips SET tag = ? WHERE id = ?", (tag, clip_id))
            return True

    def update_group(self, clip_id: int, group_name: str) -> bool:
        """Update group for a clip."""
        with transaction() as conn:
            conn.execute(
                "UPDATE clips SET group_name = ? WHERE id = ?", (group_name, clip_id)
            )
            return True

    def update_clip_content(self, clip_id: int, new_content: str) -> bool:
        """Update clip content while keeping dedupe guarantees."""
        content_hash = compute_hash(new_content)
        now = datetime.now().isoformat()
        with transaction() as conn:
            clip = conn.execute(
                "SELECT is_pinned, pinned_at, updated_at FROM clips WHERE id = ?",
                (clip_id,),
            ).fetchone()
            if not clip:
                return False
            existing = conn.execute(
                "SELECT id, is_pinned FROM clips WHERE hash = ? AND id != ?",
                (content_hash, clip_id),
            ).fetchone()
            if existing:
                if clip["is_pinned"] and not existing["is_pinned"]:
                    conn.execute("DELETE FROM clips WHERE id = ?", (existing["id"],))
                else:
                    raise ValueError("Another pinned clip already has this content.")
            updated_at = now
            if clip["is_pinned"]:
                updated_at = clip["updated_at"] or now
            conn.execute(
                "UPDATE clips SET content = ?, hash = ?, updated_at = ? WHERE id = ?",
                (new_content, content_hash, updated_at, clip_id),
            )
            return True

    def get_groups(self) -> List[str]:
        """Get all unique group names (non-empty)."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT DISTINCT group_name FROM clips WHERE group_name != '' AND is_pinned = 1 ORDER BY group_name"
        ).fetchall()
        return [r["group_name"] for r in rows]

    def get_clips_by_group(self, group_name: str) -> List[Dict[str, Any]]:
        """Get all pinned clips in a group."""
        conn = get_connection()
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
        conn = get_connection()
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
        with transaction() as conn:
            if is_pinned:
                current = conn.execute(
                    "SELECT pin_order FROM clips WHERE id = ?", (clip_id,)
                ).fetchone()
                if not current:
                    return False

                current_order = current["pin_order"]
                new_order = current_order + direction

                target = conn.execute(
                    "SELECT id FROM clips WHERE is_pinned = 1 AND pin_order = ?",
                    (new_order,),
                ).fetchone()

                if target:
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
                rows = conn.execute(
                    "SELECT id FROM clips WHERE is_pinned = 0 ORDER BY updated_at DESC"
                ).fetchall()
                clips = [dict(r) for r in rows]

                clip_ids = [c["id"] for c in clips]
                if clip_id not in clip_ids:
                    return False

                idx = clip_ids.index(clip_id)
                new_idx = idx + direction
                if 0 <= new_idx < len(clip_ids):
                    clip_ids[idx], clip_ids[new_idx] = clip_ids[new_idx], clip_ids[idx]
                    base_time = datetime.now()
                    for i, cid in enumerate(clip_ids):
                        new_time = base_time.timestamp() - i * 0.001
                        conn.execute(
                            "UPDATE clips SET updated_at = ? WHERE id = ?",
                            (datetime.fromtimestamp(new_time).isoformat(), cid),
                        )
            return True

    def clear_history(self) -> int:
        """Clear all non-pinned clips. Returns count deleted."""
        with transaction() as conn:
            conn.execute(
                """DELETE FROM clip_shortcuts WHERE clip_id IN (
                       SELECT id FROM clips WHERE is_pinned = 0
                   )"""
            )
            conn.execute(
                """DELETE FROM clip_actions WHERE clip_id IN (
                       SELECT id FROM clips WHERE is_pinned = 0
                   )"""
            )
            cursor = conn.execute("DELETE FROM clips WHERE is_pinned = 0")
            return cursor.rowcount

    def clear_pinned(self) -> int:
        """Clear all pinned clips. Returns count deleted."""
        with transaction() as conn:
            conn.execute(
                """DELETE FROM clip_shortcuts WHERE clip_id IN (
                       SELECT id FROM clips WHERE is_pinned = 1
                   )"""
            )
            conn.execute(
                """DELETE FROM clip_actions WHERE clip_id IN (
                       SELECT id FROM clips WHERE is_pinned = 1
                   )"""
            )
            cursor = conn.execute("DELETE FROM clips WHERE is_pinned = 1")
            return cursor.rowcount

    def get_history(self, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        """Get history clips with pagination."""
        conn = get_connection()
        rows = conn.execute(
            """SELECT id, type, content, hash, tag, group_name, pinned_at, created_at, updated_at
               FROM clips INDEXED BY idx_updated
               WHERE is_pinned = 0 OR (is_pinned = 1 AND pinned_at IS NOT NULL AND updated_at > pinned_at)
               ORDER BY updated_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_pinned(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Get pinned clips with pagination."""
        conn = get_connection()
        rows = conn.execute(
            """SELECT id, type, content, hash, tag, group_name, pinned_at, created_at, updated_at
               FROM clips WHERE is_pinned = 1
               ORDER BY pin_order DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_clip_by_id(self, clip_id: int) -> Optional[Dict[str, Any]]:
        """Get single clip by ID."""
        conn = get_connection()
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        return dict(row) if row else None

    def get_clip_by_hash(self, content_hash: str) -> Optional[Dict[str, Any]]:
        """Get clip by content hash."""
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM clips WHERE hash = ?", (content_hash,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_clips(self) -> List[Dict[str, Any]]:
        """Get all clips with attached shortcuts and actions for backup."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM clips ORDER BY is_pinned DESC, updated_at DESC"
        ).fetchall()
        shortcuts = self.get_all_shortcuts()
        actions = self.get_all_clip_actions()
        out = []
        for r in rows:
            d = dict(r)
            cid = d["id"]
            sc = shortcuts.get(cid)
            if sc:
                d["shortcut_key"] = sc
            act = actions.get(cid)
            if act:
                d["action"] = act
            out.append(d)
        return out

    def get_history_count(self) -> int:
        """Get total count of history clips."""
        conn = get_connection()
        return conn.execute(
            """SELECT COUNT(*) FROM clips
               WHERE is_pinned = 0 OR (is_pinned = 1 AND pinned_at IS NOT NULL AND updated_at > pinned_at)"""
        ).fetchone()[0]

    def get_pinned_count(self) -> int:
        """Get total count of pinned clips."""
        conn = get_connection()
        return conn.execute(
            "SELECT COUNT(*) FROM clips WHERE is_pinned = 1"
        ).fetchone()[0]

    def is_duplicate(self, content: str) -> bool:
        """Check if content already exists."""
        content_hash = compute_hash(content)
        conn = get_connection()
        row = conn.execute(
            "SELECT 1 FROM clips WHERE hash = ?", (content_hash,)
        ).fetchone()
        return row is not None

    def import_clips(self, clips: List[Dict[str, Any]]) -> int:
        """Import clips from backup with shortcut and action restoration."""
        count = 0
        with transaction() as conn:
            for clip in clips:
                content = clip.get("content", "")
                clip_type = clip.get("type", "text")
                tag = clip.get("tag", "")
                is_pinned = 1 if clip.get("is_pinned", False) else 0
                pin_order = clip.get("pin_order", 0)
                group_name = clip.get("group_name", "")
                content_hash = clip.get("hash") or compute_hash(content)
                created_at = clip.get("created_at", datetime.now().isoformat())
                updated_at = clip.get("updated_at", created_at)
                pinned_at = clip.get("pinned_at")
                if is_pinned and not pinned_at:
                    pinned_at = updated_at

                try:
                    cursor = conn.execute(
                        """INSERT OR IGNORE INTO clips 
                           (type, content, hash, tag, group_name, is_pinned, pin_order, pinned_at, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            clip_type,
                            content,
                            content_hash,
                            tag,
                            group_name,
                            is_pinned,
                            pin_order,
                            pinned_at,
                            created_at,
                            updated_at,
                        ),
                    )
                    count += cursor.rowcount
                    imported_id = cursor.lastrowid
                    if not imported_id:
                        row = conn.execute("SELECT id FROM clips WHERE hash = ?", (content_hash,)).fetchone()
                        imported_id = row[0] if row else None

                    # Restore shortcut if provided
                    if imported_id and clip.get("shortcut_key"):
                        skey = str(clip["shortcut_key"]).strip().upper()
                        if len(skey) == 1 and skey.isalnum():
                            conn.execute(
                                """INSERT OR REPLACE INTO clip_shortcuts (clip_id, shortcut_key)
                                   VALUES (?, ?)""",
                                (imported_id, skey),
                            )

                    # Restore action if provided
                    if imported_id and clip.get("action"):
                        act = clip["action"]
                        conn.execute(
                            """INSERT OR REPLACE INTO clip_actions
                               (clip_id, action_type, target, args_json, is_background, updated_at)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (
                                imported_id,
                                act.get("action_type", "command"),
                                act.get("target", ""),
                                json.dumps(act.get("args", [])),
                                1 if act.get("is_background") else 0,
                                act.get("updated_at", datetime.now().isoformat()),
                            ),
                        )
                except sqlite3.IntegrityError:
                    pass
        return count

    def is_db_valid(self) -> bool:
        """Check if database is valid and readable."""
        try:
            conn = get_connection()
            conn.execute("SELECT 1 FROM clips LIMIT 1")
            return True
        except Exception:
            return False

    def get_clip_count(self) -> int:
        """Get total clip count."""
        try:
            conn = get_connection()
            return conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
        except Exception:
            return 0

    # ==================== SHORTCUT OPERATIONS ====================

    def assign_shortcut(
        self, clip_id: int, key: str, *, force: bool = False
    ) -> Tuple[bool, Optional[int]]:
        """
        Assign a single shortcut key (0-9, A-Z) to a clip.
        Returns (success, existing_clip_id_if_conflict).
        """
        norm_key = (key or "").strip().upper()
        if len(norm_key) != 1 or not norm_key.isalnum():
            raise ValueError("Phím tắt chỉ chấp nhận đúng một ký tự (0-9 hoặc A-Z).")

        with transaction() as conn:
            clip = conn.execute("SELECT id FROM clips WHERE id = ?", (clip_id,)).fetchone()
            if not clip:
                return False, None

            existing = conn.execute(
                "SELECT clip_id FROM clip_shortcuts WHERE shortcut_key = ? COLLATE NOCASE",
                (norm_key,),
            ).fetchone()

            if existing:
                existing_id = int(existing["clip_id"])
                if existing_id == clip_id:
                    return True, None
                if not force:
                    return False, existing_id
                conn.execute(
                    "DELETE FROM clip_shortcuts WHERE shortcut_key = ? COLLATE NOCASE",
                    (norm_key,),
                )

            conn.execute(
                """INSERT INTO clip_shortcuts (clip_id, shortcut_key)
                   VALUES (?, ?)
                   ON CONFLICT(clip_id) DO UPDATE SET shortcut_key = excluded.shortcut_key""",
                (clip_id, norm_key),
            )
            return True, None

    def remove_shortcut(self, clip_id: int) -> bool:
        """Remove shortcut assigned to clip."""
        with transaction() as conn:
            cursor = conn.execute("DELETE FROM clip_shortcuts WHERE clip_id = ?", (clip_id,))
            return cursor.rowcount > 0

    def get_shortcut_for_clip(self, clip_id: int) -> Optional[str]:
        """Get shortcut key for a clip if assigned."""
        conn = get_connection()
        row = conn.execute(
            "SELECT shortcut_key FROM clip_shortcuts WHERE clip_id = ?", (clip_id,)
        ).fetchone()
        return str(row["shortcut_key"]) if row else None

    def get_clip_by_shortcut(self, key: str) -> Optional[Dict[str, Any]]:
        """Find the clip mapped to a shortcut key."""
        norm_key = (key or "").strip().upper()
        if not norm_key:
            return None
        conn = get_connection()
        row = conn.execute(
            """SELECT c.* FROM clips c
               JOIN clip_shortcuts s ON s.clip_id = c.id
               WHERE s.shortcut_key = ? COLLATE NOCASE""",
            (norm_key,),
        ).fetchone()
        return dict(row) if row else None

    def get_all_shortcuts(self) -> Dict[int, str]:
        """Get map of {clip_id: shortcut_key}."""
        conn = get_connection()
        rows = conn.execute("SELECT clip_id, shortcut_key FROM clip_shortcuts").fetchall()
        return {int(r["clip_id"]): str(r["shortcut_key"]) for r in rows}

    # ==================== ACTION OPERATIONS ====================

    def set_clip_action(
        self,
        clip_id: int,
        action_type: str,
        target: str,
        *,
        args: Optional[List[str]] = None,
        is_background: bool = False,
    ) -> bool:
        """
        Configure an executable action on a clip.
        action_type: 'python_inline', 'script_file', 'command'
        """
        if action_type not in ("python_inline", "script_file", "command", "url"):
            raise ValueError(f"Loại action không hợp lệ: {action_type}")
        now = datetime.now().isoformat()
        args_json = json.dumps(args or [], ensure_ascii=False)
        with transaction() as conn:
            clip = conn.execute("SELECT id FROM clips WHERE id = ?", (clip_id,)).fetchone()
            if not clip:
                return False
            conn.execute(
                """INSERT INTO clip_actions (clip_id, action_type, target, args_json, is_background, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(clip_id) DO UPDATE SET
                       action_type = excluded.action_type,
                       target = excluded.target,
                       args_json = excluded.args_json,
                       is_background = excluded.is_background,
                       updated_at = excluded.updated_at""",
                (clip_id, action_type, target.strip(), args_json, 1 if is_background else 0, now),
            )
            return True

    def get_clip_action(self, clip_id: int) -> Optional[Dict[str, Any]]:
        """Get action configuration for a clip if configured."""
        conn = get_connection()
        row = conn.execute(
            "SELECT action_type, target, args_json, is_background, updated_at FROM clip_actions WHERE clip_id = ?",
            (clip_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["args"] = json.loads(d.get("args_json") or "[]")
        except Exception:
            d["args"] = []
        d["is_background"] = bool(d.get("is_background"))
        return d

    def remove_clip_action(self, clip_id: int) -> bool:
        """Remove action assigned to clip."""
        with transaction() as conn:
            cursor = conn.execute("DELETE FROM clip_actions WHERE clip_id = ?", (clip_id,))
            return cursor.rowcount > 0

    def get_all_clip_actions(self) -> Dict[int, Dict[str, Any]]:
        """Get all configured actions."""
        conn = get_connection()
        rows = conn.execute("SELECT * FROM clip_actions").fetchall()
        out = {}
        for r in rows:
            cid = int(r["clip_id"])
            d = dict(r)
            try:
                d["args"] = json.loads(d.get("args_json") or "[]")
            except Exception:
                d["args"] = []
            d["is_background"] = bool(d.get("is_background"))
            out[cid] = d
        return out

    def get_action_clip_ids(self) -> set[int]:
        """Fast lookup of clip IDs that have an action configured."""
        conn = get_connection()
        rows = conn.execute("SELECT clip_id FROM clip_actions").fetchall()
        return {int(r[0]) for r in rows}

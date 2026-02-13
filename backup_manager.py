"""
JSON Backup Manager for Clipboard Manager
- SHA256 checksum validation
- Atomic file writes
- File rotation (keep last 10 backups)
- Debounced backup triggers
"""

import os
import json
import hashlib
import glob
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

BACKUP_DIR = os.path.join(os.path.dirname(__file__), "backups")
BACKUP_PREFIX = "clipboard_backup_"
MAX_BACKUPS = 10


def ensure_backup_dir():
    """Ensure backup directory exists."""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)


def compute_checksum(data: str) -> str:
    """Compute SHA256 checksum for data."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def get_backup_files() -> List[str]:
    """Get all backup files sorted by timestamp (newest first)."""
    ensure_backup_dir()
    pattern = os.path.join(BACKUP_DIR, f"{BACKUP_PREFIX}*.json")
    files = glob.glob(pattern)
    # Sort by filename (which contains timestamp)
    return sorted(files, reverse=True)


def rotate_backups():
    """Remove old backups, keeping only MAX_BACKUPS most recent."""
    files = get_backup_files()
    if len(files) > MAX_BACKUPS:
        for old_file in files[MAX_BACKUPS:]:
            try:
                os.remove(old_file)
            except OSError:
                pass


def create_backup(clips: List[Dict[str, Any]]) -> Optional[str]:
    """
    Create a new backup from clip data.
    Uses atomic write (write to temp, then rename).
    Returns the backup file path on success.
    """
    ensure_backup_dir()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{BACKUP_PREFIX}{timestamp}.json"
    filepath = os.path.join(BACKUP_DIR, filename)
    temp_filepath = filepath + ".tmp"

    # Prepare backup data with checksum
    backup_data = {
        "version": 1,
        "created_at": datetime.now().isoformat(),
        "clips": clips,
    }

    # Serialize and compute checksum
    clips_json = json.dumps(clips, ensure_ascii=False, sort_keys=True)
    checksum = compute_checksum(clips_json)
    backup_data["checksum"] = checksum
    backup_data["clip_count"] = len(clips)

    try:
        # Write to temp file first
        with open(temp_filepath, "w", encoding="utf-8") as f:
            json.dump(backup_data, f, ensure_ascii=False, indent=2)

        # Atomic rename
        os.replace(temp_filepath, filepath)

        # Rotate old backups
        rotate_backups()

        return filepath
    except Exception as e:
        # Clean up temp file if exists
        if os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
            except OSError:
                pass
        return None


def validate_backup(filepath: str) -> Tuple[bool, Optional[List[Dict[str, Any]]]]:
    """
    Validate a backup file.
    Returns (is_valid, clips_list).
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Check required fields
        if "clips" not in data or "checksum" not in data:
            return False, None

        clips = data["clips"]
        stored_checksum = data["checksum"]

        # Verify checksum
        clips_json = json.dumps(clips, ensure_ascii=False, sort_keys=True)
        computed_checksum = compute_checksum(clips_json)

        if stored_checksum != computed_checksum:
            return False, None

        return True, clips
    except (json.JSONDecodeError, KeyError, TypeError, OSError):
        return False, None


def find_valid_backup() -> Tuple[Optional[str], Optional[List[Dict[str, Any]]]]:
    """
    Find the most recent valid backup.
    Tries each backup from newest to oldest until finding a valid one.
    Returns (filepath, clips_list) or (None, None) if no valid backup found.
    """
    files = get_backup_files()

    for filepath in files:
        is_valid, clips = validate_backup(filepath)
        if is_valid and clips is not None:
            return filepath, clips

    return None, None


def import_legacy_json(filepath: str) -> Optional[List[Dict[str, Any]]]:
    """
    Import from legacy data.json format (history + pinned lists).
    Returns list of clips ready for DB import.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        clips = []
        now = datetime.now().isoformat()

        # Process pinned items first (higher priority)
        pinned = data.get("pinned", [])
        for i, item in enumerate(pinned):
            clip = normalize_clip_item(item)
            clip["is_pinned"] = True
            clip["pin_order"] = len(pinned) - i  # Maintain order
            clip["created_at"] = now
            clip["updated_at"] = now
            clips.append(clip)

        # Process history items
        history = data.get("history", [])
        for i, item in enumerate(history):
            clip = normalize_clip_item(item)
            clip["is_pinned"] = False
            clip["pin_order"] = 0
            clip["created_at"] = now
            clip["updated_at"] = now
            clips.append(clip)

        return clips
    except (json.JSONDecodeError, KeyError, TypeError, OSError):
        return None


def normalize_clip_item(item: Any) -> Dict[str, Any]:
    """Normalize clip item to standard format."""
    if isinstance(item, dict):
        return {
            "type": item.get("type", "text"),
            "content": item.get("content", ""),
            "tag": item.get("tag", ""),
        }
    else:
        return {"type": "text", "content": str(item), "tag": ""}


class BackupScheduler:
    """
    Handles debounced backup scheduling.
    Waits 30 seconds after last change before triggering backup.
    """

    def __init__(self, backup_func):
        self._backup_func = backup_func
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()
        self._debounce_seconds = 30

    def schedule(self):
        """Schedule a backup (debounced)."""
        with self._lock:
            # Cancel previous timer if any
            if self._timer is not None:
                self._timer.cancel()

            # Create new timer
            self._timer = threading.Timer(self._debounce_seconds, self._execute_backup)
            self._timer.daemon = True
            self._timer.start()

    def _execute_backup(self):
        """Execute the backup function."""
        with self._lock:
            self._timer = None

        try:
            self._backup_func()
        except Exception:
            pass

    def force_now(self):
        """Force immediate backup (for app exit)."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

        try:
            self._backup_func()
        except Exception:
            pass

    def cancel(self):
        """Cancel any pending backup."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

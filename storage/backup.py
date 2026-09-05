"""
Backup Manager for Clipboard Manager
- JSON backup with SHA256 checksum
- Debounced writes (30s)
- Atomic file operations
- Disaster recovery with fallback
- Backup rotation (keep 10 files)
"""

import json
import hashlib
import logging
import os
import glob
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

# Path logic: backups folder is in the project root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
BACKUP_PREFIX = "clipboard_backup_"
BACKUP_SUFFIX = ".json"
MAX_BACKUPS = 10
DEBOUNCE_SECONDS = 30
TEMP_BACKUP_MAX_AGE_SECONDS = 24 * 60 * 60
MIN_BACKUP_INTERVAL_SECONDS = 5 * 60

logger = logging.getLogger(__name__)


def ensure_backup_dir():
    """Ensure backup directory exists."""
    os.makedirs(BACKUP_DIR, exist_ok=True)


def cleanup_stale_temp_backups(*, now=None):
    """Remove only abandoned backup temp files older than 24 hours."""
    ensure_backup_dir()
    current = time.time() if now is None else now
    pattern = os.path.join(BACKUP_DIR, f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}.tmp")
    removed = []
    removed_bytes = 0
    for path in glob.glob(pattern):
        try:
            if os.path.islink(path):
                continue
            if current - os.path.getmtime(path) <= TEMP_BACKUP_MAX_AGE_SECONDS:
                continue
            size = os.path.getsize(path)
            os.remove(path)
            removed.append(path)
            removed_bytes += size
        except OSError:
            continue
    logger.info(
        "backup_temp_cleanup removed_count=%s removed_bytes=%s",
        len(removed),
        removed_bytes,
    )
    return removed


def compute_checksum(data: str) -> str:
    """Compute SHA256 checksum for data."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def get_backup_files() -> List[str]:
    """Get all backup files sorted by timestamp (newest first)."""
    ensure_backup_dir()
    pattern = os.path.join(BACKUP_DIR, f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}")
    files = glob.glob(pattern)
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


def create_backup(clips: List[Dict[Any, Any]]) -> Optional[str]:
    """
    Create a new backup from clip data.
    Uses atomic write (write to temp, then rename).
    """
    ensure_backup_dir()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{BACKUP_PREFIX}{timestamp}{BACKUP_SUFFIX}"
    filepath = os.path.join(BACKUP_DIR, filename)
    temp_filepath = filepath + ".tmp"

    # Serialize clips once. Older backups duplicated the full dataset into
    # clips/history/pinned, doubling file size and JSON CPU cost.
    clips_json = json.dumps(clips, ensure_ascii=False, sort_keys=True)
    checksum = compute_checksum(clips_json)
    created_at = datetime.now().isoformat()

    try:
        with open(temp_filepath, "w", encoding="utf-8") as f:
            f.write('{"version":2,"created_at":')
            json.dump(created_at, f, ensure_ascii=False)
            f.write(',"clips":')
            f.write(clips_json)
            f.write(',"checksum":')
            json.dump(checksum, f)
            f.write(f',"clip_count":{len(clips)}}}')

        os.replace(temp_filepath, filepath)
        rotate_backups()
        return filepath
    except Exception as e:
        if os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
            except OSError:
                pass
        print(f"[Backup] Failed: {e}")
        return None


def create_backup_in_subprocess(db_file: Optional[str] = None) -> bool:
    """Create a backup outside the UI process to avoid JSON/GIL stalls."""
    if db_file is None:
        from .db import DB_FILE

        db_file = DB_FILE

    command = [sys.executable, "-m", "storage.backup_worker", db_file]
    kwargs = {
        "cwd": BASE_DIR,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "timeout": 120,
        "check": False,
    }
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        result = subprocess.run(command, **kwargs)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def validate_backup(filepath: str) -> Tuple[bool, Optional[List[Dict[str, Any]]]]:
    """Validate a backup file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        if "clips" not in data and ("history" not in data and "pinned" not in data):
            return False, None

        clips = data.get("clips")
        if clips is None:
            # Reconstruct from history/pinned if clips missing
            clips = data.get("pinned", []) + data.get("history", [])

        if "checksum" in data:
            stored_checksum = data["checksum"]
            clips_json = json.dumps(clips, ensure_ascii=False, sort_keys=True)
            computed = compute_checksum(clips_json)
            if stored_checksum != computed:
                return False, None

        return True, clips
    except Exception:
        return False, None


def find_valid_backup() -> Tuple[Optional[str], Optional[List[Dict[str, Any]]]]:
    """Find the most recent valid backup."""
    files = get_backup_files()
    for filepath in files:
        is_valid, clips = validate_backup(filepath)
        if is_valid and clips is not None:
            return filepath, clips
    return None, None


def import_legacy_json(filepath: str) -> Optional[List[Dict[str, Any]]]:
    """Import from legacy data.json format."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        clips = []
        now = datetime.now().isoformat()

        pinned = data.get("pinned", [])
        for i, item in enumerate(pinned):
            clip = _normalize_clip_item(item)
            clip["is_pinned"] = True
            clip["pin_order"] = len(pinned) - i
            clip["created_at"] = now
            clip["updated_at"] = now
            clips.append(clip)

        history = data.get("history", [])
        for i, item in enumerate(history):
            clip = _normalize_clip_item(item)
            clip["is_pinned"] = False
            clip["pin_order"] = 0
            clip["created_at"] = now
            clip["updated_at"] = now
            clips.append(clip)

        return clips
    except Exception:
        return None


def _normalize_clip_item(item: Any) -> Dict[str, Any]:
    if isinstance(item, dict):
        return {
            "type": item.get("type", "text"),
            "content": item.get("content", ""),
            "tag": item.get("tag", ""),
        }
    return {"type": "text", "content": str(item), "tag": ""}


class BackupScheduler:
    """Debounce backups, enforce cooldown, and serialize all runs."""

    def __init__(self, backup_func, *, clock=time.monotonic, timer_factory=threading.Timer):
        self._backup_func = backup_func
        self._clock = clock
        self._timer_factory = timer_factory
        self._timer = None
        self._condition = threading.Condition()
        self._running = False
        self._dirty_during_run = False
        self._last_completed_at = None
        self._cancelled = False

    def _next_delay_locked(self):
        if self._last_completed_at is None:
            return float(DEBOUNCE_SECONDS)
        cooldown_remaining = max(
            0.0,
            MIN_BACKUP_INTERVAL_SECONDS
            - (self._clock() - self._last_completed_at),
        )
        return max(float(DEBOUNCE_SECONDS), cooldown_remaining)

    def _arm_timer_locked(self):
        delay = self._next_delay_locked()
        timer = self._timer_factory(delay, self._execute_backup)
        timer.daemon = True
        self._timer = timer
        timer.start()

    def schedule(self):
        with self._condition:
            if self._cancelled:
                return
            if self._running:
                self._dirty_during_run = True
                return
            if self._timer is not None:
                self._timer.cancel()
            self._arm_timer_locked()

    def _execute_backup(self):
        with self._condition:
            if self._cancelled:
                self._timer = None
                return
            self._timer = None
            if self._running:
                self._dirty_during_run = True
                return
            self._running = True
        try:
            self._backup_func()
        except Exception:
            pass
        finally:
            with self._condition:
                self._last_completed_at = self._clock()
                self._running = False
                if self._dirty_during_run and not self._cancelled:
                    self._dirty_during_run = False
                    self._arm_timer_locked()
                self._condition.notify_all()

    def force_now(self):
        with self._condition:
            if self._cancelled:
                return
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            while self._running and not self._cancelled:
                self._condition.wait()
            if self._cancelled:
                return
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._dirty_during_run = False
            self._running = True
        try:
            self._backup_func()
        except Exception:
            pass
        finally:
            with self._condition:
                self._last_completed_at = self._clock()
                self._running = False
                if self._dirty_during_run and not self._cancelled:
                    self._dirty_during_run = False
                    self._arm_timer_locked()
                self._condition.notify_all()

    def cancel(self):
        with self._condition:
            self._cancelled = True
            self._dirty_during_run = False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._condition.notify_all()

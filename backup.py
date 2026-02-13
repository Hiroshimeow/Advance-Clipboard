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
import os
import glob
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

BACKUP_DIR = os.path.join(os.path.dirname(__file__), "backups")
BACKUP_PREFIX = "clipboard_backup_"
BACKUP_SUFFIX = ".json"
MAX_BACKUPS = 10
DEBOUNCE_SECONDS = 30


class BackupManager:
    """Manages JSON backups with debouncing and rotation."""

    def __init__(self, storage):
        self.storage = storage
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()
        self._ensure_backup_dir()

        # Register backup callback with storage
        storage.set_backup_callback(self._on_data_changed)

    def _ensure_backup_dir(self):
        """Create backup directory if not exists."""
        os.makedirs(BACKUP_DIR, exist_ok=True)

    def _on_data_changed(self):
        """Called when storage data changes. Schedules debounced backup."""
        self.schedule_backup()

    def schedule_backup(self):
        """Schedule a backup after debounce period."""
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(DEBOUNCE_SECONDS, self._do_backup)
            self._timer.daemon = True
            self._timer.start()

    def force_backup(self):
        """Force immediate backup (e.g., on app exit)."""
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
        self._do_backup()

    def _do_backup(self):
        """Perform the actual backup operation."""
        if not self.storage.need_backup:
            return

        try:
            # Get all clips from storage
            clips = self.storage.get_all_clips()

            # Separate into history and pinned for compatibility
            history = [c for c in clips if not c.get("is_pinned")]
            pinned = [c for c in clips if c.get("is_pinned")]

            # Build backup data
            backup_data = {
                "version": 2,
                "created_at": datetime.now().isoformat(),
                "history": history,
                "pinned": pinned,
                "checksum": "",  # Will be filled
            }

            # Calculate checksum (excluding checksum field)
            data_for_hash = {k: v for k, v in backup_data.items() if k != "checksum"}
            checksum = hashlib.sha256(
                json.dumps(data_for_hash, sort_keys=True, ensure_ascii=False).encode(
                    "utf-8"
                )
            ).hexdigest()
            backup_data["checksum"] = checksum

            # Generate filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{BACKUP_PREFIX}{timestamp}{BACKUP_SUFFIX}"
            filepath = os.path.join(BACKUP_DIR, filename)
            temp_filepath = filepath + ".tmp"

            # Atomic write: write to temp then rename
            with open(temp_filepath, "w", encoding="utf-8") as f:
                json.dump(backup_data, f, ensure_ascii=False, indent=2)

            os.replace(temp_filepath, filepath)

            # Rotate old backups
            self._rotate_backups()

            # Clear backup flag
            self.storage.clear_backup_flag()

        except Exception as e:
            print(f"[BackupManager] Backup failed: {e}")

    def _rotate_backups(self):
        """Keep only MAX_BACKUPS most recent backups."""
        pattern = os.path.join(BACKUP_DIR, f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}")
        backups = sorted(glob.glob(pattern), reverse=True)

        # Delete old backups
        for old_backup in backups[MAX_BACKUPS:]:
            try:
                os.remove(old_backup)
            except Exception:
                pass

    def get_latest_backup(self) -> Optional[str]:
        """Get path to latest backup file."""
        pattern = os.path.join(BACKUP_DIR, f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}")
        backups = sorted(glob.glob(pattern), reverse=True)
        return backups[0] if backups else None

    def get_all_backups(self) -> List[str]:
        """Get all backup files sorted by newest first."""
        pattern = os.path.join(BACKUP_DIR, f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}")
        return sorted(glob.glob(pattern), reverse=True)

    def validate_backup(self, filepath: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Validate backup file.
        Returns (is_valid, data) tuple.
        """
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Check required fields
            if "history" not in data and "pinned" not in data:
                return False, None

            # Verify checksum if present (v2 format)
            if "checksum" in data:
                stored_checksum = data["checksum"]
                data_for_hash = {k: v for k, v in data.items() if k != "checksum"}
                computed = hashlib.sha256(
                    json.dumps(
                        data_for_hash, sort_keys=True, ensure_ascii=False
                    ).encode("utf-8")
                ).hexdigest()
                if stored_checksum != computed:
                    return False, None

            return True, data

        except Exception:
            return False, None

    def restore_from_backup(self, filepath: Optional[str] = None) -> Tuple[bool, int]:
        """
        Restore data from backup file.
        If filepath is None, tries latest backup.
        Returns (success, count_restored).
        """
        if filepath is None:
            filepath = self.get_latest_backup()

        if not filepath:
            return False, 0

        is_valid, data = self.validate_backup(filepath)
        if not is_valid:
            return False, 0

        # Convert to clips format for import
        clips = []

        for item in data.get("pinned", []):
            item["is_pinned"] = True
            clips.append(item)

        for item in data.get("history", []):
            item["is_pinned"] = False
            clips.append(item)

        count = self.storage.import_clips(clips)
        return True, count

    def try_recovery(self) -> Tuple[bool, int]:
        """
        Try to recover from backups.
        Attempts each backup from newest to oldest.
        Returns (success, count_restored).
        """
        backups = self.get_all_backups()

        for backup_path in backups:
            success, count = self.restore_from_backup(backup_path)
            if success and count > 0:
                return True, count

        # Also try legacy data.json
        legacy_path = os.path.join(os.path.dirname(__file__), "data.json")
        if os.path.exists(legacy_path):
            try:
                with open(legacy_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                clips = []
                for item in data.get("pinned", []):
                    if isinstance(item, dict):
                        item["is_pinned"] = True
                        clips.append(item)
                    else:
                        clips.append(
                            {"type": "text", "content": item, "is_pinned": True}
                        )

                for item in data.get("history", []):
                    if isinstance(item, dict):
                        item["is_pinned"] = False
                        clips.append(item)
                    else:
                        clips.append(
                            {"type": "text", "content": item, "is_pinned": False}
                        )

                count = self.storage.import_clips(clips)
                if count > 0:
                    return True, count
            except Exception:
                pass

        return False, 0

    def shutdown(self):
        """Clean shutdown - force backup if needed."""
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None

        if self.storage.need_backup:
            self._do_backup()


# Global instance
_backup_manager: Optional[BackupManager] = None


def get_backup_manager(storage=None) -> BackupManager:
    """Get singleton backup manager instance."""
    global _backup_manager
    if _backup_manager is None:
        if storage is None:
            from storage import get_storage

            storage = get_storage()
        _backup_manager = BackupManager(storage)
    return _backup_manager

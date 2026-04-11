"""
test_restore_db.py — Restore clipboard.db from the latest valid backup JSON.
Safe: does NOT delete any existing data. Uses import_clips which is INSERT OR IGNORE.
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from storage import get_storage
from backup_manager import find_valid_backup, get_backup_files, validate_backup


def main():
    store = get_storage()

    # Show current DB state
    clip_count = store.get_clip_count()
    print(f"[DB] Current clip count: {clip_count}")

    if clip_count > 0:
        print("[DB] Database already has data. Restore not needed.")
        print("     (To force restore, delete clipboard.db first)")
        return

    # List all backups and their sizes
    backup_files = get_backup_files()
    print(f"\n[Backups] Found {len(backup_files)} backup files:")
    for f in backup_files:
        size_mb = os.path.getsize(f) / (1024 * 1024)
        print(f"  {os.path.basename(f)}  ({size_mb:.1f} MB)")

    # Try the largest backup (most likely the real one), not just the newest
    # Sort by file size descending to find the real backup
    backups_by_size = sorted(
        backup_files, key=lambda f: os.path.getsize(f), reverse=True
    )

    restored = False
    for filepath in backups_by_size:
        size_mb = os.path.getsize(filepath) / (1024 * 1024)
        basename = os.path.basename(filepath)

        if size_mb < 0.01:
            print(f"\n[Skip] {basename} — too small ({size_mb:.3f} MB)")
            continue

        print(f"\n[Try] {basename} ({size_mb:.1f} MB)...")
        is_valid, clips = validate_backup(filepath)

        if not is_valid or not clips:
            print(f"  [INVALID] Checksum mismatch or corrupt file.")
            continue

        pinned_count = sum(1 for c in clips if c.get("is_pinned"))
        history_count = len(clips) - pinned_count
        print(
            f"  [VALID] {len(clips)} clips (history={history_count}, pinned={pinned_count})"
        )

        # Restore
        print(f"  [Restoring...]")
        imported = store.import_clips(clips)
        print(f"  [DONE] Imported {imported} new clips into DB.")

        # Verify
        new_count = store.get_clip_count()
        print(f"\n[DB] New clip count: {new_count}")
        restored = True
        break

    if not restored:
        print("\n[FAIL] No valid backup found to restore from!")
        print("       Check your backups/ directory for valid .json files.")


if __name__ == "__main__":
    main()

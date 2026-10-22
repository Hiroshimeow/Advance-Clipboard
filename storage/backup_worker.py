import sqlite3
import sys
from pathlib import Path

from .backup import create_backup
from .clips import export_clips


def create_backup_from_database(db_file: str) -> bool:
    path = Path(db_file)
    if not path.is_file():
        return False

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        clips = export_clips(conn)
    finally:
        conn.close()

    return create_backup(clips) is not None


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        return 2
    return 0 if create_backup_from_database(args[0]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

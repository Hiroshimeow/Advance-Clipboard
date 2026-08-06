import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storage import backup
from storage import backup_worker


class BackupFormatTests(unittest.TestCase):
    def test_new_backup_stores_clip_dataset_once_and_validates(self):
        clips = [
            {
                "id": 1,
                "type": "text",
                "content": "history text",
                "hash": "h1",
                "tag": "",
                "group_name": "",
                "is_pinned": 0,
                "pin_order": 0,
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-01-01T00:00:00",
            },
            {
                "id": 2,
                "type": "text",
                "content": "pinned text",
                "hash": "h2",
                "tag": "todo",
                "group_name": "work",
                "is_pinned": 1,
                "pin_order": 1,
                "created_at": "2026-01-01T00:00:01",
                "updated_at": "2026-01-01T00:00:01",
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(backup, "BACKUP_DIR", tmp):
                path = backup.create_backup(clips)
                self.assertIsNotNone(path)
                payload = json.loads(Path(path).read_text(encoding="utf-8"))
                valid, restored = backup.validate_backup(path)

        self.assertEqual(clips, payload["clips"])
        self.assertNotIn("history", payload)
        self.assertNotIn("pinned", payload)
        self.assertTrue(valid)
        self.assertEqual(clips, restored)

    def test_backup_worker_reads_database_and_creates_valid_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "clipboard.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """CREATE TABLE clips (
                    id INTEGER PRIMARY KEY,
                    type TEXT,
                    content TEXT,
                    hash TEXT,
                    tag TEXT,
                    group_name TEXT,
                    is_pinned INTEGER,
                    pin_order INTEGER,
                    pinned_at TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )"""
            )
            conn.execute(
                """INSERT INTO clips VALUES
                   (1, 'text', 'worker clip', 'h1', '', '', 0, 0, NULL,
                    '2026-01-01T00:00:00', '2026-01-01T00:00:00')"""
            )
            conn.commit()
            conn.close()

            backup_dir = Path(tmp) / "backups"
            with patch.object(backup, "BACKUP_DIR", str(backup_dir)):
                self.assertTrue(
                    backup_worker.create_backup_from_database(str(db_path))
                )
                files = list(backup_dir.glob("*.json"))
                self.assertEqual(1, len(files))
                valid, restored = backup.validate_backup(str(files[0]))

        self.assertTrue(valid)
        self.assertEqual("worker clip", restored[0]["content"])

    def test_subprocess_backup_reports_worker_result(self):
        completed = type("Completed", (), {"returncode": 0})()
        with patch.object(backup.subprocess, "run", return_value=completed) as run:
            self.assertTrue(backup.create_backup_in_subprocess("C:/tmp/clipboard.db"))

        command = run.call_args.args[0]
        self.assertEqual(backup.sys.executable, command[0])
        self.assertEqual(["-m", "storage.backup_worker"], command[1:3])
        self.assertEqual("C:/tmp/clipboard.db", command[3])


if __name__ == "__main__":
    unittest.main()

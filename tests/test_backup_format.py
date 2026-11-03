import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from storage import backup
from storage import backup_worker


class _FakeTimer:
    def __init__(self, delay, callback):
        self.delay = delay
        self.callback = callback
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.callback()


class _TimerFactory:
    def __init__(self):
        self.timers = []

    def __call__(self, delay, callback):
        timer = _FakeTimer(delay, callback)
        self.timers.append(timer)
        return timer


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

    def test_cleanup_removes_only_old_matching_temp_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_temp = root / "clipboard_backup_20260101_000000.json.tmp"
            recent_temp = root / "clipboard_backup_20260905_120000.json.tmp"
            valid_backup = root / "clipboard_backup_20260905_120000.json"
            unrelated = root / "notes.json.tmp"
            for path in (old_temp, recent_temp, valid_backup, unrelated):
                path.write_text("data", encoding="utf-8")
            now = 200_000.0
            os.utime(old_temp, (now - 86_401, now - 86_401))
            os.utime(recent_temp, (now - 60, now - 60))
            with patch.object(backup, "BACKUP_DIR", str(root)):
                removed = backup.cleanup_stale_temp_backups(now=now)

            self.assertEqual([str(old_temp)], removed)
            self.assertTrue(recent_temp.exists())
            self.assertTrue(valid_backup.exists())
            self.assertTrue(unrelated.exists())

    def test_scheduler_repeated_schedule_collapses_to_one_debounce_timer(self):
        timers = _TimerFactory()
        clock = lambda: 1000.0
        scheduler = backup.BackupScheduler(lambda: None, clock=clock, timer_factory=timers)

        scheduler.schedule()
        scheduler.schedule()

        self.assertEqual(2, len(timers.timers))
        self.assertTrue(timers.timers[0].cancelled)
        self.assertTrue(timers.timers[1].started)
        self.assertEqual(backup.DEBOUNCE_SECONDS, timers.timers[1].delay)

    def test_scheduler_completed_run_starts_five_minute_cooldown(self):
        timers = _TimerFactory()
        now = [1000.0]
        runs = []
        scheduler = backup.BackupScheduler(
            lambda: runs.append(now[0]), clock=lambda: now[0], timer_factory=timers
        )
        scheduler.schedule()
        timers.timers[-1].fire()
        self.assertEqual([1000.0], runs)

        now[0] = 1010.0
        scheduler.schedule()

        self.assertEqual(290.0, timers.timers[-1].delay)

    def test_scheduler_does_not_overlap_and_schedules_one_followup(self):
        timers = _TimerFactory()
        now = [1000.0]
        started = threading.Event()
        release = threading.Event()
        runs = []

        def backup_func():
            runs.append(now[0])
            started.set()
            release.wait(1)

        scheduler = backup.BackupScheduler(
            backup_func, clock=lambda: now[0], timer_factory=timers
        )
        scheduler.schedule()
        worker = threading.Thread(target=timers.timers[-1].fire)
        worker.start()
        self.assertTrue(started.wait(1))
        scheduler.schedule()
        scheduler.schedule()
        self.assertEqual(1, len(runs))
        release.set()
        worker.join(1)

        self.assertEqual(2, len(timers.timers))
        self.assertEqual(backup.MIN_BACKUP_INTERVAL_SECONDS, timers.timers[-1].delay)

    def test_force_now_waits_for_active_run_then_executes_once(self):
        timers = _TimerFactory()
        now = [1000.0]
        first_started = threading.Event()
        release = threading.Event()
        runs = []

        def backup_func():
            runs.append(len(runs) + 1)
            if len(runs) == 1:
                first_started.set()
                release.wait(1)

        scheduler = backup.BackupScheduler(
            backup_func, clock=lambda: now[0], timer_factory=timers
        )
        scheduler.schedule()
        active = threading.Thread(target=timers.timers[-1].fire)
        active.start()
        self.assertTrue(first_started.wait(1))
        forced = threading.Thread(target=scheduler.force_now)
        forced.start()
        self.assertTrue(forced.is_alive())
        release.set()
        active.join(1)
        forced.join(1)

        self.assertEqual([1, 2], runs)

    def test_cancel_prevents_future_runs(self):
        timers = _TimerFactory()
        runs = []
        scheduler = backup.BackupScheduler(
            lambda: runs.append(True), clock=lambda: 1000.0, timer_factory=timers
        )
        scheduler.schedule()
        timer = timers.timers[-1]
        scheduler.cancel()
        timer.fire()
        scheduler.schedule()
        scheduler.force_now()

        self.assertEqual([], runs)

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

"""Backup/import regressions: every database and backup stays in a temp directory."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import storage
from storage import backup, backup_worker, clips, db


class BackupMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source_path = self.root / "source.db"
        self.source = self.make_database(self.source_path)
        self.addCleanup(self.source.close)
        self.repo = clips.ClipRepository()
        self.enterContext(patch.object(backup, "BACKUP_DIR", str(self.root / "backups")))
        self.enterContext(patch.object(storage, "get_storage", side_effect=AssertionError("No global storage")))
        # Any accidental unbound connection must fail instead of opening user data.
        self.enterContext(patch.object(db, "get_connection", side_effect=AssertionError("Explicit temp DB required")))
        self.enterContext(patch.object(clips, "get_connection", side_effect=AssertionError("Explicit temp DB required")))

    def make_database(self, path):
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        with patch.object(db, "get_connection", return_value=conn):
            db.init_db()
        return conn

    @contextmanager
    def bound(self, conn):
        with patch.object(db, "get_connection", return_value=conn), patch.object(clips, "get_connection", return_value=conn):
            yield self.repo

    def seed(self):
        with self.bound(self.source) as repo:
            configured, _, _ = repo.add_clip("text", "configured action", "work")
            plain, _, _ = repo.add_clip("text", "plain text")
            repo.pin_clip(configured)
            repo.update_group(configured, "tools")
            repo.assign_shortcut(configured, "K")
            repo.set_clip_action(configured, "command", "tool.exe", args=["--name", "two words", "日本語"], is_background=True)
        return configured, plain

    def test_worker_backup_roundtrip_preserves_metadata_and_unconfigured_clips(self):
        configured, plain = self.seed()
        with self.bound(self.source) as repo:
            expected = repo.get_all_clips()
        self.assertTrue(backup_worker.create_backup_from_database(str(self.source_path)))
        files = list((self.root / "backups").glob("*.json"))
        self.assertEqual(1, len(files))
        valid, payload = backup.validate_backup(str(files[0]))
        self.assertTrue(valid)
        self.assertEqual(expected, payload)

        restored = self.make_database(self.root / "restored.db")
        self.addCleanup(restored.close)
        with self.bound(restored) as repo:
            self.assertEqual(2, repo.import_clips(payload))
            restored_configured = repo.get_clip_by_hash(clips.compute_hash("configured action"))
            restored_plain = repo.get_clip_by_hash(clips.compute_hash("plain text"))
            cid = restored_configured["id"]
            self.assertEqual("K", repo.get_shortcut_for_clip(cid))
            action = repo.get_clip_action(cid)
            self.assertEqual("command", action["action_type"])
            self.assertEqual("tool.exe", action["target"])
            self.assertEqual(["--name", "two words", "日本語"], action["args"])
            self.assertTrue(action["is_background"])
            self.assertIsNone(repo.get_clip_action(restored_plain["id"]))
            self.assertIsNone(repo.get_shortcut_for_clip(restored_plain["id"]))
            self.assertEqual("tools", restored_configured["group_name"])
            self.assertTrue(restored_configured["is_pinned"])
        self.assertNotIn("action", next(c for c in payload if c["id"] == plain))
        self.assertIn("action", next(c for c in payload if c["id"] == configured))

    def test_ignored_insert_after_new_clip_attaches_metadata_to_exact_duplicate(self):
        with self.bound(self.source) as repo:
            existing, _, _ = repo.add_clip("text", "existing duplicate")
            imported = [
                {"content": "new clip", "shortcut_key": "N", "action": {"target": "new.exe", "args": ["new"], "is_background": False}},
                {"content": "existing duplicate", "shortcut_key": "D", "action": {"target": "duplicate.exe", "args": ["duplicate"], "is_background": True}},
            ]
            self.assertEqual(1, repo.import_clips(imported))
            new_id = repo.get_clip_by_hash(clips.compute_hash("new clip"))["id"]
            self.assertEqual("D", repo.get_shortcut_for_clip(existing))
            self.assertEqual("N", repo.get_shortcut_for_clip(new_id))
            self.assertEqual("duplicate.exe", repo.get_clip_action(existing)["target"])
            self.assertEqual(["duplicate"], repo.get_clip_action(existing)["args"])
            self.assertTrue(repo.get_clip_action(existing)["is_background"])
            self.assertEqual("new.exe", repo.get_clip_action(new_id)["target"])
            self.assertEqual(["new"], repo.get_clip_action(new_id)["args"])
            self.assertFalse(repo.get_clip_action(new_id)["is_background"])

    def test_repository_export_uses_one_read_snapshot_during_concurrent_metadata_change(self):
        configured, _ = self.seed()
        writer = sqlite3.connect(self.source_path)
        self.addCleanup(writer.close)
        changed = []

        def update_between_reads(sql):
            if "FROM clip_shortcuts" not in sql or changed:
                return
            changed.append(True)
            with writer:
                writer.execute("UPDATE clips SET content = 'changed' WHERE id = ?", (configured,))
                writer.execute("UPDATE clip_shortcuts SET shortcut_key = 'Z' WHERE clip_id = ?", (configured,))
                writer.execute("UPDATE clip_actions SET target = 'changed.exe' WHERE clip_id = ?", (configured,))

        self.source.set_trace_callback(update_between_reads)
        self.addCleanup(self.source.set_trace_callback, None)
        with self.bound(self.source) as repo:
            exported = repo.get_all_clips()
        self.assertTrue(changed, "Concurrent writer must run between clip and metadata reads")
        configured_clip = next(c for c in exported if c["id"] == configured)
        self.assertEqual("configured action", configured_clip["content"])
        self.assertEqual("K", configured_clip["shortcut_key"])
        self.assertEqual("tool.exe", configured_clip["action"]["target"])
        self.assertFalse(self.source.in_transaction, "Export must release its own read transaction")
        self.assertEqual("changed", writer.execute("SELECT content FROM clips WHERE id = ?", (configured,)).fetchone()[0])

    def test_legacy_payloads_without_metadata_still_import(self):
        for name, payload in (
            ("version1.json", {"version": 1, "pinned": [{"content": "legacy pinned", "is_pinned": True}], "history": [{"content": "legacy history"}]}),
            ("version2.json", {"version": 2, "clips": [{"content": "legacy v2"}]}),
        ):
            path = self.root / name
            path.write_text(json.dumps(payload), encoding="utf-8")
            valid, imported = backup.validate_backup(str(path))
            self.assertTrue(valid)
            with self.bound(self.source) as repo:
                self.assertEqual(len(imported), repo.import_clips(imported))
                for item in imported:
                    cid = repo.get_clip_by_hash(clips.compute_hash(item["content"]))["id"]
                    self.assertIsNone(repo.get_clip_action(cid))
                    self.assertIsNone(repo.get_shortcut_for_clip(cid))

    def test_worker_tolerates_each_missing_metadata_table(self):
        self.seed()
        cases = (("clip_shortcuts",), ("clip_actions",), ("clip_shortcuts", "clip_actions"))
        for index, missing in enumerate(cases):
            legacy_path = self.root / f"legacy{index}.db"
            with sqlite3.connect(legacy_path) as legacy:
                self.source.backup(legacy)
                for table in missing:
                    legacy.execute(f"DROP TABLE {table}")
            legacy.close()
            with self.subTest(missing=missing):
                self.assertTrue(backup_worker.create_backup_from_database(str(legacy_path)))
                paths = sorted((self.root / "backups").glob("*.json"))
                valid, exported = backup.validate_backup(str(paths[-1]))
                self.assertTrue(valid)
                self.assertEqual(2, len(exported))
                configured = next(c for c in exported if c["content"] == "configured action")
                if "clip_shortcuts" in missing:
                    self.assertNotIn("shortcut_key", configured)
                else:
                    self.assertEqual("K", configured["shortcut_key"])
                if "clip_actions" in missing:
                    self.assertNotIn("action", configured)
                else:
                    self.assertIn("action", configured)
                    self.assertEqual("tool.exe", configured["action"]["target"])

    def test_export_leaves_callers_uncommitted_transaction_owned_by_caller(self):
        configured, _ = self.seed()
        self.source.execute("UPDATE clips SET tag = 'pending' WHERE id = ?", (configured,))
        exported = clips.export_clips(self.source)
        self.assertTrue(self.source.in_transaction)
        self.assertEqual("pending", next(c for c in exported if c["id"] == configured)["tag"])
        self.source.rollback()
        self.assertEqual("work", self.source.execute("SELECT tag FROM clips WHERE id = ?", (configured,)).fetchone()[0])

    def test_export_releases_its_read_transaction_on_error(self):
        self.source.execute("DROP TABLE clip_actions")
        self.source.execute("CREATE TABLE clip_actions (wrong_column TEXT)")
        self.source.execute("INSERT INTO clip_actions VALUES ('broken schema')")
        self.source.commit()
        with self.assertRaises((IndexError, KeyError)):
            clips.export_clips(self.source)
        self.assertFalse(self.source.in_transaction)


if __name__ == "__main__":
    unittest.main()

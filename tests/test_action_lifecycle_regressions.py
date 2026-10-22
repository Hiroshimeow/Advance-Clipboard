import atexit
import os
import shutil
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt6 import sip
from PyQt6.QtWidgets import QApplication

from core.action_runner import ActionRunner, resolve_python_runner
from main import ClientApp
from storage import get_storage


class RunnerArgvTests(unittest.TestCase):
    def test_inline_and_script_argv_with_and_without_uv(self):
        script = os.path.abspath("task with spaces.py")
        for uv in (None, "uv.exe"):
            for kind, target in (("python_inline", "print('ok')"), ("script_file", script)):
                with self.subTest(uv=uv, kind=kind), patch("core.action_runner.shutil.which", return_value=uv), patch("core.action_runner.os.path.isfile", return_value=False), patch("core.action_runner.subprocess.Popen") as popen:
                    popen.return_value.communicate.return_value = ("ok", "")
                    popen.return_value.returncode = 0
                    ActionRunner._worker(9123, kind, target, ["hello world"], False, 5, None)
                    expected = ([uv, "run"] if uv else [sys.executable])
                    if kind == "python_inline":
                        expected += (["python"] if uv else []) + ["-c", target]
                    else:
                        expected += [script]
                    self.assertEqual(expected + ["hello world"], popen.call_args.args[0])

    def test_inline_real_subprocess_with_uv_present_and_absent(self):
        actual_uv = shutil.which("uv")
        for uv in (None, actual_uv):
            if uv is None and actual_uv is None:
                modes = "fallback"
            else:
                modes = uv
            with self.subTest(runner=modes), patch("core.action_runner.shutil.which", return_value=uv):
                results = []
                ActionRunner._worker(9124, "python_inline", "import sys; print(sys.argv[1])", ["ARG_OK"], False, 20, results.append)
                self.assertTrue(results[0].success, results[0].stderr)
                self.assertEqual("ARG_OK", results[0].stdout.strip())


class IngestDrainTests(unittest.TestCase):
    def test_stop_waits_for_blocked_and_queued_accepted_jobs(self):
        from core.clipboard_ingest import CaptureJob, ClipboardIngestBridge, IngestResult
        entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
        committed = []
        def process(job):
            entered.set()
            if not release.wait(10):
                raise AssertionError("worker was never released")
            committed.append(job.content)
            return IngestResult(True)
        bridge = ClipboardIngestBridge(SimpleNamespace(process=process))
        bridge.submit(CaptureJob.text("blocked"))
        self.assertTrue(entered.wait(2))
        bridge.submit(CaptureJob.text("queued"))
        stopper = threading.Thread(target=lambda: (bridge.stop(), stopped.set()))
        stopper.start()
        try:
            self.assertFalse(stopped.wait(3.2), "stop returned while accepted work could still commit")
        finally:
            release.set()
            stopper.join(5)
            bridge.stop()
        self.assertTrue(stopped.is_set())
        self.assertFalse(bridge.is_alive)
        self.assertEqual(["blocked", "queued"], committed)
        self.assertFalse(bridge.submit(CaptureJob.text("late")))


class IsolationPathTests(unittest.TestCase):
    def test_all_image_consumers_use_runner_sandbox(self):
        import main
        import storage.db as db
        import ui.widgets as widgets
        import ui.clip_delegate as delegate
        import ui.clip_row as row
        import ui.preview as preview
        sandbox = Path(db.DB_FILE).parent.resolve()
        for module in (main, widgets, delegate, row, preview):
            with self.subTest(module=module.__name__):
                self.assertEqual(sandbox / "images", Path(module.IMAGE_DIR).resolve())


class BackupDirtyRaceTests(unittest.TestCase):
    def test_duplicate_only_import_metadata_marks_dirty_and_notifies(self):
        store = get_storage()
        store.set_backup_callback(None)
        clip_id, _ = store.add_clip("text", "duplicate import revision regression")
        store.clear_backup_flag()
        before = store.backup_revision
        callback = Mock()
        store.set_backup_callback(callback)
        self.addCleanup(store.set_backup_callback, None)
        count = store.import_clips([{"type": "text", "content": "duplicate import revision regression", "shortcut_key": "Z", "action": {"action_type": "command", "target": "metadata-only"}}])
        self.assertEqual(0, count)
        self.assertEqual("Z", store.get_shortcut_for_clip(clip_id))
        self.assertEqual("metadata-only", store.get_clip_action(clip_id)["target"])
        self.assertTrue(store.need_backup)
        self.assertGreater(store.backup_revision, before)
        callback.assert_called_once()

    def test_backup_preserves_dirty_when_mutation_occurs_during_snapshot(self):
        store = get_storage()
        store.set_backup_callback(None)
        store._mark_dirty()
        with patch("main.create_backup_in_subprocess", side_effect=lambda: (store._mark_dirty() or True)):
            ClientApp._perform_backup(SimpleNamespace(storage=store))
        self.assertTrue(store.need_backup, "newer writes must remain dirty")

    def test_successful_unchanged_backup_clears_dirty_but_failure_does_not(self):
        store = get_storage()
        store.set_backup_callback(None)
        for succeeded in (False, True):
            with self.subTest(succeeded=succeeded):
                store._mark_dirty()
                with patch("main.create_backup_in_subprocess", return_value=succeeded):
                    ClientApp._perform_backup(SimpleNamespace(storage=store))
                self.assertEqual(not succeeded, store.need_backup)


class CleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def make_client(self):
        client = ClientApp(enable_monitor=False, init_data=False)
        client.backup_scheduler.cancel()
        client.backup_scheduler = Mock()
        client.backup_scheduler.force_now.side_effect = client.storage.clear_backup_flag
        self.addCleanup(atexit.unregister, client._cleanup_on_exit)
        return client

    def test_failed_final_backup_preserves_retry_until_success(self):
        from storage.backup import BackupScheduler
        for failure in (False, OSError("backup worker failed")):
            with self.subTest(failure=failure):
                client = self.make_client()
                client.storage.set_backup_callback(None)
                client.backup_scheduler = BackupScheduler(client._perform_backup)
                client.storage.set_backup_callback(client.backup_scheduler.schedule)
                self.addCleanup(client.backup_scheduler.cancel)
                client.storage._mark_dirty()
                with patch("main.create_backup_in_subprocess", side_effect=[failure, True]) as worker, patch("main.atexit.unregister") as unregister, patch.object(client.backup_scheduler, "cancel", wraps=client.backup_scheduler.cancel) as cancel:
                    client._cleanup_on_exit()
                    self.assertTrue(client.storage.need_backup)
                    self.assertFalse(client._cleanup_complete)
                    self.assertFalse(client._cleanup_started)
                    self.assertEqual(client.backup_scheduler.schedule, client.storage._backup_callback)
                    cancel.assert_not_called()
                    unregister.assert_not_called()
                    client._cleanup_on_exit()
                    self.assertFalse(client.storage.need_backup)
                    self.assertTrue(client._cleanup_complete)
                    self.assertEqual(2, worker.call_count)
                    cancel.assert_called_once()
                    unregister.assert_called_once_with(client._cleanup_on_exit)
                    self.assertIsNone(client.storage._backup_callback)
                client.close()

    def test_cleanup_survives_deleted_qt_wrappers_and_is_idempotent(self):
        client = self.make_client()
        monitor = client.win32_monitor = Mock()
        bridge = client.ingest_bridge
        stop = Mock(wraps=bridge.stop)
        bridge.stop = stop
        sip.delete(client)
        client._cleanup_on_exit()
        client._cleanup_on_exit()
        monitor.stop.assert_called_once()
        stop.assert_called_once()
        client.backup_scheduler.cancel.assert_called_once()

    def test_shutdown_search_stops_all_timers_even_if_one_is_deleted(self):
        client = self.make_client()
        browser = client.browser
        sip.delete(browser._search_result_timer)
        browser._refresh_result_timer.start(10000)
        browser._layout_refresh_timer.start(10000)
        browser.shutdown_search()
        self.assertFalse(browser._refresh_result_timer.isActive())
        self.assertFalse(browser._layout_refresh_timer.isActive())
        client._cleanup_on_exit()
        client.close()

    def test_cleanup_flushes_pending_deletes_before_backup_without_ui_refresh(self):
        client = self.make_client()
        clip_id, _ = client.storage.add_clip("text", "pending shutdown delete")
        client._pending_delete_clip_ids.add(clip_id)
        client.refresh_lists = Mock(side_effect=AssertionError("shutdown must not refresh UI"))
        def successful_backup():
            self.assertIsNone(client.storage.get_clip_by_id(clip_id))
            client.storage.clear_backup_flag()
        client.backup_scheduler.force_now.side_effect = successful_backup
        with patch("main.atexit.unregister") as unregister:
            client._cleanup_on_exit()
        client.backup_scheduler.force_now.assert_called_once()
        unregister.assert_called_once_with(client._cleanup_on_exit)
        self.assertFalse(client._pending_delete_clip_ids)
        client.close()

    def test_cleanup_retries_failed_delete_before_final_backup(self):
        client = self.make_client()
        clip_id, _ = client.storage.add_clip("text", "retry shutdown delete")
        client._pending_delete_clip_ids.add(clip_id)
        def successful_backup():
            self.assertIsNone(client.storage.get_clip_by_id(clip_id))
            client.storage.clear_backup_flag()
        client.backup_scheduler.force_now.side_effect = successful_backup
        with patch.object(client.storage, "delete_clip", side_effect=OSError("transient")), patch("main.atexit.unregister") as unregister:
            client._cleanup_on_exit()
            self.assertFalse(client._cleanup_complete)
            self.assertIn(clip_id, client._pending_delete_clip_ids)
            client.backup_scheduler.force_now.assert_not_called()
            client.backup_scheduler.cancel.assert_not_called()
            unregister.assert_not_called()
        client._cleanup_on_exit()
        self.assertTrue(client._cleanup_complete)
        self.assertFalse(client._pending_delete_clip_ids)
        client.backup_scheduler.force_now.assert_called_once()
        self.assertIsNone(client.storage.get_clip_by_id(clip_id))
        client.close()

    def test_cleanup_does_not_finalize_if_ingest_is_still_alive(self):
        client = self.make_client()
        bridge = client.ingest_bridge
        with patch.object(bridge, "stop"), patch("main.atexit.unregister") as unregister:
            client._cleanup_on_exit()
            self.assertFalse(client._cleanup_complete)
            client.backup_scheduler.force_now.assert_not_called()
            client.backup_scheduler.cancel.assert_not_called()
            unregister.assert_not_called()
        client._cleanup_on_exit()
        self.assertTrue(client._cleanup_complete)
        client.close()

    def test_about_to_quit_runs_cleanup(self):
        client = self.make_client()
        self.qt_app.aboutToQuit.emit()
        self.assertTrue(getattr(client, "_cleanup_complete", False))
        client.close()

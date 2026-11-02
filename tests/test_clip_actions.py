"""
Unit tests for Clip Actions (RUN and Ctrl+Enter):
- Python inline code execution
- Script file execution (.py via uv run, requires full path)
- Command argv execution
- URL opening detection
- Async execution, non-blocking, CWD = script parent / app root
- Normal copy/paste/preview NEVER executes actions
- Cascade delete cleans actions
"""

import os
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from core.action_runner import ActionRunner, is_valid_url, open_url
from storage import get_storage


class ClipActionTests(unittest.TestCase):
    def setUp(self):
        self.storage = get_storage()
        self.storage.clear_history()
        self.storage.clear_pinned()
        self.clip_id, _ = self.storage.add_clip("text", "Sample Action Clip")

    def test_validate_action_config(self):
        # script_file requires full absolute path
        ok, msg = ActionRunner.validate_action_config("script_file", "relative/path.py")
        self.assertFalse(ok)
        self.assertIn("tuyệt đối", msg)

        # script_file with full path is valid
        abs_path = os.path.abspath("test_script.py")
        ok, msg = ActionRunner.validate_action_config("script_file", abs_path)
        self.assertTrue(ok)

        # python_inline valid syntax
        ok, msg = ActionRunner.validate_action_config("python_inline", "print('hello')")
        self.assertTrue(ok)

        # python_inline syntax error
        ok, msg = ActionRunner.validate_action_config("python_inline", "def broken(:")
        self.assertFalse(ok)
        self.assertIn("cú pháp", msg)

        # URL validation
        ok, msg = ActionRunner.validate_action_config("url", "https://google.com")
        self.assertTrue(ok)
        ok, msg = ActionRunner.validate_action_config("url", "not_a_url")
        self.assertFalse(ok)

    def test_set_and_get_clip_action(self):
        abs_path = os.path.abspath("my_script.py")
        ok = self.storage.set_clip_action(
            self.clip_id,
            "script_file",
            abs_path,
            args=["--flag", "value"],
            is_background=False,
        )
        self.assertTrue(ok)

        act = self.storage.get_clip_action(self.clip_id)
        self.assertIsNotNone(act)
        self.assertEqual(act["action_type"], "script_file")
        self.assertEqual(act["target"], abs_path)
        self.assertEqual(act["args"], ["--flag", "value"])
        self.assertFalse(act["is_background"])

    def test_execute_python_inline_real_subprocess(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tf:
            out_file = tf.name

        code = f"""
import sys, os
with open(r'{out_file}', 'w') as f:
    f.write('INLINE_EXEC_OK')
"""
        action = {
            "action_type": "python_inline",
            "target": code,
            "args": [],
            "is_background": False,
        }

        completed = [False]
        res_holder = []

        def on_done(res):
            completed[0] = True
            res_holder.append(res)

        ok = ActionRunner.execute_action(self.clip_id, action, on_complete=on_done, timeout=10.0)
        self.assertTrue(ok)

        # Wait for async execution
        start = time.time()
        while not completed[0] and (time.time() - start) < 10.0:
            time.sleep(0.05)

        self.assertTrue(completed[0])
        self.assertTrue(res_holder[0].success)

        with open(out_file, "r") as f:
            content = f.read()
        self.assertEqual(content, "INLINE_EXEC_OK")

        try:
            os.remove(out_file)
        except Exception:
            pass

    def test_execute_script_file_runs_in_script_cwd(self):
        # Create temp directory and script inside it
        temp_dir = tempfile.mkdtemp()
        script_file = os.path.join(temp_dir, "test_task.py")
        out_file = os.path.join(temp_dir, "output.txt")

        with open(script_file, "w") as f:
            f.write(f"""
import os
with open('{out_file}', 'w') as f:
    f.write(os.getcwd())
""")

        action = {
            "action_type": "script_file",
            "target": script_file,
            "args": [],
            "is_background": False,
        }

        completed = [False]
        def on_done(res):
            completed[0] = True

        ok = ActionRunner.execute_action(self.clip_id, action, on_complete=on_done, timeout=10.0)
        self.assertTrue(ok)

        start = time.time()
        while not completed[0] and (time.time() - start) < 10.0:
            time.sleep(0.05)

        self.assertTrue(completed[0])
        with open(out_file, "r") as f:
            cwd_written = f.read().strip()
        self.assertEqual(cwd_written, os.path.abspath(temp_dir))

        # Cleanup
        try:
            os.remove(script_file)
            os.remove(out_file)
            os.rmdir(temp_dir)
        except Exception:
            pass

    def test_url_detection(self):
        self.assertTrue(is_valid_url("https://github.com"))
        self.assertTrue(is_valid_url("http://localhost:8080/api"))
        self.assertFalse(is_valid_url("Just plain text"))
        self.assertFalse(is_valid_url("C:\\path\\file.txt"))

    def test_cascade_delete_cleans_action(self):
        self.storage.set_clip_action(
            self.clip_id, "command", "notepad.exe", args=[]
        )
        self.assertIsNotNone(self.storage.get_clip_action(self.clip_id))

        self.storage.delete_clip(self.clip_id)
        self.assertIsNone(self.storage.get_clip_action(self.clip_id))



    def test_save_and_get_url_action_in_storage_and_dialog(self):
        # Regression test for ValueError: Loại action không hợp lệ: url
        ok = self.storage.set_clip_action(
            self.clip_id,
            "url",
            "https://example.com/docs",
            args=[],
            is_background=False,
        )
        self.assertTrue(ok)
        act = self.storage.get_clip_action(self.clip_id)
        self.assertIsNotNone(act)
        self.assertEqual(act["action_type"], "url")
        self.assertEqual(act["target"], "https://example.com/docs")
        self.assertIn(self.clip_id, self.storage.get_action_clip_ids())



    def test_stop_running_action_and_live_terminal_log(self):
        code = """
import sys, time
print('LIVE_LOG_LINE_1', flush=True)
print('LIVE_LOG_LINE_2', flush=True)
time.sleep(15)
"""
        action = {
            "action_type": "python_inline",
            "target": code,
            "args": [],
            "is_background": False,
        }
        ok = ActionRunner.execute_action(self.clip_id, action)
        self.assertTrue(ok)

        # Wait until process is running and prints live lines
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if ActionRunner.is_running(self.clip_id) and "LIVE_LOG_LINE_2" in ActionRunner.get_action_log(self.clip_id):
                break
            time.sleep(0.05)

        self.assertTrue(ActionRunner.is_running(self.clip_id))
        self.assertIn(self.clip_id, ActionRunner.get_running_clip_ids())
        live_log = ActionRunner.get_action_log(self.clip_id)
        self.assertIn("LIVE_LOG_LINE_1", live_log)
        self.assertIn("LIVE_LOG_LINE_2", live_log)

        # Stop the running action
        stopped = ActionRunner.stop_action(self.clip_id)
        self.assertTrue(stopped)
        self.assertFalse(ActionRunner.is_running(self.clip_id))
        self.assertNotIn(self.clip_id, ActionRunner.get_running_clip_ids())
        self.assertIn("STOP Action", ActionRunner.get_action_log(self.clip_id))

    def test_running_action_pinned_at_position_4_in_history_and_pinned(self):
        from PyQt6.QtWidgets import QApplication
        from main import ClientApp

        app_inst = QApplication.instance() or QApplication([])
        self.storage.clear_history()
        self.storage.clear_pinned()

        h_ids = []
        for i in range(1, 7):
            cid, _ = self.storage.add_clip("text", f"hist_clip_{i}")
            h_ids.append(cid)

        p_ids = []
        for i in range(1, 7):
            cid, _ = self.storage.add_clip("text", f"pin_clip_{i}")
            self.storage.pin_clip(cid)
            p_ids.append(cid)

        # Oldest history is h_ids[0], oldest pinned is p_ids[0]
        running_h_id = h_ids[0]
        running_p_id = p_ids[0]

        client = ClientApp(enable_monitor=False, init_data=True)
        try:
            with patch.object(ActionRunner, "get_running_clip_ids", return_value=[running_h_id, running_p_id]),                  patch.object(ActionRunner, "is_running", side_effect=lambda cid: cid in (running_h_id, running_p_id)):
                client.refresh_lists()
                h_rows = client.list_history.rows()
                p_rows = [r for r in client.list_pinned.rows() if r.is_clip]

                # Position #4 is 0-based index 3 (right after Alt+1..3 / Alt+4..6)
                self.assertEqual(h_rows[3].clip_id, running_h_id)
                self.assertTrue(h_rows[3].clip.get("is_action_running"))
                self.assertEqual(p_rows[3].clip_id, running_p_id)
                self.assertTrue(p_rows[3].clip.get("is_action_running"))
        finally:
            client.backup_scheduler.cancel()
            client.close()

    def test_action_and_ctrl_search_filters(self):
        self.storage.clear_history()
        self.storage.clear_pinned()

        c1, _ = self.storage.add_clip("text", "normal clip")
        c2, _ = self.storage.add_clip("text", "script runner clip")
        c3, _ = self.storage.add_clip("text", "pinned action clip")
        self.storage.pin_clip(c3)
        c4, _ = self.storage.add_clip("text", "shortcut clip history")
        c5, _ = self.storage.add_clip("text", "shortcut clip pinned")
        self.storage.pin_clip(c5)

        self.storage.set_clip_action(c2, "python_inline", "print(1)")
        self.storage.set_clip_action(c3, "url", "https://example.com")
        self.storage.assign_shortcut(c4, "Q")
        self.storage.assign_shortcut(c5, "1")

        # Test 'action' and 'action:'
        for q in ("action", "action:", "actions", "actions:"):
            self.assertTrue(self.storage.is_filter_query(q))
            h_res = [r["id"] for r in self.storage.search_history(q)]
            p_res = [r["id"] for r in self.storage.search_pinned(q)]
            self.assertEqual(h_res, [c2])
            self.assertEqual(p_res, [c3])

        # Test 'ctrl' and 'ctrl:'
        for q in ("ctrl", "ctrl:", "shortcut", "shortcut:"):
            self.assertTrue(self.storage.is_filter_query(q))
            h_res = [r["id"] for r in self.storage.search_history(q)]
            p_res = [r["id"] for r in self.storage.search_pinned(q)]
            self.assertEqual(h_res, [c4])
            self.assertEqual(p_res, [c5])

        # Test 'ctrl: q' keyword filter
        self.assertEqual([r["id"] for r in self.storage.search_history("ctrl: q")], [c4])
        self.assertEqual([r["id"] for r in self.storage.search_pinned("ctrl: q")], [])

    def test_popups_have_window_stays_on_top_hint(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QApplication
        from ui.widgets import ShortcutAssignDialog, ActionConfigDialog, ActionLogDialog, ClipEditPopup

        app_inst = QApplication.instance() or QApplication([])
        clip = {"id": self.clip_id, "type": "text", "content": "test popup z-order"}
        for dlg_cls in (
            lambda: ShortcutAssignDialog(clip, self.storage),
            lambda: ActionConfigDialog(clip, self.storage),
            lambda: ActionLogDialog(clip),
            lambda: ClipEditPopup(clip),
        ):
            dlg = dlg_cls()
            try:
                flags = dlg.windowFlags()
                self.assertTrue(bool(flags & Qt.WindowType.WindowStaysOnTopHint))
            finally:
                dlg.close()


if __name__ == "__main__":
    unittest.main()

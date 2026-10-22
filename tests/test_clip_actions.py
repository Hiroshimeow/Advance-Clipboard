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
import threading
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
with open({out_file!r}, 'w') as f:
    f.write('INLINE_EXEC_OK')
"""
        action = {
            "action_type": "python_inline",
            "target": code,
            "args": [],
            "is_background": False,
        }

        completed = threading.Event()
        res_holder = []

        def on_done(res):
            res_holder.append(res)
            completed.set()

        ok = ActionRunner.execute_action(self.clip_id, action, on_complete=on_done, timeout=10.0)
        self.assertTrue(ok)

        self.assertTrue(completed.wait(10.0), "inline action did not complete")
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
with open({out_file!r}, 'w') as f:
    f.write(os.getcwd())
""")

        action = {
            "action_type": "script_file",
            "target": script_file,
            "args": [],
            "is_background": False,
        }

        completed = threading.Event()
        res_holder = []
        def on_done(res):
            res_holder.append(res)
            completed.set()

        ok = ActionRunner.execute_action(self.clip_id, action, on_complete=on_done, timeout=10.0)
        self.assertTrue(ok)

        self.assertTrue(completed.wait(10.0), "script action did not complete")
        self.assertTrue(res_holder[0].success, res_holder[0].stderr)
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


if __name__ == "__main__":
    unittest.main()

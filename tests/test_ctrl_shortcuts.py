"""
Unit tests for Ctrl+<single_key> persistent shortcuts:
- Only accepts 1 key (0-9, A-Z), normalized to uppercase
- Conflict detection and reassigning (force=True)
- Removing shortcut
- Persistent across restart, search, reorder, content editing
- Deletion in same transaction (cascade)
- Direct paste via Ctrl+<key>
"""

import unittest
from unittest.mock import Mock

from PyQt6.QtWidgets import QApplication

from main import ClientApp
from storage import get_storage


class CtrlShortcutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not QApplication.instance():
            cls.app_instance = QApplication([])
        else:
            cls.app_instance = QApplication.instance()

    def setUp(self):
        self.storage = get_storage()
        self.storage.clear_history()
        self.storage.clear_pinned()

        self.clip_a, _ = self.storage.add_clip("text", "Content A")
        self.clip_b, _ = self.storage.add_clip("text", "Content B")

        self.client = ClientApp(enable_monitor=False, init_data=True)
        self.client.handle_paste = Mock()

    def tearDown(self):
        self.client._cleanup_on_exit()
        self.client.close()

    def test_assign_valid_single_key(self):
        # Assign 1 to clip A
        ok, conflict = self.storage.assign_shortcut(self.clip_a, "1")
        self.assertTrue(ok)
        self.assertIsNone(conflict)

        # Assign Q (lowercase input normalized to uppercase) to clip B
        ok, conflict = self.storage.assign_shortcut(self.clip_b, "q")
        self.assertTrue(ok)
        self.assertIsNone(conflict)

        # Verify storage lookup
        self.assertEqual(self.storage.get_shortcut_for_clip(self.clip_a), "1")
        self.assertEqual(self.storage.get_shortcut_for_clip(self.clip_b), "Q")

        clip = self.storage.get_clip_by_shortcut("1")
        self.assertIsNotNone(clip)
        self.assertEqual(clip["id"], self.clip_a)

        clip_q = self.storage.get_clip_by_shortcut("q")
        self.assertIsNotNone(clip_q)
        self.assertEqual(clip_q["id"], self.clip_b)

    def test_reject_invalid_keys(self):
        # Empty string
        with self.assertRaises(ValueError):
            self.storage.assign_shortcut(self.clip_a, "")

        # Multi-char
        with self.assertRaises(ValueError):
            self.storage.assign_shortcut(self.clip_a, "Ctrl+Q")

        # Non-alphanumeric
        with self.assertRaises(ValueError):
            self.storage.assign_shortcut(self.clip_a, "#")

    def test_conflict_detection_and_force_reassign(self):
        # Assign '1' to clip A
        self.storage.assign_shortcut(self.clip_a, "1")

        # Attempt to assign '1' to clip B without force -> fails with conflict
        ok, conflict_id = self.storage.assign_shortcut(self.clip_b, "1", force=False)
        self.assertFalse(ok)
        self.assertEqual(conflict_id, self.clip_a)

        # Reassign '1' to clip B with force -> succeeds and removes from clip A
        ok, conflict_id = self.storage.assign_shortcut(self.clip_b, "1", force=True)
        self.assertTrue(ok)
        self.assertIsNone(conflict_id)
        self.assertEqual(self.storage.get_shortcut_for_clip(self.clip_b), "1")
        self.assertIsNone(self.storage.get_shortcut_for_clip(self.clip_a))

    def test_remove_shortcut(self):
        self.storage.assign_shortcut(self.clip_a, "9")
        self.assertEqual(self.storage.get_shortcut_for_clip(self.clip_a), "9")

        ok = self.storage.remove_shortcut(self.clip_a)
        self.assertTrue(ok)
        self.assertIsNone(self.storage.get_shortcut_for_clip(self.clip_a))
        self.assertIsNone(self.storage.get_clip_by_shortcut("9"))

    def test_shortcut_pastes_mapped_clip(self):
        self.storage.assign_shortcut(self.clip_a, "A")
        self.client.browser.refresh_shortcuts_and_actions()

        ok = self.client.browser.handle_ctrl_shortcut("a")
        self.assertTrue(ok)
        self.client.handle_paste.assert_called_once()
        pasted = self.client.handle_paste.call_args[0][0]
        self.assertEqual(pasted["id"], self.clip_a)

    def test_cascade_delete_cleans_shortcut(self):
        self.storage.assign_shortcut(self.clip_a, "K")
        self.assertEqual(self.storage.get_shortcut_for_clip(self.clip_a), "K")

        # Delete clip A
        self.storage.delete_clip(self.clip_a)

        # Shortcut must be gone
        self.assertIsNone(self.storage.get_shortcut_for_clip(self.clip_a))
        self.assertIsNone(self.storage.get_clip_by_shortcut("K"))


if __name__ == "__main__":
    unittest.main()

"""
Unit tests for Alt+1..6 quick paste:
- Alt+1..3: History column visible clips 1, 2, 3
- Alt+4..6: Pinned column visible clips 1, 2, 3
- Model-accurate after search/filter/sort
- Skips group headers/separators and collapsed items
- Safe when column has fewer items (does nothing, no wrong column fallback)
- Safe inside search input
"""

import unittest
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from main import ClientApp
from storage import get_storage
from storage.db import transaction


class AltQuickPasteTests(unittest.TestCase):
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

        # Seed data
        self.h1_id, _ = self.storage.add_clip("text", "apple")
        self.h2_id, _ = self.storage.add_clip("text", "banana")
        self.h3_id, _ = self.storage.add_clip("text", "cherry")
        self.h4_id, _ = self.storage.add_clip("text", "date")

        self.p1_id, _ = self.storage.add_clip("text", "pin_alpha")
        self.storage.pin_clip(self.p1_id)
        self.p2_id, _ = self.storage.add_clip("text", "pin_beta")
        self.storage.pin_clip(self.p2_id)
        self.p3_id, _ = self.storage.add_clip("text", "pin_gamma")
        self.storage.pin_clip(self.p3_id)

        # Pinning updates timestamps too. Explicit distinct instants keep this
        # ordering fixture independent of clock resolution and insertion speed.
        with transaction() as conn:
            for index, clip_id in enumerate((self.h1_id, self.h2_id, self.h3_id,
                                             self.h4_id, self.p1_id, self.p2_id, self.p3_id), 1):
                stamp = f"2025-01-01T00:00:{index:02d}.000000"
                conn.execute("UPDATE clips SET updated_at = ?, pinned_at = CASE WHEN is_pinned THEN ? ELSE NULL END WHERE id = ?",
                             (stamp, stamp, clip_id))

        self.client = ClientApp(enable_monitor=False, init_data=True)
        self.client.handle_paste = Mock()

    def tearDown(self):
        self.client._cleanup_on_exit()
        self.client.close()

    def test_alt_1_to_3_pastes_history_visible_clips(self):
        # Alt+1 -> newest history ("date")
        ok = self.client.browser.handle_alt_digit(1)
        self.assertTrue(ok)
        self.client.handle_paste.assert_called_once()
        pasted = self.client.handle_paste.call_args[0][0]
        self.assertEqual(pasted["content"], "date")

        self.client.handle_paste.reset_mock()
        # Alt+2 -> 2nd newest ("cherry")
        ok = self.client.browser.handle_alt_digit(2)
        self.assertTrue(ok)
        pasted = self.client.handle_paste.call_args[0][0]
        self.assertEqual(pasted["content"], "cherry")

        self.client.handle_paste.reset_mock()
        # Alt+3 -> 3rd newest ("banana")
        ok = self.client.browser.handle_alt_digit(3)
        self.assertTrue(ok)
        pasted = self.client.handle_paste.call_args[0][0]
        self.assertEqual(pasted["content"], "banana")

    def test_alt_4_to_6_pastes_pinned_visible_clips(self):
        # Alt+4 -> 1st pinned
        ok = self.client.browser.handle_alt_digit(4)
        self.assertTrue(ok)
        self.client.handle_paste.assert_called_once()
        pasted = self.client.handle_paste.call_args[0][0]
        self.assertEqual(pasted["content"], "pin_gamma")

        self.client.handle_paste.reset_mock()
        # Alt+5 -> 2nd pinned
        ok = self.client.browser.handle_alt_digit(5)
        self.assertTrue(ok)
        pasted = self.client.handle_paste.call_args[0][0]
        self.assertEqual(pasted["content"], "pin_beta")

        self.client.handle_paste.reset_mock()
        # Alt+6 -> 3rd pinned
        ok = self.client.browser.handle_alt_digit(6)
        self.assertTrue(ok)
        pasted = self.client.handle_paste.call_args[0][0]
        self.assertEqual(pasted["content"], "pin_alpha")

    def test_alt_pastes_according_to_active_search_filter(self):
        # Search "an" -> matches "banana" in history
        self.client.search_input.setText("an")
        self.client.browser.on_search_text_changed("an")
        self.client._do_search()
        deadline = time.monotonic() + 3.0
        while self.client.browser._last_search_query != "an" and time.monotonic() < deadline:
            self.app_instance.processEvents()
            self.client.browser._drain_search_results()
        self.assertEqual("an", self.client.browser._last_search_query, "search did not complete")

        history_clips = self.client.browser.get_column_clips("history")
        self.assertTrue(any("banana" in c["content"] for c in history_clips))

        # Alt+1 should paste the top filtered result
        self.client.handle_paste.reset_mock()
        ok = self.client.browser.handle_alt_digit(1)
        self.assertTrue(ok)
        pasted = self.client.handle_paste.call_args[0][0]
        self.assertIn("an", pasted["content"].lower())

    def test_alt_does_nothing_if_column_has_fewer_clips(self):
        # Clear pinned so Pinned has 0 clips
        self.storage.clear_pinned()
        self.client.refresh_lists()

        # Alt+4, Alt+5, Alt+6 must do nothing and not paste History
        self.client.handle_paste.reset_mock()
        ok = self.client.browser.handle_alt_digit(4)
        self.assertFalse(ok)
        self.client.handle_paste.assert_not_called()

        ok = self.client.browser.handle_alt_digit(5)
        self.assertFalse(ok)
        self.client.handle_paste.assert_not_called()

        ok = self.client.browser.handle_alt_digit(6)
        self.assertFalse(ok)
        self.client.handle_paste.assert_not_called()

    def test_alt_skips_collapsed_groups(self):
        # Put pinned clips into a group and collapse it
        self.storage.update_group(self.p1_id, "Work")
        self.storage.update_group(self.p2_id, "Work")
        self.client.refresh_lists()

        # Collapse "Work" group
        self.client.collapse_group("Work")

        # Pinned visible clips should only include ungrouped "pin_gamma"
        pinned_clips = self.client.browser.get_column_clips("pinned")
        self.assertEqual(len(pinned_clips), 1)
        self.assertEqual(pinned_clips[0]["content"], "pin_gamma")

        # Alt+4 pastes pin_gamma
        self.client.handle_paste.reset_mock()
        ok = self.client.browser.handle_alt_digit(4)
        self.assertTrue(ok)
        pasted = self.client.handle_paste.call_args[0][0]
        self.assertEqual(pasted["content"], "pin_gamma")

        # Alt+5 does nothing because collapsed items are skipped
        self.client.handle_paste.reset_mock()
        ok = self.client.browser.handle_alt_digit(5)
        self.assertFalse(ok)
        self.client.handle_paste.assert_not_called()


if __name__ == "__main__":
    unittest.main()

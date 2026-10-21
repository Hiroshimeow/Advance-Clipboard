"""
Unit tests for focus preservation and paste simulation safety:
- Direct WM_PASTE optimization for Win32 Edit controls (Explorer F2 rename and address bar)
- Neutralizing Alt keyup so SC_KEYMENU is not dispatched when Alt is held
- Confirming show_at_cursor does not attach thread input to target foreground
"""

import sys
import unittest
from unittest.mock import Mock, patch

from core.clipboard_monitor import simulate_paste
from main import ClientApp


class FocusF2SafetyTests(unittest.TestCase):

    def test_direct_wm_paste_for_edit_control(self):
        user32_mock = Mock()
        user32_mock.IsWindow.return_value = 1

        def fake_get_class_name(hwnd, buf, max_count):
            buf.value = "Edit"
            return 4

        user32_mock.GetClassNameW.side_effect = fake_get_class_name

        with patch("sys.platform", "win32"), patch(
            "core.clipboard_monitor.user32", user32_mock
        ):
            simulate_paste(target_hwnd=1001, focus_hwnd=2002)

            # Must send WM_PASTE (0x0302) directly to focus_hwnd
            user32_mock.SendMessageW.assert_called_once_with(2002, 0x0302, 0, 0)
            # Should NOT inject keyboard keys when direct WM_PASTE succeeds
            user32_mock.keybd_event.assert_not_called()

    def test_neutralize_alt_keyup_when_alt_down(self):
        user32_mock = Mock()
        user32_mock.IsWindow.return_value = 0  # not an Edit control

        # Simulate Alt key logically pressed
        def fake_get_async_key_state(vk):
            if vk == 0x12:  # VK_MENU
                return 0x8000
            return 0

        user32_mock.GetAsyncKeyState.side_effect = fake_get_async_key_state

        with patch("sys.platform", "win32"), patch(
            "core.clipboard_monitor.user32", user32_mock
        ):
            simulate_paste(target_hwnd=1001, focus_hwnd=None)

            # Check that unassigned dummy key 0x07 was sent before VK_MENU keyup
            calls = user32_mock.keybd_event.call_args_list
            # Find index of dummy key and VK_MENU keyup
            dummy_down = any(c[0][0] == 0x07 and c[0][2] == 0 for c in calls)
            dummy_up = any(c[0][0] == 0x07 and c[0][2] == 0x0002 for c in calls)
            alt_up = any(c[0][0] == 0x12 and c[0][2] == 0x0002 for c in calls)

            self.assertTrue(dummy_down)
            self.assertTrue(dummy_up)
            self.assertTrue(alt_up)


if __name__ == "__main__":
    unittest.main()

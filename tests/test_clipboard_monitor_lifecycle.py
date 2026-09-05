import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from core.clipboard_monitor import (
    HOTKEY_TOGGLE,
    WM_APP_QUIT,
    WM_CLIPBOARDUPDATE,
    Win32ClipboardMonitor,
)


def make_monitor(*, create_window_result=101, messages=()):
    queued_messages = iter(messages)

    def get_message(message_ptr, _hwnd, _minimum, _maximum):
        try:
            message_ptr._obj.message = next(queued_messages)
            return 1
        except StopIteration:
            return 0

    user32 = SimpleNamespace(
        RegisterClassExW=Mock(return_value=1),
        CreateWindowExW=Mock(return_value=create_window_result),
        AddClipboardFormatListener=Mock(return_value=1),
        RegisterHotKey=Mock(return_value=1),
        GetMessageW=Mock(side_effect=get_message),
        TranslateMessage=Mock(return_value=1),
        DispatchMessageW=Mock(return_value=0),
        RemoveClipboardFormatListener=Mock(return_value=1),
        UnregisterHotKey=Mock(return_value=1),
        DestroyWindow=Mock(return_value=1),
        UnregisterClassW=Mock(return_value=1),
        DefWindowProcW=Mock(return_value=77),
        PostThreadMessageW=Mock(return_value=1),
    )
    kernel32 = SimpleNamespace(
        GetCurrentThreadId=Mock(return_value=12),
        GetModuleHandleW=Mock(return_value=34),
        GetLastError=Mock(return_value=5),
    )
    return Win32ClipboardMonitor(user32_api=user32, kernel32_api=kernel32), user32, kernel32


class ClipboardMonitorLifecycleTests(unittest.TestCase):
    def test_create_window_failure_unregisters_class_and_resets_state(self):
        monitor, user32, _kernel32 = make_monitor(create_window_result=0)
        monitor._run_message_loop()
        self.assertEqual("stopped", monitor.state)
        user32.UnregisterClassW.assert_called_once()
        user32.DestroyWindow.assert_not_called()

    def test_successful_loop_releases_only_acquired_resources(self):
        monitor, user32, _kernel32 = make_monitor(messages=[WM_APP_QUIT])
        monitor._run_message_loop()
        user32.RemoveClipboardFormatListener.assert_called_once()
        user32.UnregisterHotKey.assert_called_once()
        user32.DestroyWindow.assert_called_once()
        user32.UnregisterClassW.assert_called_once()

    def test_wndproc_contains_python_exception(self):
        monitor, user32, _kernel32 = make_monitor()
        monitor._emit_clipboard_changed = Mock(side_effect=RuntimeError("signal failure"))
        result = monitor._wndproc(123, WM_CLIPBOARDUPDATE, 0, 0)
        self.assertEqual(user32.DefWindowProcW.return_value, result)


if __name__ == "__main__":
    unittest.main()

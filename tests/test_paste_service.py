import sys
import unittest
from unittest.mock import patch

from core.paste_service import PasteService


class _FakeUser32:
    def __init__(self):
        self.foreground = 100
        self.target = 200
        self.ctrl_down = False
        self.alt_down = False
        self.invalid_windows = set()
        self.iconic_windows = set()
        self.set_foreground_succeeds = True
        self.attach_thread_succeeds = True
        self.calls = []

    def GetAsyncKeyState(self, key):
        # VK_CONTROL = 0x11, VK_MENU = 0x12
        if key == 0x11 and self.ctrl_down:
            return 0x8000
        if key == 0x12 and self.alt_down:
            return 0x8000
        return 0

    def IsWindow(self, hwnd):
        self.calls.append(("IsWindow", hwnd))
        return hwnd not in self.invalid_windows

    def IsIconic(self, hwnd):
        self.calls.append(("IsIconic", hwnd))
        return hwnd in self.iconic_windows

    def ShowWindow(self, hwnd, command):
        self.calls.append(("ShowWindow", hwnd, command))
        self.iconic_windows.discard(hwnd)
        return True

    def GetForegroundWindow(self):
        self.calls.append(("GetForegroundWindow",))
        return self.foreground

    def GetWindowThreadProcessId(self, hwnd, process_id):
        self.calls.append(("GetWindowThreadProcessId", hwnd))
        return hwnd + 10

    def AttachThreadInput(self, source_thread, target_thread, attach):
        self.calls.append(("AttachThreadInput", source_thread, target_thread, attach))
        return self.attach_thread_succeeds

    def BringWindowToTop(self, hwnd):
        self.calls.append(("BringWindowToTop", hwnd))
        return True

    def SetForegroundWindow(self, hwnd):
        self.calls.append(("SetForegroundWindow", hwnd))
        if self.set_foreground_succeeds:
            self.foreground = hwnd
        return self.set_foreground_succeeds

    def SetFocus(self, hwnd):
        self.calls.append(("SetFocus", hwnd))
        return hwnd


class _FakeKernel32:
    def GetCurrentThreadId(self):
        return 999


class _FakeWindll:
    def __init__(self, user32):
        self.user32 = user32
        self.kernel32 = _FakeKernel32()


class _FakeCtypes:
    def __init__(self, user32):
        self.windll = _FakeWindll(user32)


class PasteServiceTests(unittest.TestCase):
    def test_ready_to_paste_returns_false_when_ctrl_is_down(self):
        user32 = _FakeUser32()
        user32.ctrl_down = True
        service = PasteService(ctypes_module=_FakeCtypes(user32), paste_func=lambda: None)

        with patch.object(sys, "platform", "win32"):
            self.assertFalse(service.ready_to_paste(200))

    def test_ready_to_paste_returns_false_when_alt_is_down(self):
        user32 = _FakeUser32()
        user32.alt_down = True
        service = PasteService(ctypes_module=_FakeCtypes(user32), paste_func=lambda: None)

        with patch.object(sys, "platform", "win32"):
            self.assertFalse(service.ready_to_paste(200))

    def test_restore_target_focus_invalid_window_returns_false(self):
        user32 = _FakeUser32()
        user32.invalid_windows.add(200)
        service = PasteService(ctypes_module=_FakeCtypes(user32), paste_func=lambda: None)

        with patch.object(sys, "platform", "win32"):
            self.assertFalse(service.restore_target_focus(200))

    def test_restore_target_focus_sets_foreground_and_focus(self):
        user32 = _FakeUser32()
        service = PasteService(ctypes_module=_FakeCtypes(user32), paste_func=lambda: None)

        with patch.object(sys, "platform", "win32"):
            self.assertTrue(service.restore_target_focus(200))

        call_names = [call[0] for call in user32.calls]
        self.assertIn("SetForegroundWindow", call_names)
        self.assertIn("SetFocus", call_names)
        self.assertEqual(user32.foreground, 200)

    def test_restore_target_focus_restores_minimized_window(self):
        user32 = _FakeUser32()
        user32.iconic_windows.add(200)
        service = PasteService(ctypes_module=_FakeCtypes(user32), paste_func=lambda: None)

        with patch.object(sys, "platform", "win32"):
            self.assertTrue(service.restore_target_focus(200))

        self.assertIn(("ShowWindow", 200, 9), user32.calls)

    def test_restore_target_focus_returns_false_when_foreground_does_not_change(self):
        user32 = _FakeUser32()
        user32.set_foreground_succeeds = False
        service = PasteService(ctypes_module=_FakeCtypes(user32), paste_func=lambda: None)

        with patch.object(sys, "platform", "win32"):
            self.assertFalse(service.restore_target_focus(200))

        self.assertIn(("SetForegroundWindow", 200), user32.calls)
        self.assertNotEqual(user32.foreground, 200)

    def test_restore_target_focus_returns_false_on_win32_exception(self):
        user32 = _FakeUser32()

        def boom(hwnd):
            raise OSError("boom")

        user32.SetForegroundWindow = boom
        service = PasteService(ctypes_module=_FakeCtypes(user32), paste_func=lambda: None)

        with patch.object(sys, "platform", "win32"):
            self.assertFalse(service.restore_target_focus(200))

    def test_perform_keyboard_paste_calls_injected_paste_func(self):
        calls = []
        service = PasteService(paste_func=lambda: calls.append("paste"))

        service.perform_keyboard_paste(200)

        self.assertEqual(calls, ["paste"])


if __name__ == "__main__":
    unittest.main()

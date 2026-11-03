import ctypes
import unittest

from core import win32_api


class Win32ApiSignatureTests(unittest.TestCase):
    def test_pointer_returning_functions_are_pointer_sized(self):
        self.assertIs(win32_api.kernel32.GetModuleHandleW.restype, ctypes.wintypes.HMODULE)
        self.assertIs(win32_api.user32.CreateWindowExW.restype, ctypes.wintypes.HWND)
        self.assertEqual(ctypes.sizeof(win32_api.LRESULT), ctypes.sizeof(ctypes.c_void_p))

    def test_message_loop_functions_have_explicit_signatures(self):
        self.assertEqual(win32_api.user32.GetMessageW.restype, ctypes.c_int)
        self.assertIs(win32_api.user32.DispatchMessageW.restype, win32_api.LRESULT)
        self.assertEqual(len(win32_api.user32.PostThreadMessageW.argtypes), 4)

    def test_registration_and_cleanup_functions_have_signatures(self):
        names = (
            "RegisterClassExW",
            "UnregisterClassW",
            "AddClipboardFormatListener",
            "RemoveClipboardFormatListener",
            "RegisterHotKey",
            "UnregisterHotKey",
            "DestroyWindow",
        )
        for name in names:
            with self.subTest(name=name):
                function = getattr(win32_api.user32, name)
                self.assertIsNotNone(function.argtypes)
                self.assertIsNotNone(function.restype)


if __name__ == "__main__":
    unittest.main()

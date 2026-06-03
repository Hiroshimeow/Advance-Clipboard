"""
Win32 Clipboard Monitor & Hotkey Manager
=========================================
Pure Win32 API approach — zero keyboard hooks, zero CPU polling.

Architecture:
- A dedicated hidden HWND (message-only window) that NEVER gets destroyed
- Uses AddClipboardFormatListener for WM_CLIPBOARDUPDATE
- Uses RegisterHotKey for global hotkeys (no low-level keyboard hooks)
- Uses keybd_event for paste simulation (no pynput)
- Runs its own message pump in a daemon thread

Why this is better than pynput:
- pynput uses SetWindowsHookEx(WH_KEYBOARD_LL) which hooks ALL keystrokes
  system-wide → causes keyboard lag in games, Photoshop, Excel, etc.
- RegisterHotKey only triggers on the specific combo, zero overhead otherwise
- AddClipboardFormatListener is the official Windows API for clipboard monitoring
  and works regardless of how content is copied (Ctrl+C, right-click, API calls)

Why a separate hidden window instead of using ClientApp.winId():
- Qt may destroy/recreate the native HWND when hide()/show() is called
- When HWND changes, AddClipboardFormatListener registration is lost
- A message-only window is never visible, never destroyed, always listening
"""

import ctypes
import ctypes.wintypes
import threading
from PyQt6.QtCore import QObject, pyqtSignal

# Win32 constants
WM_CLIPBOARDUPDATE = 0x031D
WM_HOTKEY = 0x0312
WM_DESTROY = 0x0002
WM_QUIT = 0x0012
WM_USER = 0x0400
WM_APP_QUIT = WM_USER + 1  # Custom message to stop the loop

# Virtual key codes
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt key
VK_V = 0x56
VK_ESCAPE = 0x1B

# Modifier flags for RegisterHotKey
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000

# keyboard injection flags
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1


# Hotkey IDs
HOTKEY_TOGGLE = 1  # Ctrl+Alt+V

# Window class style
CS_HREDRAW = 0x0002
CS_VREDRAW = 0x0001

# HWND_MESSAGE for message-only window
HWND_MESSAGE = ctypes.wintypes.HWND(-3)

# Win32 API type definitions - 64-bit safe
import sys

if sys.maxsize > 2**32:
    LRESULT = ctypes.c_int64
    LPARAM = ctypes.c_int64
    WPARAM = ctypes.c_uint64
    ULONG_PTR = ctypes.c_uint64
else:
    LRESULT = ctypes.c_long
    LPARAM = ctypes.c_long
    WPARAM = ctypes.c_uint
    ULONG_PTR = ctypes.c_ulong

WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT,
    ctypes.wintypes.HWND,
    ctypes.wintypes.UINT,
    WPARAM,
    LPARAM,
)

# User32 functions - ensure correct arg types for 64-bit
user32 = ctypes.windll.user32
user32.DefWindowProcW.argtypes = [
    ctypes.wintypes.HWND,
    ctypes.wintypes.UINT,
    WPARAM,
    LPARAM,
]
user32.DefWindowProcW.restype = LRESULT


def _coerce_lparam(value):
    """Normalize callback values into a signed LPARAM-sized integer."""
    bits = ctypes.sizeof(ctypes.c_void_p) * 8
    mask = (1 << bits) - 1
    value &= mask
    if value >= (1 << (bits - 1)):
        value -= 1 << bits
    return value


class WNDCLASSEX(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.UINT),
        ("style", ctypes.wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.wintypes.HINSTANCE),
        ("hIcon", ctypes.wintypes.HANDLE),
        ("hCursor", ctypes.wintypes.HANDLE),
        ("hbrBackground", ctypes.wintypes.HANDLE),
        ("lpszMenuName", ctypes.wintypes.LPCWSTR),
        ("lpszClassName", ctypes.wintypes.LPCWSTR),
        ("hIconSm", ctypes.wintypes.HANDLE),
    ]


class Win32ClipboardMonitor(QObject):
    """
    Monitors clipboard changes and global hotkeys using pure Win32 API.

    Signals:
        clipboard_changed: Emitted when clipboard content changes
        hotkey_toggle: Emitted when Ctrl+Alt+V is pressed
        hotkey_escape: Emitted when Escape is pressed (via RegisterHotKey)
    """

    clipboard_changed = pyqtSignal()
    hotkey_toggle = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._hwnd = None
        self._thread = None
        self._thread_id = None
        self._running = False
        self._wndproc_ref = None  # prevent GC of the callback

    def start(self):
        """Start the monitor thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_message_loop,
            daemon=True,
            name="Win32ClipboardMonitor",
        )
        self._thread.start()

    def stop(self):
        """Stop the monitor thread and cleanup."""
        if not self._running:
            return
        self._running = False
        # Post quit message to the thread's message loop
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_APP_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def _run_message_loop(self):
        """Run Win32 message loop in a dedicated thread."""
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # Create the window procedure callback
        # IMPORTANT: Store reference to prevent garbage collection
        self._wndproc_ref = WNDPROC(self._wndproc)

        # Register window class
        class_name = "AdvClipboardMonitor"
        hinstance = kernel32.GetModuleHandleW(None)

        wc = WNDCLASSEX()
        wc.cbSize = ctypes.sizeof(WNDCLASSEX)
        wc.style = 0
        wc.lpfnWndProc = self._wndproc_ref
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = hinstance
        wc.hIcon = None
        wc.hCursor = None
        wc.hbrBackground = None
        wc.lpszMenuName = None
        wc.lpszClassName = class_name
        wc.hIconSm = None

        atom = user32.RegisterClassExW(ctypes.byref(wc))
        if not atom:
            print(f"[Win32Monitor] RegisterClassExW failed: {kernel32.GetLastError()}")
            return

        # Create message-only window (parent = HWND_MESSAGE)
        self._hwnd = user32.CreateWindowExW(
            0,  # dwExStyle
            class_name,  # lpClassName
            "ClipMonitor",  # lpWindowName
            0,  # dwStyle
            0,
            0,
            0,
            0,  # x, y, w, h
            HWND_MESSAGE,  # hWndParent = message-only
            None,  # hMenu
            hinstance,  # hInstance
            None,  # lpParam
        )

        if not self._hwnd:
            print(f"[Win32Monitor] CreateWindowExW failed: {kernel32.GetLastError()}")
            user32.UnregisterClassW(class_name, hinstance)
            return

        # Register clipboard format listener
        if not user32.AddClipboardFormatListener(self._hwnd):
            print(
                f"[Win32Monitor] AddClipboardFormatListener failed: {kernel32.GetLastError()}"
            )

        # Register hotkey: Ctrl+Alt+V (ID=1)
        if not user32.RegisterHotKey(
            self._hwnd, HOTKEY_TOGGLE, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_V
        ):
            print(
                f"[Win32Monitor] RegisterHotKey Ctrl+Alt+V failed: {kernel32.GetLastError()}"
            )

        print("[Win32Monitor] Started — listening for clipboard & hotkeys")

        # Message loop
        msg = ctypes.wintypes.MSG()
        while self._running:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            if msg.message == WM_APP_QUIT:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        # Cleanup
        if self._hwnd:
            user32.RemoveClipboardFormatListener(self._hwnd)
            user32.UnregisterHotKey(self._hwnd, HOTKEY_TOGGLE)
            user32.DestroyWindow(self._hwnd)
            self._hwnd = None
        user32.UnregisterClassW(class_name, hinstance)

        print("[Win32Monitor] Stopped")

    def _wndproc(self, hwnd, msg, wparam, lparam):
        """Window procedure for the hidden message window."""
        user32 = ctypes.windll.user32

        if msg == WM_CLIPBOARDUPDATE:
            # Emit signal — Qt handles thread-safety for queued connections
            self.clipboard_changed.emit()
            return 0

        elif msg == WM_HOTKEY:
            hotkey_id = wparam
            if hotkey_id == HOTKEY_TOGGLE:
                self.hotkey_toggle.emit()
                return 0

        elif msg == WM_DESTROY:
            return 0

        return user32.DefWindowProcW(hwnd, msg, wparam, _coerce_lparam(lparam))


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.wintypes.DWORD), ("union", INPUT_UNION)]


def _keyboard_input(vk: int, flags: int = 0) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
    return inp


def simulate_paste():
    """
    Simulate Ctrl+V using discrete Win32 keybd_event calls.

    SendInput-in-one-batch is faster, but several target apps appear to miss the
    paste chord after focus restoration. The older discrete event sequence is
    intentionally paced so Windows and the target message queue observe Ctrl as
    down before V is pressed.
    """
    import time

    user32 = ctypes.windll.user32

    # Clear the opener hotkey state first. Ctrl+Alt+V may still be physically
    # transitioning when the user clicks a clip, so release modifiers explicitly.
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.01)

    # Press Ctrl, then V, then release in the natural order. The tiny gap after
    # Ctrl down is what makes the chord reliable in editors/browsers/terminals.
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    time.sleep(0.005)
    user32.keybd_event(VK_V, 0, 0, 0)
    user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.005)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

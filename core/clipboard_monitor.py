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
import logging
import threading
from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

from core.win32_api import (
    LRESULT,
    LPARAM,
    ULONG_PTR,
    WPARAM,
    WNDCLASSEX,
    WNDPROC,
    kernel32,
    user32,
)

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

def _coerce_lparam(value):
    """Normalize callback values into a signed LPARAM-sized integer."""
    bits = ctypes.sizeof(ctypes.c_void_p) * 8
    mask = (1 << bits) - 1
    value &= mask
    if value >= (1 << (bits - 1)):
        value -= 1 << bits
    return value


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

    def __init__(self, *, user32_api=user32, kernel32_api=kernel32):
        super().__init__()
        self._user32 = user32_api
        self._kernel32 = kernel32_api
        self._state_lock = threading.Lock()
        self._state = "stopped"
        self._ready = threading.Event()
        self._hwnd = None
        self._thread = None
        self._thread_id = None
        self._wndproc_ref = None

    @property
    def state(self):
        with self._state_lock:
            return self._state

    def _set_state(self, value):
        with self._state_lock:
            self._state = value

    def start(self):
        """Start the monitor thread and report whether initialization succeeded."""
        with self._state_lock:
            if self._state != "stopped":
                return self._state == "running"
            self._state = "starting"
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run_message_loop,
            daemon=True,
            name="Win32ClipboardMonitor",
        )
        self._thread.start()
        self._ready.wait(timeout=1.0)
        return self.state == "running"

    def stop(self):
        """Stop the monitor thread and cleanup deterministically."""
        with self._state_lock:
            if self._state == "stopped":
                return
            self._state = "stopping"
        if self._thread_id:
            self._user32.PostThreadMessageW(self._thread_id, WM_APP_QUIT, 0, 0)
        thread = self._thread
        if thread:
            thread.join(timeout=3)
        self._thread = None
        if self.state != "stopped":
            self._set_state("stopped")

    def _run_message_loop(self):
        """Run Win32 message loop in a dedicated thread."""
        class_name = "AdvClipboardMonitor"
        class_registered = False
        listener_registered = False
        hotkey_registered = False
        hinstance = None
        try:
            self._thread_id = self._kernel32.GetCurrentThreadId()
            self._wndproc_ref = WNDPROC(self._wndproc)
            hinstance = self._kernel32.GetModuleHandleW(None)

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

            if not self._user32.RegisterClassExW(ctypes.byref(wc)):
                logger.error(
                    "win32_register_class_failed error=%s", self._kernel32.GetLastError()
                )
                return
            class_registered = True

            self._hwnd = self._user32.CreateWindowExW(
                0,
                class_name,
                "ClipMonitor",
                0,
                0,
                0,
                0,
                0,
                HWND_MESSAGE,
                None,
                hinstance,
                None,
            )
            if not self._hwnd:
                logger.error(
                    "win32_create_window_failed error=%s", self._kernel32.GetLastError()
                )
                return

            listener_registered = bool(
                self._user32.AddClipboardFormatListener(self._hwnd)
            )
            if not listener_registered:
                logger.error(
                    "win32_clipboard_listener_failed error=%s",
                    self._kernel32.GetLastError(),
                )

            hotkey_registered = bool(
                self._user32.RegisterHotKey(
                    self._hwnd,
                    HOTKEY_TOGGLE,
                    MOD_CONTROL | MOD_ALT | MOD_NOREPEAT,
                    VK_V,
                )
            )
            if not hotkey_registered:
                logger.error(
                    "win32_register_hotkey_failed error=%s",
                    self._kernel32.GetLastError(),
                )

            self._set_state("running")
            self._ready.set()
            msg = ctypes.wintypes.MSG()
            while self.state == "running":
                ret = self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0 or msg.message == WM_APP_QUIT:
                    break
                self._user32.TranslateMessage(ctypes.byref(msg))
                self._user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._ready.set()
            if hotkey_registered and self._hwnd:
                self._user32.UnregisterHotKey(self._hwnd, HOTKEY_TOGGLE)
            if listener_registered and self._hwnd:
                self._user32.RemoveClipboardFormatListener(self._hwnd)
            if self._hwnd:
                self._user32.DestroyWindow(self._hwnd)
                self._hwnd = None
            if class_registered:
                self._user32.UnregisterClassW(class_name, hinstance)
            self._thread_id = None
            self._set_state("stopped")

    def _wndproc(self, hwnd, msg, wparam, lparam):
        """Window procedure for the hidden message window."""
        try:
            if msg == WM_CLIPBOARDUPDATE:
                self._emit_clipboard_changed()
                return 0
            if msg == WM_HOTKEY and wparam == HOTKEY_TOGGLE:
                self.hotkey_toggle.emit()
                return 0
            if msg == WM_DESTROY:
                return 0
        except BaseException:
            logger.exception("win32_window_callback_failed message=%s", msg)
        return self._user32.DefWindowProcW(hwnd, msg, wparam, _coerce_lparam(lparam))

    def _emit_clipboard_changed(self):
        self.clipboard_changed.emit()


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

    # Clear the opener hotkey state first. Ctrl+Alt+V may still be physically
    # transitioning when the user clicks a clip, so release modifiers explicitly.
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    # time.sleep(0.001)

    # Press Ctrl, then V, then release in the natural order. The tiny gap after
    # Ctrl down is what makes the chord reliable in editors/browsers/terminals.
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    # time.sleep(0.001)
    user32.keybd_event(VK_V, 0, 0, 0)
    user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    # time.sleep(0.001)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

"""
Win32 Clipboard Monitor & Hotkey Manager
=========================================
Pure Win32 API approach — zero keyboard hooks, zero CPU polling.

Architecture:
- A dedicated hidden HWND (message-only window) that NEVER gets destroyed
- Uses AddClipboardFormatListener for WM_CLIPBOARDUPDATE
- Uses RegisterHotKey for global hotkeys (no low-level keyboard hooks)
- Uses paced keybd_event or direct WM_PASTE for paste simulation (no pynput)
- Runs its own message pump in a daemon thread
"""

import sys
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
WM_PASTE = 0x0302

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
        self._wndproc_ref = WNDPROC(self._wndproc)

    @property
    def state(self):
        with self._state_lock:
            return self._state

    def _set_state(self, value):
        with self._state_lock:
            self._state = value

    def start(self):
        """Start the clipboard monitor and register global hotkeys."""
        with self._state_lock:
            if self._state != "stopped":
                return self._state == "running"
            self._state = "starting"
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run_message_loop,
            daemon=True,
            name="Win32ClipboardMonitorThread",
        )
        self._thread.start()
        self._ready.wait(timeout=1.0)
        return self.state == "running"

    def stop(self):
        """Stop the clipboard monitor and unregister hotkeys."""
        with self._state_lock:
            if self._state == "stopped":
                return
            self._state = "stopping"
            thread_id = self._thread_id

        if thread_id:
            self._user32.PostThreadMessageW(thread_id, WM_APP_QUIT, 0, 0)

        thread = self._thread
        if thread and thread.is_alive() and threading.current_thread() != thread:
            thread.join(timeout=2.0)
            if thread.is_alive():
                logger.warning("Win32 clipboard monitor thread did not exit cleanly within 2s")

        with self._state_lock:
            self._set_state("stopped")
            self._thread = None
            self._hwnd = None
            self._thread_id = None

    def _run_message_loop(self):
        """Runs dedicated Win32 message pump for clipboard events and hotkeys."""
        class_name = f"AdvanceClipboardMonitor_{id(self)}"
        class_registered = False
        listener_registered = False
        hotkey_registered = False
        hinstance = None
        try:
            self._thread_id = self._kernel32.GetCurrentThreadId()
            hinstance = self._kernel32.GetModuleHandleW(None)

            wc = WNDCLASSEX()
            wc.cbSize = ctypes.sizeof(WNDCLASSEX)
            wc.style = CS_HREDRAW | CS_VREDRAW
            wc.lpfnWndProc = self._wndproc_ref
            wc.hInstance = hinstance
            wc.lpszClassName = class_name

            atom = self._user32.RegisterClassExW(ctypes.byref(wc))
            if not atom:
                err = self._kernel32.GetLastError()
                logger.error("win32_register_class_failed error=%s", err)
                self._set_state("stopped")
                self._ready.set()
                return
            class_registered = True

            hwnd = self._user32.CreateWindowExW(
                0,
                class_name,
                "AdvanceClipboardMonitorWindow",
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
            if not hwnd:
                err = self._kernel32.GetLastError()
                logger.error("win32_create_window_failed error=%s", err)
                self._set_state("stopped")
                self._ready.set()
                return

            self._hwnd = hwnd

            listener_registered = bool(self._user32.AddClipboardFormatListener(hwnd))
            if not listener_registered:
                err = self._kernel32.GetLastError()
                logger.error("win32_add_clipboard_listener_failed error=%s", err)

            hotkey_registered = bool(
                self._user32.RegisterHotKey(
                    hwnd, HOTKEY_TOGGLE, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_V
                )
            )
            if not hotkey_registered:
                err = self._kernel32.GetLastError()
                logger.error("win32_register_hotkey_failed id=%s error=%s", HOTKEY_TOGGLE, err)

            self._set_state("running")
            self._ready.set()

            # Message pump
            msg = ctypes.wintypes.MSG()
            while self.state == "running":
                ret = self._user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
                if ret <= 0 or msg.message == WM_APP_QUIT:
                    break
                self._user32.TranslateMessage(ctypes.byref(msg))
                self._user32.DispatchMessageW(ctypes.byref(msg))

        except Exception as exc:
            logger.error("win32_monitor_loop_exception: %s", exc, exc_info=True)
            self._set_state("stopped")
        finally:
            self._ready.set()
            if hotkey_registered and self._hwnd:
                self._user32.UnregisterHotKey(self._hwnd, HOTKEY_TOGGLE)
            if listener_registered and self._hwnd:
                self._user32.RemoveClipboardFormatListener(self._hwnd)
            if self._hwnd:
                try:
                    self._user32.DestroyWindow(self._hwnd)
                except Exception:
                    pass
                self._hwnd = None
            if class_registered and hinstance and hasattr(self._user32, "UnregisterClassW"):
                try:
                    self._user32.UnregisterClassW(class_name, hinstance)
                except Exception:
                    pass
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


def get_window_class_name(hwnd) -> str:
    """Safely get Win32 window class name."""
    if not hwnd or sys.platform != "win32":
        return ""
    try:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        return buf.value
    except Exception:
        return ""


VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5


def simulate_paste(target_hwnd=None, focus_hwnd=None):
    """
    Simulate paste into target application with minimal latency.
    
    1. If target focus control is a Win32 'Edit' / 'RichEdit' class (such as Explorer F2 rename
       or address bar), send direct WM_PASTE (0x0302) message.
    2. Otherwise, release any held Alt/Ctrl modifiers (including left/right specific VKs so
       Alt+1..6 never re-triggers the Ctrl+Alt+V global hotkey) with a neutral dummy key (0x07)
       to prevent SC_KEYMENU, then inject Ctrl+V.
    """
    if sys.platform != "win32":
        return

    # 1. Direct Edit control paste optimization
    if focus_hwnd and user32.IsWindow(focus_hwnd):
        cls_name = get_window_class_name(focus_hwnd).lower()
        if cls_name in ("edit", "richedit20w", "richedit50w"):
            logger.debug("simulate_paste_direct_wm_paste hwnd=%s class=%s", focus_hwnd, cls_name)
            user32.SendMessageW(focus_hwnd, WM_PASTE, 0, 0)
            return

    # 2. Paced keybd_event sequence
    alt_down = bool(
        (user32.GetAsyncKeyState(VK_MENU) & 0x8000)
        or (user32.GetAsyncKeyState(VK_LMENU) & 0x8000)
        or (user32.GetAsyncKeyState(VK_RMENU) & 0x8000)
    )
    ctrl_down = bool(
        (user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
        or (user32.GetAsyncKeyState(VK_LCONTROL) & 0x8000)
        or (user32.GetAsyncKeyState(VK_RCONTROL) & 0x8000)
    )

    if alt_down:
        user32.keybd_event(0x07, 0, 0, 0)
        user32.keybd_event(0x07, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_LMENU, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_RMENU, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)

    if ctrl_down:
        user32.keybd_event(VK_LCONTROL, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_RCONTROL, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 0, 0)
    user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

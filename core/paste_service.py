"""
Windows paste/focus side-effect boundary.

The UI layer owns selection, clipboard payload construction, and retry timing.
This service owns platform-specific readiness checks, target focus restoration,
and keyboard paste simulation so the main window does not need to carry Win32
side effects directly.
"""

from __future__ import annotations

import ctypes as _ctypes
import logging
import sys
from collections.abc import Callable
from typing import Any

from core.clipboard_monitor import VK_CONTROL, VK_MENU, simulate_paste

SW_RESTORE = 9


class PasteService:
    """Coordinate Windows focus restoration and keyboard paste injection."""

    def __init__(
        self,
        *,
        ctypes_module: Any = _ctypes,
        paste_func: Callable[[], None] = simulate_paste,
        logger: logging.Logger | None = None,
    ):
        self.ctypes = ctypes_module
        self.paste_func = paste_func
        self.logger = logger or logging.getLogger(__name__)

    def ready_to_paste(self, target_hwnd) -> bool:
        """Return True when modifier keys are released and target focus is restored."""
        if sys.platform != "win32":
            return True

        user32 = self.ctypes.windll.user32
        ctrl_down = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
        alt_down = bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000)
        if ctrl_down or alt_down:
            return False

        if target_hwnd:
            self.restore_target_focus(target_hwnd)
            return user32.GetForegroundWindow() == target_hwnd

        return True

    def restore_target_focus(self, target_hwnd) -> bool:
        """Best-effort foreground/focus restoration for the app active before UI opened."""
        if sys.platform != "win32" or not target_hwnd:
            return True

        user32 = self.ctypes.windll.user32
        try:
            if hasattr(user32, "IsWindow") and not user32.IsWindow(target_hwnd):
                self.logger.warning("restore_target_focus_invalid hwnd=%s", target_hwnd)
                return False

            current_foreground = user32.GetForegroundWindow()
            if current_foreground == target_hwnd:
                return True

            if hasattr(user32, "IsIconic") and user32.IsIconic(target_hwnd):
                user32.ShowWindow(target_hwnd, SW_RESTORE)

            kernel32 = self.ctypes.windll.kernel32
            current_thread = kernel32.GetCurrentThreadId()
            target_thread = user32.GetWindowThreadProcessId(target_hwnd, None)
            foreground_thread = (
                user32.GetWindowThreadProcessId(current_foreground, None)
                if current_foreground
                else 0
            )
            attached = []

            def attach(thread_id):
                if thread_id and thread_id != current_thread:
                    user32.AttachThreadInput(current_thread, thread_id, True)
                    attached.append(thread_id)

            attach(foreground_thread)
            attach(target_thread)
            try:
                if hasattr(user32, "BringWindowToTop"):
                    user32.BringWindowToTop(target_hwnd)
                user32.SetForegroundWindow(target_hwnd)
                if hasattr(user32, "SetFocus"):
                    user32.SetFocus(target_hwnd)
            finally:
                for thread_id in reversed(attached):
                    user32.AttachThreadInput(current_thread, thread_id, False)

            return user32.GetForegroundWindow() == target_hwnd
        except Exception as exc:
            self.logger.warning(
                "restore_target_focus_failed hwnd=%s error=%s", target_hwnd, exc
            )
            return False

    def perform_keyboard_paste(self, target_hwnd=None) -> None:
        """Inject Ctrl+V. Caller must ensure the target window is focused first."""
        self.logger.info("perform_keyboard_paste target_hwnd=%s", target_hwnd)
        self.paste_func()

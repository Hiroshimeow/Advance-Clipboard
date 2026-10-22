"""
Action Runner Module for Advance Clipboard Workspace.
Supports:
- Python inline code execution
- Script file execution (.py via `uv run`, .ps1 via PowerShell)
- External command execution (argv based)
- URL opening (default browser via system handler)
- Non-blocking async execution with background vs foreground distinction
- Output capturing and process status tracking
"""

import os
import sys
import json
import shutil
import logging
import subprocess
import threading
import webbrowser
from typing import Dict, Any, List, Optional, Tuple, Callable

logger = logging.getLogger("action_runner")


def is_valid_url(text: str) -> bool:
    """Check if text is a valid web URL."""
    s = (text or "").strip()
    return s.startswith("http://") or s.startswith("https://")


def open_url(url: str) -> Tuple[bool, str]:
    """Open URL using default system browser."""
    clean_url = (url or "").strip()
    if not is_valid_url(clean_url):
        return False, "Đường dẫn không hợp lệ (phải bắt đầu bằng http:// hoặc https://)"
    try:
        if sys.platform == "win32" and hasattr(os, "startfile"):
            os.startfile(clean_url)
            return True, "Đã mở URL trong trình duyệt."
        # Fallback to webbrowser
        opened = webbrowser.open(clean_url)
        return (True, "Đã mở URL.") if opened else (False, "Không thể mở trình duyệt.")
    except Exception as e:
        logger.error(f"Failed to open URL {clean_url}: {e}")
        return False, str(e)


def resolve_python_runner(script_path: Optional[str] = None) -> List[str]:
    """
    Resolve runner command for Python.
    Prefers `uv run`. If uv is not available, searches for virtualenv near script or uses sys.executable.
    """
    uv_bin = shutil.which("uv")
    if uv_bin:
        return [uv_bin, "run"] + (["python"] if script_path is None else [])

    if script_path:
        script_dir = os.path.dirname(os.path.abspath(script_path))
        # Search for .venv up to 3 levels up
        cur = script_dir
        for _ in range(4):
            candidate = os.path.join(cur, ".venv", "Scripts", "python.exe")
            if os.path.isfile(candidate):
                return [candidate]
            candidate_nix = os.path.join(cur, ".venv", "bin", "python")
            if os.path.isfile(candidate_nix):
                return [candidate_nix]
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent

    return [sys.executable]


class ActionExecutionResult:
    """Represents the execution outcome of a clip action."""

    def __init__(
        self,
        success: bool,
        exit_code: Optional[int] = 0,
        stdout: str = "",
        stderr: str = "",
        message: str = "",
        is_background: bool = False,
    ):
        self.success = success
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.message = message
        self.is_background = is_background

    def __repr__(self) -> str:
        return f"<ActionExecutionResult success={self.success} code={self.exit_code} msg={self.message}>"


class ActionRunner:
    """Manages dispatch and asynchronous execution of clip actions."""

    _active_processes: Dict[int, subprocess.Popen] = {}
    _lock = threading.Lock()

    @classmethod
    def is_running(cls, clip_id: int) -> bool:
        with cls._lock:
            proc = cls._active_processes.get(clip_id)
            if proc is not None:
                if proc.poll() is None:
                    return True
                else:
                    del cls._active_processes[clip_id]
        return False

    @classmethod
    def cancel_action(cls, clip_id: int) -> bool:
        with cls._lock:
            proc = cls._active_processes.pop(clip_id, None)
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                return True
            except Exception:
                try:
                    proc.kill()
                    return True
                except Exception:
                    pass
        return False

    @classmethod
    def validate_action_config(
        cls, action_type: str, target: str, args: Optional[List[str]] = None
    ) -> Tuple[bool, str]:
        """
        Validate action configuration before saving.
        Rule for script_file: must be full path (absolute path) and exists or has valid script extension.
        """
        t = (target or "").strip()
        if not t:
            return False, "Target không được để trống."

        if action_type == "script_file":
            if not os.path.isabs(t):
                return False, "Đường dẫn script phải là đường dẫn tuyệt đối (full path), ví dụ: C:\\scripts\\task.py"
            ext = os.path.splitext(t)[1].lower()
            if ext not in (".py", ".ps1", ".bat", ".cmd", ".sh"):
                return False, f"Định dạng file '{ext}' chưa được hỗ trợ trực tiếp. Hỗ trợ .py, .ps1, .bat, .cmd, .sh."

        elif action_type == "python_inline":
            try:
                compile(t, "<inline_action>", "exec")
            except SyntaxError as e:
                return False, f"Lỗi cú pháp Python: {e}"

        elif action_type == "command":
            pass

        elif action_type == "url":
            if not is_valid_url(t):
                return False, "URL phải bắt đầu bằng http:// hoặc https://"

        else:
            return False, f"Loại action không hỗ trợ: {action_type}"

        return True, ""

    @classmethod
    def execute_action(
        cls,
        clip_id: int,
        action: Dict[str, Any],
        *,
        on_complete: Optional[Callable[[ActionExecutionResult], None]] = None,
        timeout: float = 60.0,
    ) -> bool:
        """
        Execute an action asynchronously in a background thread.
        Returns False if already running or config invalid.
        """
        if cls.is_running(clip_id):
            logger.warning(f"Action for clip {clip_id} is already running.")
            return False

        action_type = action.get("action_type")
        target = action.get("target", "").strip()
        args = action.get("args") or []
        is_background = bool(action.get("is_background", False))

        if action_type == "url":
            ok, msg = open_url(target)
            res = ActionExecutionResult(success=ok, exit_code=0 if ok else 1, message=msg)
            if on_complete:
                on_complete(res)
            return ok

        # Dispatch async worker
        thread = threading.Thread(
            target=cls._worker,
            args=(clip_id, action_type, target, args, is_background, timeout, on_complete),
            daemon=True,
            name=f"ActionWorker-{clip_id}",
        )
        thread.start()
        return True

    @classmethod
    def _worker(
        cls,
        clip_id: int,
        action_type: str,
        target: str,
        args: List[str],
        is_background: bool,
        timeout: float,
        on_complete: Optional[Callable[[ActionExecutionResult], None]],
    ):
        result = ActionExecutionResult(success=False, message="Khởi tạo...")
        try:
            cmd: List[str] = []
            cwd = os.getcwd()

            if action_type == "script_file":
                full_path = os.path.abspath(target)
                script_dir = os.path.dirname(full_path)
                cwd = script_dir if os.path.isdir(script_dir) else os.getcwd()
                ext = os.path.splitext(full_path)[1].lower()

                if ext == ".py":
                    runner = resolve_python_runner(full_path)
                    cmd = runner + [full_path] + list(args)
                elif ext == ".ps1":
                    powershell = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
                    cmd = [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", full_path] + list(args)
                else:
                    cmd = [full_path] + list(args)

            elif action_type == "python_inline":
                runner = resolve_python_runner()
                cmd = runner + ["-c", target] + list(args)

            elif action_type == "command":
                cmd = [target] + list(args)

            else:
                result = ActionExecutionResult(
                    success=False, message=f"Không hỗ trợ loại action '{action_type}'"
                )
                if on_complete:
                    on_complete(result)
                return

            creationflags = 0
            if sys.platform == "win32":
                # CREATE_NO_WINDOW for background commands without terminal popup
                creationflags = subprocess.CREATE_NO_WINDOW if not is_background else 0x00000008  # DETACHED_PROCESS

            if is_background:
                # Detached background process (e.g. Chrome, long running daemon)
                proc = subprocess.Popen(
                    cmd,
                    cwd=cwd,
                    creationflags=creationflags,
                    close_fds=True,
                )
                result = ActionExecutionResult(
                    success=True,
                    exit_code=0,
                    message=f"Đã kích hoạt tiến trình nền (PID {proc.pid}).",
                    is_background=True,
                )
            else:
                proc = subprocess.Popen(
                    cmd,
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creationflags,
                )
                with cls._lock:
                    cls._active_processes[clip_id] = proc

                stdout, stderr = proc.communicate(timeout=timeout)
                exit_code = proc.returncode
                success = (exit_code == 0)
                msg = "Hoàn thành thành công." if success else f"Thất bại (mã thoát {exit_code})."

                # Truncate output preview to 64KB
                max_preview = 65536
                if len(stdout) > max_preview:
                    stdout = stdout[:max_preview] + "\n...[cắt bớt do quá dài]"
                if len(stderr) > max_preview:
                    stderr = stderr[:max_preview] + "\n...[cắt bớt do quá dài]"

                result = ActionExecutionResult(
                    success=success,
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    message=msg,
                    is_background=False,
                )

        except subprocess.TimeoutExpired:
            cls.cancel_action(clip_id)
            result = ActionExecutionResult(
                success=False,
                exit_code=-1,
                message=f"Quá thời gian thực thi ({timeout}s). Đã dừng tiến trình.",
            )
        except Exception as e:
            logger.error(f"Error running action for clip {clip_id}: {e}", exc_info=True)
            result = ActionExecutionResult(
                success=False,
                exit_code=-2,
                message=f"Lỗi thực thi: {str(e)}",
            )
        finally:
            with cls._lock:
                cls._active_processes.pop(clip_id, None)
            if on_complete:
                try:
                    on_complete(result)
                except Exception as cb_err:
                    logger.error(f"Callback error after action execution: {cb_err}")

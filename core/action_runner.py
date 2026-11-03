"""
Action Runner Module for Advance Clipboard Workspace.
Supports:
- Python inline code execution
- Script file execution (.py via `uv run`, .ps1 via PowerShell)
- External command execution (argv based)
- URL opening (default browser via system handler)
- Live terminal log streaming (stdout + stderr) for running actions
- Stopping running actions (process tree termination via taskkill /T /F on Windows)
- Non-blocking async execution with background vs foreground distinction
"""

import os
import sys
import time
import shutil
import signal
import logging
import subprocess
import threading
import webbrowser
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple, Callable

logger = logging.getLogger("action_runner")

MAX_LOG_CHARS = 131072  # 128 KB rolling terminal log per clip


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
        return [uv_bin, "run"]

    if script_path:
        script_dir = os.path.dirname(os.path.abspath(script_path))
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
    """Manages dispatch, live terminal logs, and lifecycle of clip actions."""

    _active_processes: Dict[int, subprocess.Popen] = {}
    _started_at: Dict[int, float] = {}
    _action_logs: Dict[int, str] = {}
    _action_status: Dict[int, Dict[str, Any]] = {}
    _stopped_by_user: set[int] = set()
    _state_callbacks: List[Callable[[int, str], None]] = []
    _lock = threading.Lock()

    @classmethod
    def add_state_listener(cls, callback: Callable[[int, str], None]) -> None:
        with cls._lock:
            if callback not in cls._state_callbacks:
                cls._state_callbacks.append(callback)

    @classmethod
    def remove_state_listener(cls, callback: Callable[[int, str], None]) -> None:
        with cls._lock:
            if callback in cls._state_callbacks:
                cls._state_callbacks.remove(callback)

    @classmethod
    def _notify_state(cls, clip_id: int, state: str) -> None:
        with cls._lock:
            callbacks = list(cls._state_callbacks)
        for cb in callbacks:
            try:
                cb(clip_id, state)
            except Exception:
                pass

    @classmethod
    def _append_log(cls, clip_id: int, text: str) -> None:
        if not text:
            return
        with cls._lock:
            current = cls._action_logs.get(clip_id, "")
            combined = current + text
            if len(combined) > MAX_LOG_CHARS:
                combined = "...[log cũ đã cắt bớt]...\n" + combined[-MAX_LOG_CHARS:]
            cls._action_logs[clip_id] = combined

    @classmethod
    def get_action_log(cls, clip_id: int) -> str:
        with cls._lock:
            return cls._action_logs.get(clip_id, "")

    @classmethod
    def get_action_status(cls, clip_id: int) -> Dict[str, Any]:
        running = cls.is_running(clip_id)
        with cls._lock:
            info = dict(cls._action_status.get(clip_id) or {})
        info["is_running"] = running
        return info

    @classmethod
    def is_running(cls, clip_id: int) -> bool:
        with cls._lock:
            proc = cls._active_processes.get(clip_id)
            if proc is not None:
                if proc.poll() is None:
                    return True
                else:
                    cls._active_processes.pop(clip_id, None)
                    cls._started_at.pop(clip_id, None)
        return False

    @classmethod
    def get_running_clip_ids(cls) -> List[int]:
        """Return list of clip_ids that currently have a live running action, ordered by start time."""
        alive: List[Tuple[float, int]] = []
        with cls._lock:
            dead = []
            for cid, proc in list(cls._active_processes.items()):
                if proc.poll() is None:
                    alive.append((cls._started_at.get(cid, 0.0), cid))
                else:
                    dead.append(cid)
            for cid in dead:
                cls._active_processes.pop(cid, None)
                cls._started_at.pop(cid, None)
        alive.sort(key=lambda item: item[0])
        return [cid for _, cid in alive]

    @classmethod
    def stop_action(cls, clip_id: int) -> bool:
        """Stop a running action and kill its entire child process tree."""
        with cls._lock:
            proc = cls._active_processes.pop(clip_id, None)
            cls._started_at.pop(clip_id, None)
            if proc is not None:
                cls._stopped_by_user.add(clip_id)
                if clip_id in cls._action_status:
                    cls._action_status[clip_id]["state"] = "stopped"

        if proc is None:
            return False

        killed = False
        pid = getattr(proc, "pid", None)
        try:
            if proc.poll() is None:
                if sys.platform == "win32" and pid:
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(pid)],
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                            capture_output=True,
                            timeout=3.0,
                        )
                        killed = True
                    except Exception:
                        pass
                else:
                    try:
                        if pid and hasattr(os, "killpg"):
                            os.killpg(os.getpgid(pid), signal.SIGTERM)
                            killed = True
                    except Exception:
                        pass

                try:
                    proc.terminate()
                    killed = True
                except Exception:
                    pass
                try:
                    proc.kill()
                    killed = True
                except Exception:
                    pass
        finally:
            cls._append_log(
                clip_id,
                f"\n[⏹ {datetime.now().strftime('%H:%M:%S')}] Đã dừng tiến trình bởi người dùng (STOP Action).\n",
            )
            cls._notify_state(clip_id, "stopped")
        return killed

    cancel_action = stop_action

    @classmethod
    def validate_action_config(
        cls, action_type: str, target: str, args: Optional[List[str]] = None
    ) -> Tuple[bool, str]:
        """
        Validate action configuration before saving.
        Rule for script_file: must be full path (absolute path) and has valid script extension.
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
        timeout: Optional[float] = None,
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

        with cls._lock:
            cls._stopped_by_user.discard(clip_id)
            cls._action_logs[clip_id] = ""
            cls._action_status[clip_id] = {
                "state": "starting",
                "action_type": action_type,
                "target": target,
                "args": list(args),
                "pid": None,
                "exit_code": None,
                "started_at": datetime.now().strftime("%H:%M:%S"),
            }

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
        timeout: Optional[float],
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
                if len(runner) >= 2 and runner[1] == "run":
                    cmd = runner + ["python", "-u", "-c", target] + list(args)
                else:
                    cmd = runner + ["-u", "-c", target] + list(args)

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
            popen_kwargs: Dict[str, Any] = {}
            if sys.platform == "win32":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            else:
                popen_kwargs["start_new_session"] = True

            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"

            header = (
                f"[{datetime.now().strftime('%H:%M:%S')}] Bắt đầu chạy Action ({action_type})\n"
                f"$ {' '.join(cmd)}\n"
                f"CWD: {cwd}\n"
                f"{'-' * 56}\n"
            )
            cls._append_log(clip_id, header)

            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=creationflags,
                **popen_kwargs,
            )

            with cls._lock:
                cls._active_processes[clip_id] = proc
                cls._started_at[clip_id] = time.monotonic()
                if clip_id in cls._action_status:
                    cls._action_status[clip_id]["state"] = "running"
                    cls._action_status[clip_id]["pid"] = proc.pid

            cls._notify_state(clip_id, "running")

            stdout_chunks: List[str] = []
            stderr_chunks: List[str] = []

            def _read_stream(stream, chunks: List[str], prefix: str = ""):
                try:
                    for line in iter(stream.readline, ""):
                        if not line:
                            break
                        chunks.append(line)
                        cls._append_log(clip_id, f"{prefix}{line}")
                except Exception:
                    pass
                finally:
                    try:
                        stream.close()
                    except Exception:
                        pass

            t_out = threading.Thread(
                target=_read_stream, args=(proc.stdout, stdout_chunks, ""), daemon=True
            )
            t_err = threading.Thread(
                target=_read_stream, args=(proc.stderr, stderr_chunks, "[stderr] "), daemon=True
            )
            t_out.start()
            t_err.start()

            effective_timeout = timeout
            proc.wait(timeout=effective_timeout)
            t_out.join(timeout=1.0)
            t_err.join(timeout=1.0)

            exit_code = proc.returncode
            with cls._lock:
                was_stopped = clip_id in cls._stopped_by_user

            stdout = "".join(stdout_chunks)
            stderr = "".join(stderr_chunks)
            max_preview = 65536
            if len(stdout) > max_preview:
                stdout = stdout[:max_preview] + "\n...[cắt bớt do quá dài]"
            if len(stderr) > max_preview:
                stderr = stderr[:max_preview] + "\n...[cắt bớt do quá dài]"

            if was_stopped:
                msg = "Đã dừng bởi người dùng."
                success = False
            else:
                success = exit_code == 0
                msg = "Hoàn thành thành công." if success else f"Thất bại (mã thoát {exit_code})."
                cls._append_log(
                    clip_id,
                    f"\n{'-' * 56}\n[{datetime.now().strftime('%H:%M:%S')}] {msg}\n",
                )

            with cls._lock:
                if clip_id in cls._action_status:
                    cls._action_status[clip_id]["state"] = "stopped" if was_stopped else ("completed" if success else "failed")
                    cls._action_status[clip_id]["exit_code"] = exit_code

            result = ActionExecutionResult(
                success=success,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                message=msg,
                is_background=is_background,
            )

        except subprocess.TimeoutExpired:
            cls.stop_action(clip_id)
            cls._append_log(
                clip_id,
                f"\n[⏱ Quá thời gian thực thi ({timeout}s). Đã dừng tiến trình.]\n",
            )
            result = ActionExecutionResult(
                success=False,
                exit_code=-1,
                message=f"Quá thời gian thực thi ({timeout}s). Đã dừng tiến trình.",
            )
        except Exception as e:
            logger.error(f"Error running action for clip {clip_id}: {e}", exc_info=True)
            cls._append_log(clip_id, f"\n[Lỗi thực thi]: {str(e)}\n")
            with cls._lock:
                if clip_id in cls._action_status:
                    cls._action_status[clip_id]["state"] = "failed"
            result = ActionExecutionResult(
                success=False,
                exit_code=-2,
                message=f"Lỗi thực thi: {str(e)}",
            )
        finally:
            with cls._lock:
                cls._active_processes.pop(clip_id, None)
                cls._started_at.pop(clip_id, None)
            cls._notify_state(clip_id, "finished")
            if on_complete:
                try:
                    on_complete(result)
                except Exception as cb_err:
                    logger.error(f"Callback error after action execution: {cb_err}")

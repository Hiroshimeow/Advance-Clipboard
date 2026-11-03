# Windows Clipboard Soak Verification

Use this runbook on a Windows 10/11 machine after the automated test suite and search benchmark have been run from a clean application process.

## 1. Automated regression

```powershell
$env:QT_QPA_PLATFORM='offscreen'
uv run python -m unittest discover -s tests -p 'test_*.py'
uv run python tests/benchmark_search.py
```

The unit/UI suite must pass. The search benchmark prints JSON for a temporary 25,000-row database and fails when a common one- or two-term query exceeds 25 ms p95. A benchmark failure requires a separate FTS5 design; do not silently expand this release into a search migration.

## 2. Monitor start/stop stress

Close any running Advance Clipboard instance first so the global hotkey is not owned elsewhere. Then run:

```powershell
uv run python -c "import ctypes, ctypes.wintypes as w; from core.clipboard_monitor import Win32ClipboardMonitor; k=ctypes.windll.kernel32; k.GetCurrentProcess.argtypes=[]; k.GetCurrentProcess.restype=w.HANDLE; k.GetProcessHandleCount.argtypes=[w.HANDLE, ctypes.POINTER(w.DWORD)]; k.GetProcessHandleCount.restype=w.BOOL; p=k.GetCurrentProcess(); c=w.DWORD(); assert k.GetProcessHandleCount(p, ctypes.byref(c)); before=c.value; m=Win32ClipboardMonitor(); [(m.start(), m.stop()) for _ in range(500)]; assert k.GetProcessHandleCount(p, ctypes.byref(c)); after=c.value; print({'before_handles':before,'after_handles':after,'growth':after-before,'state':m.state,'thread_alive':bool(m._thread and m._thread.is_alive())}); assert m.state=='stopped'; assert not (m._thread and m._thread.is_alive()); assert after-before<20"
```

Acceptance:

- all 500 `start()`/`stop()` cycles complete;
- no `Windows fatal exception`, access violation, or heap-corruption report appears;
- monitor state is `stopped` and no monitor thread remains alive;
- process handle growth is below 20.

## 3. Thirty-minute mixed clipboard soak

This part is interactive because it validates actual global clipboard ownership, foreground activation latency, and a real sleep/resume transition.

1. Start one primary Advance Clipboard instance. Launch it a second time and confirm the existing window activates instead of creating another monitor/hotkey owner.
2. Copy 400 distinct short text values.
3. Copy 20 text values near 2 MiB UTF-8 and two values over 2 MiB. Confirm the oversized values remain usable on the system clipboard but are rejected from history with `text_too_large` logging.
4. Copy 40 screenshots, including ten 4K images. Open the popup with `Ctrl+Alt+V` after every tenth capture and record activation latency.
5. Put the machine to sleep for two minutes, resume, and copy 40 additional values.
6. Exit normally and confirm the final backup completes.

Acceptance:

- no new `Windows fatal exception`, `0xc0000005`, or `0xc0000374` entry;
- accepted text/image counts match the workload and rejected payloads are logged by stable reason;
- popup activation latency is below 150 ms at p95;
- a second launch never owns a second global hotkey or clipboard listener.

## 4. Backup and process bounds

After the soak:

```powershell
Get-ChildItem backups -Filter 'clipboard_backup_*.json' | Measure-Object
Get-ChildItem backups -Filter 'clipboard_backup_*.json.tmp'
Get-Process python | Select-Object Id,CPU,WorkingSet64,PrivateMemorySize64,Handles,Threads
```

Acceptance:

- at most ten valid JSON backups;
- no matching backup temp file older than 24 hours;
- no overlapping backup worker processes;
- idle CPU returns near zero;
- working set and handle count return close to the pre-soak baseline.

Do not manually delete backup `.tmp` files while checking this. Startup cleanup owns deletion and is restricted to matching temp backups older than 24 hours.

## 5. Crash correlation

If the app or machine fails, record the exact timestamp and compare it with Windows Application Error/WER and System Event 41 records. An app-only native crash blocks this release. A kernel restart remains a machine/driver investigation unless a dump stack demonstrates an app-triggered driver path; this application work must not be reported as fixing machine restarts.

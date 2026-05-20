# ADR 0001: Isolate Windows Paste and Focus Side Effects

## Status

Accepted

## Context

The main PyQt window previously owned several responsibilities at once:

- User selection and paste intent.
- Clipboard payload preparation.
- Retry timing.
- Windows foreground-window restoration.
- Keyboard paste simulation.

This made `main.py` harder to reason about and made platform-specific behavior harder to test independently.

## Decision

Move Windows focus restoration and keyboard paste injection behind `core.paste_service.PasteService`.

The UI layer keeps responsibility for:

- Determining which clip should be pasted.
- Writing text/image payloads to the clipboard.
- Managing paste attempt state and retry timing.
- Clearing UI search state after paste.

`PasteService` owns:

- Modifier-key readiness checks.
- Restoring focus to the previously active target window.
- Invoking keyboard paste simulation.

## Consequences

Positive:

- `main.py` carries less Win32 side-effect code.
- The paste workflow has a clearer platform boundary.
- Focus/paste behavior can be unit-tested through a smaller service.
- Future Windows-specific fixes can be localized.

Trade-offs:

- `ClientApp` still keeps thin wrapper methods for compatibility with existing tests and call sites.
- Clipboard payload construction remains in the UI layer because it depends on Qt clipboard/image APIs and app-level image storage conventions.

## Validation

Validated syntax with:

```powershell
$env:PYTHONHOME=$null
$env:PYTHONPATH=$null
.\.venv\Scripts\python.exe -m compileall main.py core\paste_service.py core\clipboard_monitor.py
```

Validated the existing focus/paste test with:

```powershell
$env:PYTHONHOME=$null
$env:PYTHONPATH=$null
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m unittest tests.test_keyboard_navigation.KeyboardNavigationTests.test_ready_to_paste_restores_last_active_window_before_ctrl_v
```

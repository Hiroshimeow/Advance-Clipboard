# Advance Clipboard Case Study

## Summary

Advance Clipboard is a local-first Windows clipboard manager built to make high-volume developer clipboard workflows searchable, recoverable, and easier to rediscover. It combines Win32 clipboard monitoring, PyQt6 UI, SQLite WAL storage, local backup/recovery, hybrid lexical-semantic search, and an optional Neural Memory Map visualization.

## Problem

Developers often copy commands, logs, snippets, screenshots, file paths, and error messages during daily work. Standard clipboard history is chronological and transient, which makes it hard to recover useful context later or find semantically related items.

Advance Clipboard treats clipboard history as a local productivity database rather than a throwaway stack.

## Constraints

- The app must work offline and keep clipboard data local.
- Clipboard monitoring must not poll or add global keyboard-hook overhead.
- The UI must stay responsive while search/indexing work runs.
- Paste must restore focus to the original target window reliably on Windows.
- The database must survive corruption or accidental local failures.
- AI features must be optional and bounded so they do not index unbounded history.

## Architecture

```text
Win32 clipboard listener / hotkey
        │
        ▼
PyQt6 application shell
        │
        ├── SQLite storage with WAL
        ├── JSON backup and recovery
        ├── Hybrid lexical + semantic search
        ├── Paste/focus service boundary
        └── Neural Memory Map sidecar
```

## Key Engineering Decisions

### Win32 message-only clipboard monitor

The app uses a dedicated hidden message-only window with `AddClipboardFormatListener` and `RegisterHotKey`. This avoids global keyboard hooks for normal operation and keeps clipboard/hotkey monitoring event-driven.

### Local-first SQLite storage

SQLite WAL mode provides a simple embedded data store with low operational overhead. Clipboard records, metadata, tags, groups, neural vectors, and neural links remain local.

### Backup and recovery path

The app maintains JSON backups and can restore data when the SQLite database is empty or invalid. This turns clipboard history from ephemeral runtime state into recoverable user data.

### Background semantic indexing

The neural engine runs in a daemon thread and indexes a bounded window of pinned clips plus recent history. Search falls back to lexical SQL behavior if semantic indexes are unavailable.

### Paste service boundary

Windows focus restoration and keyboard paste simulation are isolated behind `core.paste_service.PasteService`. The UI owns selection and clipboard payload preparation; the service owns platform side effects. This reduces `main.py` coupling and makes the paste flow easier to test and reason about.

## Current Refactor Direction

The next engineering goal is not adding large features. The focus is to polish the repo into a production-minded case study:

1. Split platform side effects out of `main.py`.
2. Stabilize test commands and document environment requirements.
3. Add architecture notes and decision records.
4. Add search diagnostics and settings only after the core app remains stable.

## Testing Notes

The current Windows environment had `PYTHONHOME` pointing at a Python 3.14 runtime, which can contaminate other Python interpreters and trigger standard-library mismatch errors. For reliable local validation, clear `PYTHONHOME` and `PYTHONPATH`, then use the project `.venv` Python.

Example:

```powershell
$env:PYTHONHOME=$null
$env:PYTHONPATH=$null
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m compileall main.py core\paste_service.py core\clipboard_monitor.py
.\.venv\Scripts\python.exe -m unittest tests.test_keyboard_navigation.KeyboardNavigationTests.test_ready_to_paste_restores_last_active_window_before_ctrl_v
```


## Before / After Refactor

### Before

- Win32 focus restoration lived directly in `main.py`.
- Paste flow mixed UI state, clipboard payload writes, retry timing, focus restoration, and keyboard injection.
- Platform-specific side effects were harder to test independently.

### After

- `core/paste_service.py` owns Windows focus restoration and keyboard paste injection.
- `main.py` delegates through thin compatibility wrappers.
- Existing paste/focus regression test still passes.
- `PasteService` accepts an injected paste function, which allows direct unit tests without patching the global clipboard monitor module.

### Evidence

- Commit: `d17e837 refactor: isolate paste focus service`
- Added: `core/paste_service.py`
- Added: `tests/test_paste_service.py`
- Validation:
  - `compileall main.py core\paste_service.py core\clipboard_monitor.py`
  - `unittest tests.test_paste_service`
  - `unittest tests.test_keyboard_navigation.KeyboardNavigationTests.test_ready_to_paste_restores_last_active_window_before_ctrl_v`

## Deliberate Non-Goals

The following features were intentionally deferred:

- Global typing suggestions while the user types in any app.
- Low-level keyboard hooks for normal operation.
- Go/Rust native helper process.
- Cloud sync.
- Unbounded AI indexing.
- Heavy installer work before stabilizing testability and reliability.

These were deferred because the current portfolio goal is to demonstrate reliability, local-first architecture, Windows integration, and maintainable refactoring rather than feature breadth.

## Current Risks

- `main.py` still owns too many responsibilities.
- Some GUI tests still depend on broad `ClientApp` startup behavior.
- Runtime data paths are still dev-oriented.
- Packaging is currently launcher-based, not installer-based.

## Portfolio Narrative

This project demonstrates:

- Windows desktop integration with Win32 APIs.
- Local-first data handling and privacy-conscious design.
- SQLite-backed reliability with backup/recovery.
- PyQt6 UI engineering with keyboard navigation and paste workflow tests.
- Background AI indexing without blocking the UI.
- A practical approach to refactoring a prototype into a maintainable product case study.

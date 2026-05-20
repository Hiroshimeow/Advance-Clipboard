# Testing Guide

## Environment setup

Clear inherited Python env variables before running tests:

```powershell
$env:PYTHONHOME=$null
$env:PYTHONPATH=$null
$env:QT_QPA_PLATFORM='offscreen'
```

## Test tiers

### Fast automated validation

Use this before each code commit:

```powershell
.\.venv\Scripts\python.exe -m compileall main.py core\paste_service.py core\clipboard_monitor.py
.\.venv\Scripts\python.exe -m unittest tests.test_paste_service
.\.venv\Scripts\python.exe -m unittest tests.test_keyboard_navigation.KeyboardNavigationTests.test_ready_to_paste_restores_last_active_window_before_ctrl_v
```

### Full discovery

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Full discovery currently includes GUI/neural/manual-style coverage and may exceed fast-validation time in headless environments. If this exceeds the local timeout, run test files individually and classify failures as unit, GUI/offscreen, neural/integration, or manual smoke coverage.

### Known slow/manual areas

- `tests/test_neural_show.py` opens the neural map UI and calls the Qt event loop at import time; it is a manual smoke/demo script, not a safe default unittest module.
- Neural map display tests may require GUI/WebEngine behavior.
- Screenshot/demo tests are closer to manual smoke validation than fast unit coverage.

## Manual Windows smoke test matrix

| Scenario                            | Expected                                       |
| ----------------------------------- | ---------------------------------------------- |
| Paste text into Notepad             | Target receives selected clip                  |
| Paste text into browser textarea    | Target receives selected clip                  |
| Paste into VS Code editor           | Target receives selected clip                  |
| Paste into terminal                 | Target receives selected clip                  |
| Target window minimized             | App restores target before paste               |
| Target window closed after UI opens | Paste fails safely, app does not crash         |
| Ctrl/Alt still held after hotkey    | Paste waits until modifiers release            |
| Image paste                         | Image copied/pasted when target supports image |
| High-DPI display                    | Popup remains visible and usable               |
| Multi-monitor                       | Popup appears on correct screen                |
| App hidden mode via `.vbs`          | Runs without visible console                   |
| Debug mode via `.bat`               | Console visible for troubleshooting            |

## Secret scanning

Run secret scans against source files only. Exclude virtual environments and dependency caches:

```powershell
git ls-files | Select-String -NotMatch "^\.venv/|^dist/|^build/"
```

If using an external scanner, configure excludes for:

- `.venv/`
- `dist/`
- `build/`
- `__pycache__/`
- dependency caches

## Known limitations

- Some focus behavior is Windows/app dependent.
- Elevated/admin target apps may behave differently.
- Full GUI tests may be slower than direct unit tests.

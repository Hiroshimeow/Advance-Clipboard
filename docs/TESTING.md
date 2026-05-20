# Testing Guide

## Environment setup

Clear inherited Python env variables before running tests:

```powershell
$env:PYTHONHOME=$null
$env:PYTHONPATH=$null
$env:QT_QPA_PLATFORM='offscreen'
```

## Fast validation

```powershell
.\.venv\Scripts\python.exe -m compileall main.py core\paste_service.py core\clipboard_monitor.py
.\.venv\Scripts\python.exe -m unittest tests.test_paste_service
.\.venv\Scripts\python.exe -m unittest tests.test_keyboard_navigation.KeyboardNavigationTests.test_ready_to_paste_restores_last_active_window_before_ctrl_v
```

## Full test suite

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

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

## Known limitations

- Some focus behavior is Windows/app dependent.
- Elevated/admin target apps may behave differently.
- Full GUI tests may be slower than direct unit tests.

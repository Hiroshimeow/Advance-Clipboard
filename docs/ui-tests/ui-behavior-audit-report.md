# UI Behavior Audit Report

**Date:** 2026-06-11  
**Auditor:** Hermes Kanban Worker (t_50e33cf0)  
**Repository:** Advance-Clipboard  
**Python:** 3.11.15, PyQt6  

---

## Executive Summary

All five audited areas passed verification:

| Area | Status | Notes |
|------|--------|-------|
| Clip row layout structure | PASS | `[content][badge][actions]` enforced correctly |
| Smooth scrolling | PASS (with fix) | `int()` cast added for PyQt6 float type safety |
| Hover highlighting | PASS | Style sheet applied/removed correctly, previous hover cleared |
| Scroll-to-center on keyboard nav | PASS | `scrollToSelected()` centers viewport on selected item |
| Async promote-to-top | PASS | Uses `QTimer.singleShot(0, ...)` to avoid UI blocking |

**Bug Found:** 1 type error in `wheelEvent` — `bar.setValue()` received a float instead of int. Fixed.

---

## Detailed Findings

### 1. Clip Row Layout Structure

**Files inspected:** `ui/widgets.py`, `tests/test_widgets_layout.py`  
**Tests run:** `pytest tests/test_widgets_layout.py -v` — **5/5 passed** (0.26s)

Verified:
- Content area takes dominant width
- Badge column is compact (~36px) and does not reduce text width
- Action buttons stay flush-right in a dedicated column
- Single-line rows are tall enough for button columns (min height enforced)
- Multi-line text keeps last line visible
- Legacy clip data (missing fields) renders without errors

### 2. Smooth Scrolling

**File:** `ui/widgets.py` — `SmoothListWidget.wheelEvent()`  
**Verification:** Simulated wheel events with offscreen QApplication

**Finding:** `event.angleDelta().y()` returns a `float` in PyQt6 (e.g., `-120.0`). The expression `abs(delta) // 3` produces a float (`40.0`), but `QScrollBar.setValue()` expects an `int`. This causes `TypeError: setValue(self, a0: int): argument 1 has unexpected type 'float'`.

**Fix Applied:**
```diff
- step = max(1, abs(delta) // 3)
+ step = int(max(1, abs(delta) // 3))
```

**Committed:** `1dbd678` — "fix(ui): cast scroll step to int for PyQt6 type safety"

**Verified behavior:**
- Scroll mode: `ScrollPerPixel` (per-pixel scrolling enabled)
- Wheel down from position 100 → 140 (step=40, which is 120/3)
- Wheel up from position 140 → 100 (step=40, symmetric)

### 3. Hover Highlighting

**File:** `ui/widgets.py` — `SmoothListWidget.mouseMoveEvent()`, `leaveEvent()`, `_clear_hover()`  
**Verification:** Simulated mouse move and leave events with proper `QMouseEvent` objects

Verified:
- Hover on row 2 applies style sheet (border/background) — confirmed 53-char style string
- Moving hover to row 0 clears the previous hover on row 2
- `leaveEvent()` resets `_hovered_row` to -1 and `_hovered_item` to None
- `mouseTracking=True` is set in constructor for real-time tracking

### 4. Scroll-to-Center During Keyboard Navigation

**File:** `ui/clipboard_browser_controller.py` — `nav_up()`, `nav_down()`  
**Verification:** Inspected source code + runtime test on `scrollToSelected()`

Verified:
- Both `nav_up` and `nav_down` call `w.scrollToSelected()` after changing selection
- Runtime test: selecting row 15 in a 30-item list moved scroll from 0 to 986 (centered)
- No additional centering logic needed — Qt's built-in `scrollToSelected()` handles this

### 5. Async Promote-to-Top Logic

**File:** `main.py` — `_promote_history_clip_async()`, `_promote_history_clip()`  
**Verification:** Source code inspection (lines 1026-1038)

Verified:
```python
def _promote_history_clip_async(self, clip_id):
    if clip_id is None:
        return
    QTimer.singleShot(0, lambda: self._promote_history_clip(clip_id))
```

- Uses `QTimer.singleShot(0, ...)` to defer execution to the next event loop iteration
- This prevents UI blocking during paste operations on the main thread
- Called from two places (lines 1024 and 1379) — both use the async wrapper
- `_promote_history_clip` checks `self.isVisible()` before updating the browser

### 6. Runtime Stability (Hover + Deletion)

**File:** `ui/clipboard_browser_controller.py` — `refresh_lists()`  
**Verification:** Source code inspection + runtime test

Verified:
- `refresh_lists()` calls `_clear_hover()` before clearing and rebuilding the list
- This prevents crashes when a hovered item is deleted during list refresh
- Runtime test confirmed: hover on row 3 → `_clear_hover()` → `clear()` → no crash, state clean

---

## Module Compile Integrity

All three critical modules compile without errors:
- `ui/widgets.py` — OK
- `ui/clipboard_browser_controller.py` — OK
- `main.py` — OK (imports succeed when Win32-specific code is available)

---

## Fix Summary

| Commit | File | Change | Reason |
|--------|------|--------|--------|
| `1dbd678` | `ui/widgets.py` | `step = int(max(1, abs(delta) // 3))` | PyQt6 returns float from `angleDelta().y()`, causing `TypeError` on `bar.setValue()` |

---

## Recommendations for Future Work

1. **Expand test coverage:** Add behavioral tests for hover, scrolling, and navigation to the pytest suite
2. **Visual regression testing:** Consider screenshot-based testing for clip row rendering
3. **Performance testing:** Test with 1000+ clipboard history items to verify scroll performance
4. **Cross-platform compatibility:** The `WINFUNCTYPE` import in `core/clipboard_monitor.py` needs a proper guard for non-Windows environments

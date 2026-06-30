import os
import sys
import time
import unittest
import ctypes
import types
from unittest.mock import MagicMock, patch

# Add project root to path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from PyQt6.QtCore import QEvent
from PyQt6.QtGui import QKeyEvent

# Ensure headless Qt (caller also sets this env var)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


if not hasattr(ctypes, "WINFUNCTYPE"):
    ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE

_clipboard_monitor_mod = types.ModuleType("core.clipboard_monitor")
_clipboard_monitor_mod.Win32ClipboardMonitor = MagicMock()
_clipboard_monitor_mod.VK_CONTROL = 0x11
_clipboard_monitor_mod.VK_MENU = 0x12
_clipboard_monitor_mod.simulate_paste = MagicMock()
sys.modules["core.clipboard_monitor"] = _clipboard_monitor_mod

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QListWidgetItem

try:
    from PyQt6.QtTest import QSignalSpy
except Exception:  # pragma: no cover
    QSignalSpy = None


from main import ClientApp
from ui.clipboard_browser_controller import SEARCH_DEBOUNCE_MS
from ui.widgets import SearchLineEdit


_APP: QApplication | None = None


def _get_qapp() -> QApplication:
    global _APP
    if _APP is not None:
        return _APP

    inst = QApplication.instance()
    if isinstance(inst, QApplication):
        _APP = inst
        return _APP

    _APP = QApplication([])
    _APP.setQuitOnLastWindowClosed(False)
    return _APP


def _send_key(widget, key: Qt.Key, *, text: str = ""):
    press = QKeyEvent(
        QEvent.Type.KeyPress, int(key), Qt.KeyboardModifier.NoModifier, text
    )
    release = QKeyEvent(
        QEvent.Type.KeyRelease, int(key), Qt.KeyboardModifier.NoModifier, ""
    )
    QApplication.sendEvent(widget, press)
    QApplication.sendEvent(widget, release)
    QApplication.processEvents()


def _wait_until(predicate, timeout_ms: int = 1200):
    import time

    end = time.monotonic() + (timeout_ms / 1000.0)
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


class _TestClientApp(ClientApp):
    def __init__(self):
        storage_patch = patch("main.get_storage", return_value=_FakeStorage())
        storage_patch.start()
        try:
            super().__init__(enable_monitor=False, init_data=False)
        finally:
            storage_patch.stop()
        self.pasted = []

    def handle_paste(self, data):
        self.pasted.append(data)


class _OpenTraceClientApp(_TestClientApp):
    def __init__(self):
        self.open_calls = []
        super().__init__()

    def show_at_cursor(self):
        self.open_calls.append("show_at_cursor")
        return super().show_at_cursor()

    def _on_ui_opened(self):
        self.open_calls.append("on_ui_opened")
        return super()._on_ui_opened()


class _FakeStorage:
    def __init__(self, history=None, pinned=None):
        self._history = [dict(c) for c in (history or [])]
        self._pinned = [dict(c) for c in (pinned or [])]
        self.need_backup = False

    def set_backup_callback(self, callback):
        return


    def clear_backup_flag(self):
        self.need_backup = False

    def search_history(self, query: str, limit=None, ranked=True):
        q = (query or "").lower()
        rows = [c for c in self._history if q in str(c.get("content", "")).lower()]
        return rows if limit is None else rows[:limit]

    def search_pinned(self, query: str, limit=None, ranked=True):
        q = (query or "").lower()
        rows = [c for c in self._pinned if q in str(c.get("content", "")).lower()]
        return rows if limit is None else rows[:limit]

    def get_history(self, limit=20, offset=0):
        return list(self._history)[offset : offset + limit]

    def get_groups(self):
        names = {
            str(c.get("group_name", ""))
            for c in self._pinned
            if str(c.get("group_name", "")).strip()
        }
        return sorted(names)

    def get_clips_by_group(self, group_name):
        return [c for c in self._pinned if c.get("group_name") == group_name]

    def get_ungrouped_pinned(self, limit=50, offset=0):
        rows = [
            c
            for c in self._pinned
            if not str(c.get("group_name", "")).strip()
        ]
        return rows[offset : offset + limit]

    def get_clip_by_id(self, clip_id):
        for clip in self._history + self._pinned:
            if clip.get("id") == clip_id:
                return dict(clip)
        return None

    def pin_clip(self, clip_id):
        for idx, clip in enumerate(list(self._history)):
            if clip.get("id") == clip_id:
                pinned = dict(clip)
                pinned["is_pinned"] = 1
                self._history.pop(idx)
                self._pinned.insert(0, pinned)
                return True
        return True

    def unpin_clip(self, clip_id):
        for idx, clip in enumerate(list(self._pinned)):
            if clip.get("id") == clip_id:
                history = dict(clip)
                history["is_pinned"] = 0
                history["group_name"] = ""
                self._pinned.pop(idx)
                self._history.insert(0, history)
                return True
        return True

    def update_tag(self, clip_id, tag):
        for clip in self._history + self._pinned:
            if clip.get("id") == clip_id:
                clip["tag"] = tag
                return True
        return False

    def update_group(self, clip_id, group_name):
        for clip in self._pinned:
            if clip.get("id") == clip_id:
                clip["group_name"] = group_name
                return True
        return False

    def update_clip_content(self, clip_id, new_content):
        for clip in self._history + self._pinned:
            if clip.get("id") != clip_id and clip.get("content") == new_content:
                raise ValueError("Clip content already exists.")
        for clip in self._history + self._pinned:
            if clip.get("id") == clip_id:
                clip["content"] = new_content
                return True
        return False


    def delete_clip(self, clip_id):
        self._history = [c for c in self._history if c.get("id") != clip_id]
        self._pinned = [c for c in self._pinned if c.get("id") != clip_id]
        return True


class _SlowFakeStorage(_FakeStorage):
    def get_history(self, limit=20, offset=0):
        time.sleep(0.25)
        return super().get_history(limit, offset)


class _SlowSearchStorage(_FakeStorage):
    def search_history(self, query: str, limit=None, ranked=True):
        time.sleep(0.25)
        return super().search_history(query, limit, ranked)

    def search_pinned(self, query: str, limit=None, ranked=True):
        time.sleep(0.25)
        return super().search_pinned(query, limit, ranked)


class _OutOfOrderSearchStorage(_FakeStorage):
    def search_history(self, query: str, limit=None, ranked=True):
        if query == "slow":
            time.sleep(0.2)
        rows = [{"id": 1, "type": "text", "content": f"{query} result"}]
        return rows if limit is None else rows[:limit]

    def search_pinned(self, query: str, limit=None, ranked=True):
        if query == "slow":
            time.sleep(0.2)
        return []


class _DeleteObservingStorage(_FakeStorage):
    def __init__(self, app, history=None, pinned=None):
        super().__init__(history=history, pinned=pinned)
        self.app = app
        self.history_count_seen_during_delete = None

    def delete_clip(self, clip_id):
        self.history_count_seen_during_delete = self.app.list_history.count()
        return super().delete_clip(clip_id)


class _SlowDeleteStorage(_FakeStorage):
    def __init__(self, history=None, pinned=None):
        super().__init__(history=history, pinned=pinned)
        self.delete_calls = []

    def delete_clip(self, clip_id):
        self.delete_calls.append(clip_id)
        time.sleep(0.25)
        return super().delete_clip(clip_id)


class _SpyClientApp(_TestClientApp):
    def __init__(self):
        super().__init__()
        self.search_runs = 0

    def _do_search(self):
        self.search_runs += 1
        return super()._do_search()


class _FakeUser32:
    def __init__(self, foreground=100, target=200):
        self.foreground = foreground
        self.target = target
        self.calls = []

    def GetAsyncKeyState(self, key):
        return 0

    def IsWindow(self, hwnd):
        self.calls.append(("IsWindow", hwnd))
        return hwnd == self.target

    def IsIconic(self, hwnd):
        self.calls.append(("IsIconic", hwnd))
        return False

    def ShowWindow(self, hwnd, command):
        self.calls.append(("ShowWindow", hwnd, command))
        return True

    def GetForegroundWindow(self):
        self.calls.append(("GetForegroundWindow",))
        return self.foreground

    def GetWindowThreadProcessId(self, hwnd, process_id):
        self.calls.append(("GetWindowThreadProcessId", hwnd))
        return hwnd + 10

    def AttachThreadInput(self, source_thread, target_thread, attach):
        self.calls.append(("AttachThreadInput", source_thread, target_thread, attach))
        return True

    def BringWindowToTop(self, hwnd):
        self.calls.append(("BringWindowToTop", hwnd))
        return True

    def SetForegroundWindow(self, hwnd):
        self.calls.append(("SetForegroundWindow", hwnd))
        self.foreground = hwnd
        return True

    def SetFocus(self, hwnd):
        self.calls.append(("SetFocus", hwnd))
        return hwnd


class _FakeKernel32:
    def GetCurrentThreadId(self):
        return 999


class _FakeWindll:
    def __init__(self, user32):
        self.user32 = user32
        self.kernel32 = _FakeKernel32()


class KeyboardNavigationTests(unittest.TestCase):
    def test_search_debounce_is_100ms_for_responsive_typing(self):
        self.assertEqual(SEARCH_DEBOUNCE_MS, 100)

    @classmethod
    def setUpClass(cls):
        _get_qapp()

    def test_search_line_edit_forwards_vertical_nav_but_keeps_horizontal_text_navigation(self):
        _get_qapp()
        calls = {"up": 0, "down": 0, "left": 0, "right": 0, "enter": 0}
        w = SearchLineEdit()
        w.set_key_handlers(
            on_up=lambda: calls.__setitem__("up", calls["up"] + 1),
            on_down=lambda: calls.__setitem__("down", calls["down"] + 1),
            on_left=lambda: calls.__setitem__("left", calls["left"] + 1),
            on_right=lambda: calls.__setitem__("right", calls["right"] + 1),
            on_enter=lambda: calls.__setitem__("enter", calls["enter"] + 1),
        )
        w.show()
        w.setFocus()
        QApplication.processEvents()
        _send_key(w, Qt.Key.Key_A, text="a")
        _send_key(w, Qt.Key.Key_B, text="b")
        w.setCursorPosition(2)
        _send_key(w, Qt.Key.Key_Left)
        self.assertEqual(w.cursorPosition(), 1)
        _send_key(w, Qt.Key.Key_Right)
        self.assertEqual(w.cursorPosition(), 2)
        self.assertEqual(w.text(), "ab")
        _send_key(w, Qt.Key.Key_Up)
        _send_key(w, Qt.Key.Key_Return)
        self.assertEqual(calls["up"], 1)
        self.assertEqual(calls["left"], 0)
        self.assertEqual(calls["right"], 0)
        self.assertEqual(calls["enter"], 1)
        w.close()
        QApplication.processEvents()

    def test_client_app_defaults_to_history_active_and_enter_pastes_active_item(self):
        _get_qapp()
        app = _TestClientApp()
        app.show()
        h_item = QListWidgetItem()
        h_item.setData(
            Qt.ItemDataRole.UserRole, {"id": 1, "type": "text", "content": "H"}
        )
        app.list_history.addItem(h_item)
        app._on_ui_opened()
        self.assertEqual(app.active_side, "history")
        _send_key(app.search_input, Qt.Key.Key_Return)
        self.assertEqual([d.get("content") for d in app.pasted], ["H"])
        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()

    def test_grouped_pinned_switch_skips_header_and_enter_pastes_child_clip(self):
        _get_qapp()
        app = _TestClientApp()
        app.show()
        app.list_history.addItem(QListWidgetItem())  # just to open
        app.list_pinned.addItem(QListWidgetItem("Header"))
        child = QListWidgetItem()
        child.setData(
            Qt.ItemDataRole.UserRole, {"id": 2, "type": "text", "content": "C"}
        )
        app.list_pinned.addItem(child)
        app._on_ui_opened()
        app.set_active_side("pinned")
        self.assertEqual(app.list_pinned.currentRow(), 1)
        _send_key(app.search_input, Qt.Key.Key_Return)
        self.assertEqual([d.get("content") for d in app.pasted], ["C"])
        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()

    def test_both_sides_empty_navigation_is_safe(self):
        _get_qapp()
        app = _SpyClientApp()
        app.storage = _FakeStorage(history=[], pinned=[])
        app.show()
        app._on_ui_opened()
        for key in [
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Return,
        ]:
            _send_key(app.search_input, key)
        self.assertIsNone(app.list_history.currentItem())
        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()

    def test_up_down_skips_headers_in_middle_of_list(self):
        _get_qapp()
        app = _TestClientApp()
        app.show()
        app._on_ui_opened()
        w = app.list_history
        w.clear()
        it_a = QListWidgetItem()
        it_a.setData(
            Qt.ItemDataRole.UserRole, {"id": 10, "type": "text", "content": "A"}
        )
        w.addItem(it_a)
        w.addItem(QListWidgetItem("Header"))
        it_b = QListWidgetItem()
        it_b.setData(
            Qt.ItemDataRole.UserRole, {"id": 11, "type": "text", "content": "B"}
        )
        w.addItem(it_b)
        w.setCurrentRow(0)
        _send_key(app.search_input, Qt.Key.Key_Down)
        self.assertEqual(w.currentRow(), 2)
        _send_key(app.search_input, Qt.Key.Key_Up)
        self.assertEqual(w.currentRow(), 0)
        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()

    def test_debounced_search_and_navigation_coexist_async(self):
        _get_qapp()
        history = [
            {"id": 1, "type": "text", "content": "abcd"},
            {"id": 2, "type": "text", "content": "abxd"},
        ]
        app = _SpyClientApp()
        app.storage = _FakeStorage(history=history, pinned=[])
        app.show()
        app._on_ui_opened()
        for ch in "abc":
            _send_key(app.search_input, Qt.Key(ord(ch.upper())), text=ch)
        _wait_until(lambda: app.search_runs >= 1 and app.list_history.count() == 1)
        _send_key(app.search_input, Qt.Key.Key_Down)
        _send_key(app.search_input, Qt.Key.Key_D, text="d")
        _wait_until(lambda: app.search_runs >= 2 and app.list_history.count() == 1)
        self.assertEqual(
            app.list_history.currentItem().data(Qt.ItemDataRole.UserRole).get("id"), 1
        )
        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()

    def test_select_fallback_next_previous_first_skipping_headers(self):
        _get_qapp()
        app = _TestClientApp()
        app.show()
        app._on_ui_opened()
        w = app.list_history
        w.clear()
        w.addItem(QListWidgetItem("Header"))

        def add(cid):
            it = QListWidgetItem()
            it.setData(
                Qt.ItemDataRole.UserRole, {"id": cid, "type": "text", "content": "x"}
            )
            w.addItem(it)

        add(1)
        add(2)
        add(3)
        w.setCurrentRow(2)  # at id=2
        app._select_with_fallback_rules(w, prev_clip_id=99, prev_row=2)
        self.assertEqual(w.currentItem().data(Qt.ItemDataRole.UserRole).get("id"), 3)
        w.setCurrentRow(3)  # at id=3
        app._select_with_fallback_rules(w, prev_clip_id=99, prev_row=3)
        self.assertEqual(w.currentItem().data(Qt.ItemDataRole.UserRole).get("id"), 2)
        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()

    def test_selection_preserved_across_manual_refresh(self):
        _get_qapp()
        app = _TestClientApp()
        history = [{"id": 1, "type": "text", "content": "H"}]
        app.storage = _FakeStorage(history=history, pinned=[])
        app.show()

        # Initial population
        app.refresh_lists()
        app.list_history.setCurrentRow(0)
        self.assertEqual(app.list_history.currentRow(), 0)

        # Trigger refresh - should stay at id=1
        app.refresh_lists()

        cur = app.list_history.currentItem()
        self.assertIsNotNone(cur)
        self.assertEqual(cur.data(Qt.ItemDataRole.UserRole).get("id"), 1)
        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()

    def test_delete_removes_visible_clip_before_storage_delete_finishes(self):
        _get_qapp()
        app = _TestClientApp()
        history = [{"id": 1, "type": "text", "content": "delete me"}]
        app.storage = _DeleteObservingStorage(app, history=history)
        app.refresh_lists()
        self.assertEqual(app.list_history.count(), 1)

        app.handle_delete(1)

        self.assertEqual(app.list_history.count(), 0)
        self.assertTrue(
            _wait_until(lambda: app.storage.history_count_seen_during_delete == 0)
        )
        self.assertEqual(app.storage.history_count_seen_during_delete, 0)
        self.assertEqual(app.list_history.count(), 0)
        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()

    def test_delete_returns_before_slow_storage_delete_runs(self):
        _get_qapp()
        app = _TestClientApp()
        history = [{"id": 1, "type": "text", "content": "slow delete"}]
        app.storage = _SlowDeleteStorage(history=history)
        app.refresh_lists()

        start = time.monotonic()
        app.handle_delete(1)
        elapsed = time.monotonic() - start

        self.assertLess(elapsed, 0.05)
        self.assertEqual(app.list_history.count(), 0)
        self.assertEqual(app.storage.delete_calls, [])
        self.assertTrue(_wait_until(lambda: app.storage.delete_calls == [1]))
        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()

    def test_repeated_delete_clicks_for_same_clip_are_coalesced(self):
        _get_qapp()
        app = _TestClientApp()
        history = [{"id": 1, "type": "text", "content": "rapid delete"}]
        app.storage = _SlowDeleteStorage(history=history)
        app.refresh_lists()

        app.handle_delete(1)
        app.handle_delete(1)
        app.handle_delete(1)

        self.assertEqual(app.list_history.count(), 0)
        self.assertEqual(app.storage.delete_calls, [])
        self.assertTrue(_wait_until(lambda: app.storage.delete_calls == [1]))
        self.assertEqual(app.storage.delete_calls, [1])
        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()

    def test_ui_show_does_not_select_all_text_and_cursor_at_end(self):
        _get_qapp()
        app = _TestClientApp()
        test_text = "some search query"
        app.search_input.setText(test_text)

        # Call show_at_cursor (which sets focus and position)
        app.show_at_cursor()

        self.assertFalse(app.search_input.hasSelectedText())
        self.assertEqual(app.search_input.cursorPosition(), len(test_text))

        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()

    def test_toggle_visibility_does_not_duplicate_open_flow(self):
        _get_qapp()
        app = _OpenTraceClientApp()
        app.toggle_visibility()
        QApplication.processEvents()

        self.assertEqual(app.open_calls.count("show_at_cursor"), 1)
        self.assertTrue(_wait_until(lambda: app.open_calls.count("on_ui_opened") == 1))
        self.assertEqual(app.open_calls.count("on_ui_opened"), 1)

        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()

    def test_ui_open_resets_selection_to_newest_item(self):
        _get_qapp()
        app = _TestClientApp()
        # Mock storage with some items
        history = [{"id": i, "type": "text", "content": f"item {i}"} for i in range(10)]
        app.storage = _FakeStorage(history=history)
        app.refresh_lists()

        # Select item at index 5
        app.list_history.setCurrentRow(5)
        self.assertEqual(app.list_history.currentRow(), 5)

        # Add new item to top of storage
        new_item = {"id": 99, "type": "text", "content": "newest"}
        app.storage._history.insert(0, new_item)
        app.is_ui_dirty = True

        # Call show_at_cursor
        app.show_at_cursor()
        self.assertTrue(_wait_until(lambda: app.list_history.currentRow() == 0))

        # Verify first item (the new one) is selected
        self.assertEqual(app.list_history.currentRow(), 0)
        cur_data = app.list_history.currentItem().data(Qt.ItemDataRole.UserRole)
        self.assertEqual(cur_data["id"], 99)
        self.assertEqual(app.active_side, "history")

        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()

    def test_ui_open_resets_from_pinned_back_to_history(self):
        _get_qapp()
        app = _TestClientApp()
        app.storage = _FakeStorage(
            history=[{"id": 1, "type": "text", "content": "newest"}],
            pinned=[{"id": 2, "type": "text", "content": "older pinned"}],
        )
        app.refresh_lists()
        app.set_active_side("pinned")
        app.list_pinned.setCurrentRow(0)

        app.show_at_cursor()

        self.assertEqual(app.active_side, "history")
        self.assertEqual(app.list_history.currentRow(), 0)
        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()

    def test_history_group_action_auto_pins_clip(self):
        _get_qapp()
        app = _TestClientApp()
        app.storage = _FakeStorage(
            history=[{"id": 1, "type": "text", "content": "group me", "group_name": ""}],
            pinned=[],
        )
        app.refresh_lists()

        app.handle_set_group(1, "Work")

        self.assertEqual(len(app.storage._history), 0)
        self.assertEqual(app.storage._pinned[0]["group_name"], "Work")
        self.assertEqual(app.active_side, "pinned")
        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()

    def test_history_tag_action_does_not_pin_clip(self):
        _get_qapp()
        app = _TestClientApp()
        app.storage = _FakeStorage(
            history=[{"id": 1, "type": "text", "content": "tag me", "tag": ""}],
            pinned=[],
        )
        app.refresh_lists()

        app.handle_add_tag(1, "todo")

        self.assertEqual(app.storage._history[0]["tag"], "todo")
        self.assertEqual(len(app.storage._pinned), 0)
        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()

    def test_expand_toggle_updates_row_widget_state(self):
        _get_qapp()
        app = _TestClientApp()
        long_text = "\n".join([f"line {i}" for i in range(12)])
        app.storage = _FakeStorage(
            history=[{"id": 1, "type": "text", "content": long_text}],
            pinned=[],
        )
        app.refresh_lists()
        item = app.list_history.item(0)
        row = item.data(Qt.ItemDataRole.UserRole + 100)
        self.assertFalse(row.is_expanded)
        self.assertNotIn(1, app.browser.expanded_clip_ids)

        app.handle_toggle_expand(1)
        item = app.list_history.item(0)
        row = item.data(Qt.ItemDataRole.UserRole + 100)
        self.assertTrue(row.is_expanded)
        self.assertIn(1, app.browser.expanded_clip_ids)

        app.handle_toggle_expand(1)
        item = app.list_history.item(0)
        row = item.data(Qt.ItemDataRole.UserRole + 100)
        self.assertFalse(row.is_expanded)
        self.assertNotIn(1, app.browser.expanded_clip_ids)
        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()

    def test_fix_clip_updates_pinned_content_and_blocks_duplicates(self):
        _get_qapp()
        app = _TestClientApp()
        app.storage = _FakeStorage(
            history=[],
            pinned=[
                {"id": 1, "type": "text", "content": "old pinned", "group_name": ""},
                {"id": 2, "type": "text", "content": "other pinned", "group_name": ""},
            ],
        )
        app.refresh_lists()

        app.handle_fix_clip(1, "new pinned")
        self.assertEqual(app.storage._pinned[0]["content"], "new pinned")

        with self.assertRaises(ValueError):
            app.handle_fix_clip(1, "other pinned")

        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()

    def test_show_at_cursor_returns_before_dirty_refresh_finishes(self):
        _get_qapp()
        app = _TestClientApp()
        app.storage = _SlowFakeStorage(
            history=[{"id": 1, "type": "text", "content": "delayed"}]
        )
        app.is_ui_dirty = True

        start = time.monotonic()
        app.show_at_cursor()
        elapsed = time.monotonic() - start

        self.assertLess(elapsed, 0.1)
        self.assertEqual(app.list_history.count(), 0)
        self.assertTrue(_wait_until(lambda: app.list_history.count() == 1))

        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()

    def test_deferred_dirty_refresh_does_not_block_event_loop_on_slow_storage(self):
        _get_qapp()
        app = _TestClientApp()
        app.storage = _SlowFakeStorage(
            history=[{"id": 1, "type": "text", "content": "delayed"}]
        )
        app.is_ui_dirty = True

        app.show_at_cursor()
        time.sleep(0.07)
        start = time.monotonic()
        QApplication.processEvents()
        elapsed = time.monotonic() - start

        self.assertLess(elapsed, 0.1)
        self.assertTrue(_wait_until(lambda: app.list_history.count() == 1))

        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()

    def test_search_execution_returns_before_slow_storage_search_finishes(self):
        _get_qapp()
        app = _TestClientApp()
        app.storage = _SlowSearchStorage(
            history=[{"id": 1, "type": "text", "content": "delayed search"}],
            pinned=[],
        )
        app.browser.current_search_query = "delayed"

        start = time.monotonic()
        app.browser._do_search()
        elapsed = time.monotonic() - start

        self.assertLess(elapsed, 0.1)
        self.assertEqual(app.list_history.count(), 0)
        time.sleep(0.07)
        start = time.monotonic()
        QApplication.processEvents()
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.1)
        self.assertTrue(_wait_until(lambda: app.list_history.count() == 1))

        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()

    def test_newer_search_result_wins_over_older_slow_result(self):
        _get_qapp()
        app = _TestClientApp()
        app.storage = _OutOfOrderSearchStorage()

        app.browser.current_search_query = "slow"
        app.browser._do_search()
        time.sleep(0.07)
        QApplication.processEvents()

        app.browser.current_search_query = "fast"
        app.browser._do_search()
        time.sleep(0.07)
        QApplication.processEvents()

        self.assertTrue(_wait_until(lambda: app.list_history.count() == 1))
        current = app.list_history.item(0).data(Qt.ItemDataRole.UserRole)
        self.assertEqual(current["content"], "fast result")

        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()

    def test_ready_to_paste_restores_last_active_window_before_ctrl_v(self):
        _get_qapp()
        with patch("main.get_storage", return_value=_FakeStorage()):
            app = ClientApp(enable_monitor=False, init_data=False)
        app.last_active_window_handle = 200
        user32 = _FakeUser32(foreground=100, target=200)

        with patch.object(sys, "platform", "win32"), patch(
            "main.ctypes.windll", _FakeWindll(user32), create=True
        ):
            self.assertTrue(app._ready_to_paste())

        call_names = [c[0] for c in user32.calls]
        self.assertIn("SetForegroundWindow", call_names)
        self.assertIn("SetFocus", call_names)
        self.assertEqual(user32.foreground, 200)

        app.backup_scheduler.cancel()
        app.close()
        QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()

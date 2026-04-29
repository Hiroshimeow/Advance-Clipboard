import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from PyQt6.QtCore import QEvent
from PyQt6.QtGui import QKeyEvent

# Ensure headless Qt (caller also sets this env var)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Mock heavy Neural modules BEFORE importing main to avoid WebEngine crash in tests
sys.modules.setdefault("neural.engine", MagicMock())
sys.modules.setdefault("neural.ui", MagicMock())
sys.modules.setdefault("neural.bridge", MagicMock())
sys.modules.setdefault("PyQt6.QtWebEngineWidgets", MagicMock())
sys.modules.setdefault("PyQt6.QtWebChannel", MagicMock())

# Patch NeuralEngine and SidecarWindow with safe stubs
import types

_neural_engine_mod = types.ModuleType("neural.engine")


class _StubEngine:
    name = "NeuralEngine"

    def __init__(self, *a, **kw):
        pass

    def start(self):
        pass

    def stop(self):
        pass


_neural_engine_mod.NeuralEngine = _StubEngine
sys.modules["neural.engine"] = _neural_engine_mod

_neural_ui_mod = types.ModuleType("neural.ui")


class _StubSidecar:
    def __init__(self, *a, **kw):
        self.bridge = MagicMock()
        self.bridge.node_clicked = MagicMock()
        self.bridge.node_clicked.connect = MagicMock()
        self.search_bar = MagicMock()
        self.search_bar.textChanged = MagicMock()
        self.search_bar.textChanged.connect = MagicMock()

    def show(self):
        pass

    def hide(self):
        pass

    def close(self):
        pass

    def move(self, *a):
        pass

    def resize(self, *a):
        pass

    def setGeometry(self, *a):
        pass

    def setWindowTitle(self, *a):
        pass

    def update_data(self, *a):
        pass

    def focus_node(self, *a):
        pass

    def focus_query(self, *a):
        pass

    def reload_config(self, *a):
        pass

    def grab(self):
        return MagicMock()

    def isVisible(self):
        return False

    def isActiveWindow(self):
        return False


_neural_ui_mod.SidecarWindow = _StubSidecar
sys.modules["neural.ui"] = _neural_ui_mod


from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QListWidgetItem

try:
    from PyQt6.QtTest import QSignalSpy
except Exception:  # pragma: no cover
    QSignalSpy = None


from main import ClientApp
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
        super().__init__(enable_monitor=False, init_data=False)
        self.pasted = []

    def handle_paste(self, data):
        self.pasted.append(data)


class _FakeStorage:
    def __init__(self, history=None, pinned=None):
        self._history = history or []
        self._pinned = pinned or []
        self.need_backup = False

    def set_backup_callback(self, callback):
        return

    def set_neural_event_callback(self, callback):
        return

    def clear_backup_flag(self):
        self.need_backup = False

    def search_history(self, query: str):
        q = (query or "").lower()
        return [c for c in self._history if q in str(c.get("content", "")).lower()]

    def search_pinned(self, query: str):
        q = (query or "").lower()
        return [c for c in self._pinned if q in str(c.get("content", "")).lower()]

    def get_history(self, limit=20, offset=0):
        return list(self._history)[offset : offset + limit]

    def get_groups(self):
        return []

    def get_ungrouped_pinned(self, limit=50, offset=0):
        return list(self._pinned)[offset : offset + limit]

    def trigger_daily_rebuild(self):
        pass


class _SlowFakeStorage(_FakeStorage):
    def get_history(self, limit=20, offset=0):
        time.sleep(0.25)
        return super().get_history(limit, offset)


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
    @classmethod
    def setUpClass(cls):
        _get_qapp()

    def test_search_line_edit_forwards_nav_keys_but_allows_text_and_backspace(self):
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
        self.assertEqual(w.text(), "a")
        _send_key(w, Qt.Key.Key_Up)
        _send_key(w, Qt.Key.Key_Left)
        _send_key(w, Qt.Key.Key_Return)
        self.assertEqual(calls["up"], 1)
        self.assertEqual(calls["left"], 1)
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

    def test_ready_to_paste_restores_last_active_window_before_ctrl_v(self):
        _get_qapp()
        app = ClientApp(enable_monitor=False, init_data=False)
        app.last_active_window_handle = 200
        user32 = _FakeUser32(foreground=100, target=200)

        with patch.object(sys, "platform", "win32"), patch(
            "main.ctypes.windll", _FakeWindll(user32)
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

import os
import sys
import time
import unittest
from unittest.mock import patch

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import main
from main import ClientApp


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


def _wait_until(predicate, timeout_ms=1200):
    end = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    QApplication.processEvents()
    return bool(predicate())


class _StubSignal:
    def connect(self, callback):
        self.callback = callback


class _StubSearchBar:
    def __init__(self):
        self.textChanged = _StubSignal()


class _StubBridge:
    def __init__(self):
        self.node_clicked = _StubSignal()


class _StubSidecar:
    def __init__(self, *args, **kwargs):
        self.bridge = _StubBridge()
        self.search_bar = _StubSearchBar()
        self.visible = False
        self.hide_calls = 0
        self.show_calls = 0
        self.update_calls = 0
        self.reload_calls = 0
        self.focus_queries = []
        self._docked_mode = None

    def setGeometry(self, *args):
        self.geometry_args = args

    def move(self, *args):
        self.move_args = args

    def resize(self, *args):
        self.resize_args = args

    def show(self):
        self.show_calls += 1
        self.visible = True

    def hide(self):
        self.hide_calls += 1
        self.visible = False

    def close(self):
        self.visible = False

    def isVisible(self):
        return self.visible

    def activateWindow(self):
        self.activated = True

    def raise_(self):
        self.raised = True

    def reload_config(self):
        self.reload_calls += 1

    def update_data(self, nodes, links):
        self.update_calls += 1
        self.nodes = nodes
        self.links = links

    def focus_query(self, query):
        self.focus_queries.append(query)


class _StubEngine:
    create_calls = 0

    def __init__(self, *args, **kwargs):
        type(self).create_calls += 1
        self.start_calls = 0
        self.stop_calls = 0

    def start(self):
        self.start_calls += 1

    def stop(self):
        self.stop_calls += 1

    def enqueue_new_clip(self, clip_id):
        self.last_new_clip = clip_id

    def enqueue_priority_reindex(self, clip_id):
        self.last_priority_clip = clip_id


class _StubStorage:
    def __init__(self):
        self.need_backup = False
        self.vector_fetch_calls = 0
        self.graph_fetch_calls = 0

    def trigger_daily_rebuild(self):
        pass

    def set_backup_callback(self, callback):
        self.backup_callback = callback

    def set_neural_event_callback(self, callback):
        self.neural_callback = callback

    def clear_backup_flag(self):
        self.need_backup = False

    def get_all_clip_ids_with_vectors(self, limit=500):
        self.vector_fetch_calls += 1
        return [1, 2]

    def get_neural_data(self, clip_ids):
        self.graph_fetch_calls += 1
        nodes = [{"id": cid, "label": f"clip {cid}"} for cid in clip_ids]
        links = [{"source_id": 1, "target_id": 2, "weight": 0.75}]
        return nodes, links


class _SlowGraphStorage(_StubStorage):
    def get_all_clip_ids_with_vectors(self, limit=500):
        time.sleep(0.25)
        return super().get_all_clip_ids_with_vectors(limit)


class NeuralLazyLoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_qapp()

    def _make_app(self, storage=None):
        self.storage = storage or _StubStorage()
        _StubEngine.create_calls = 0
        patches = [
            patch.object(main, "HAS_NEURAL_SUPPORT", True),
            patch.object(main, "NeuralEngine", _StubEngine),
            patch.object(main, "SidecarWindow", _StubSidecar),
            patch.object(main, "get_storage", return_value=self.storage),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        app = ClientApp(enable_monitor=False, init_data=False)
        self.addCleanup(app.backup_scheduler.cancel)
        self.addCleanup(app.close)
        return app

    def test_neural_engine_is_not_started_during_initialization(self):
        app = self._make_app()

        self.assertIsNone(app.neural_engine)
        self.assertIsNone(app.sidecar)
        self.assertEqual(0, _StubEngine.create_calls)
        self.assertFalse(app._neural_engine_started)
        self.assertEqual("not_started", app._neural_map_state)

    def test_first_map_toggle_starts_engine_and_fetches_graph_once(self):
        app = self._make_app()

        app._toggle_neural(True)

        self.assertTrue(_wait_until(lambda: app._neural_map_state == "ready"))
        self.assertEqual(1, app.neural_engine.start_calls)
        self.assertEqual(1, _StubEngine.create_calls)
        self.assertTrue(app._neural_engine_started)
        self.assertTrue(app._galaxy_loaded)
        self.assertEqual("ready", app._neural_map_state)
        self.assertEqual(1, self.storage.vector_fetch_calls)
        self.assertEqual(1, self.storage.graph_fetch_calls)
        self.assertEqual(1, app.sidecar.update_calls)

    def test_second_map_toggle_reuses_cached_graph_without_restart_or_reload(self):
        app = self._make_app()

        app._toggle_neural(True)
        app._toggle_neural(False)
        self.assertTrue(_wait_until(lambda: app._neural_map_state == "ready"))
        app._toggle_neural(True)

        self.assertEqual(1, app.neural_engine.start_calls)
        self.assertTrue(app._galaxy_loaded)
        self.assertEqual(1, self.storage.vector_fetch_calls)
        self.assertEqual(1, self.storage.graph_fetch_calls)
        self.assertEqual(0, app.sidecar.reload_calls)

    def test_toggle_off_only_hides_sidecar(self):
        app = self._make_app()

        app._toggle_neural(False)

        self.assertIsNone(app.sidecar)
        self.assertIsNone(app.neural_engine)
        self.assertEqual(0, self.storage.graph_fetch_calls)

    def test_floating_open_reuses_cached_graph_after_first_load(self):
        app = self._make_app()

        app._show_neural_floating()
        self.assertTrue(_wait_until(lambda: app._neural_map_state == "ready"))
        app.sidecar.hide()
        app._show_neural_floating()

        self.assertEqual(1, app.neural_engine.start_calls)
        self.assertTrue(app._galaxy_loaded)
        self.assertEqual(1, self.storage.vector_fetch_calls)
        self.assertEqual(1, self.storage.graph_fetch_calls)
        self.assertEqual(0, app.sidecar.reload_calls)

    def test_first_toggle_returns_before_background_graph_fetch_finishes(self):
        app = self._make_app(_SlowGraphStorage())

        start = time.monotonic()
        app._toggle_neural(True)
        elapsed = time.monotonic() - start

        self.assertLess(elapsed, 0.15)
        self.assertEqual("loading", app._neural_map_state)
        self.assertTrue(_wait_until(lambda: app._neural_map_state == "ready"))
        self.assertEqual(1, self.storage.graph_fetch_calls)


if __name__ == "__main__":
    unittest.main()

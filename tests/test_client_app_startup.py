import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

# Ensure headless Qt (caller also sets this env var)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Stub heavy neural modules before importing main.
sys.modules.setdefault("PyQt6.QtWebEngineWidgets", MagicMock())
sys.modules.setdefault("PyQt6.QtWebChannel", MagicMock())

_neural_engine_mod = types.ModuleType("neural.engine")


class _StubEngine:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass


_neural_engine_mod.NeuralEngine = _StubEngine
sys.modules["neural.engine"] = _neural_engine_mod

_neural_ui_mod = types.ModuleType("neural.ui")


class _StubSidecar:
    def __init__(self, *args, **kwargs):
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

    def move(self, *args):
        pass

    def resize(self, *args):
        pass

    def setGeometry(self, *args):
        pass

    def setWindowTitle(self, *args):
        pass

    def update_data(self, *args):
        pass

    def focus_node(self, *args):
        pass

    def focus_query(self, *args):
        pass

    def reload_config(self, *args):
        pass

    def grab(self):
        return MagicMock()

    def isVisible(self):
        return False

    def isActiveWindow(self):
        return False


_neural_ui_mod.SidecarWindow = _StubSidecar
sys.modules["neural.ui"] = _neural_ui_mod

from PyQt6.QtWidgets import QApplication

import main
from main import ClientApp


_APP: QApplication | None = None


def _get_qapp() -> QApplication:
    global _APP
    inst = QApplication.instance()
    if isinstance(inst, QApplication):
        _APP = inst
        return _APP

    _APP = QApplication([])
    _APP.setQuitOnLastWindowClosed(False)
    return _APP


class _StartupStorage:
    def __init__(self):
        self.need_backup = False
        self.rebuild_calls = 0

    def trigger_daily_rebuild(self):
        self.rebuild_calls += 1

    def set_backup_callback(self, callback):
        self.backup_callback = callback

    def set_neural_event_callback(self, callback):
        self.neural_event_callback = callback

    def clear_backup_flag(self):
        self.need_backup = False


class ClientAppStartupTests(unittest.TestCase):
    def setUp(self):
        _get_qapp()

    def _make_app(self, storage):
        with patch.object(main, "get_storage", return_value=storage), patch.object(
            main, "get_neural_support_error", return_value=None
        ):
            return ClientApp(
                enable_monitor=False,
                init_data=False,
                enable_background_jobs=True,
            )

    def test_env_disable_rag_rebuild_suppresses_startup_rebuild(self):
        storage = _StartupStorage()
        with patch.dict(os.environ, {"ADV_CLIP_DISABLE_RAG_REBUILD": "1"}):
            app = self._make_app(storage)

        self.addCleanup(app.backup_scheduler.cancel)
        self.addCleanup(app.close)
        self.assertEqual(storage.rebuild_calls, 0)

    def test_background_jobs_enabled_triggers_startup_rebuild_by_default(self):
        storage = _StartupStorage()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ADV_CLIP_DISABLE_RAG_REBUILD", None)
            app = self._make_app(storage)

        self.addCleanup(app.backup_scheduler.cancel)
        self.addCleanup(app.close)
        self.assertEqual(storage.rebuild_calls, 1)


if __name__ == "__main__":
    unittest.main()

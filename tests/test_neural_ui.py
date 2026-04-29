import os
import sys
import types

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QWidget


class _StubSignal(QObject):
    triggered = pyqtSignal()

    def connect(self, callback):
        self.triggered.connect(callback)


class _StubPage:
    def __init__(self):
        self.js_calls = []
        self.web_channel = None

    def setWebChannel(self, channel):
        self.web_channel = channel

    def runJavaScript(self, script):
        self.js_calls.append(script)


class _StubWebView(QWidget):
    def __init__(self):
        super().__init__()
        self.loadFinished = _StubSignal()
        self._page = _StubPage()

    def setUrl(self, url):
        self.url = url

    def page(self):
        return self._page


class _StubWebChannel:
    def registerObject(self, *args, **kwargs):
        return None


class _StubBridge:
    pass


stub_webengine = types.ModuleType("PyQt6.QtWebEngineWidgets")
stub_webengine.QWebEngineView = _StubWebView
sys.modules["PyQt6.QtWebEngineWidgets"] = stub_webengine

stub_webchannel = types.ModuleType("PyQt6.QtWebChannel")
stub_webchannel.QWebChannel = _StubWebChannel
sys.modules["PyQt6.QtWebChannel"] = stub_webchannel

stub_bridge = types.ModuleType("neural.bridge")
stub_bridge.NeuralBridge = _StubBridge
sys.modules["neural.bridge"] = stub_bridge

for module_name in ("neural.ui", "neural.engine"):
    if module_name in sys.modules:
        del sys.modules[module_name]

from storage import get_storage
from neural.ui import SidecarWindow


_APP: QApplication | None = None


def _get_qapp() -> QApplication:
    global _APP
    if _APP is not None:
        return _APP
    inst = QApplication.instance()
    if isinstance(inst, QApplication):
        _APP = inst
        return _APP
    _APP = QApplication([sys.argv[0] or "test_neural_ui.py"])
    _APP.setQuitOnLastWindowClosed(False)
    return _APP


def _load_mock_data(sidecar, store, limit=50):
    ids = store.get_all_clip_ids_with_vectors(limit=limit)
    nodes, links = store.get_neural_data(ids)
    formatted_links = [
        {
            "source": l["source_id"],
            "target": l["target_id"],
            "weight": float(l["weight"]),
        }
        for l in links
    ]
    sidecar.update_data(nodes, formatted_links)
    return ids


def test_sidecar_accepts_graph_data_before_page_ready():
    _get_qapp()
    store = get_storage()
    sidecar = SidecarWindow(store)
    try:
        sidecar.show()

        ids = _load_mock_data(sidecar, store)

        if ids:
            assert sidecar._pending_graph_payload is not None
            assert not sidecar._page_ready
    finally:
        sidecar.close()


def _run_interactive_smoke():
    app = _get_qapp()
    store = get_storage()
    sidecar = SidecarWindow(store)
    sidecar.show()

    def mock_data():
        ids = _load_mock_data(sidecar, store)
        if ids:
            sidecar.focus_node(ids[0])

    # Manual smoke mode for local use; not exercised by pytest.
    mock_data()
    app.processEvents()
    sidecar.close()
    return 0


if __name__ == "__main__":
    sys.exit(_run_interactive_smoke())

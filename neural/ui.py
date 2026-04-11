import os
import json
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLineEdit,
    QPushButton,
    QHBoxLayout,
    QApplication,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtCore import Qt, QUrl, QEvent, pyqtSignal, QTimer
from neural.bridge import NeuralBridge


class SidecarWindow(QWidget):
    lost_focus_external = pyqtSignal()

    def __init__(self, storage, main_window=None, parent=None):
        super().__init__(
            parent, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint
        )
        self.storage = storage
        self._main_window = main_window
        self._page_ready = False
        self._pending_graph_payload = None
        self._pending_focus_id = None
        self._docked_mode = False  # True = tied to main UI, False = independent
        self._graph_config = self._load_graph_config()
        self.setWindowTitle("Neural Memory Map")
        self.resize(500, 600)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        bg = self._graph_config.get("background_color", "#000000")
        self.setStyleSheet(f"background-color: {bg}; color: #00ff41;")

        self.initUI()
        self.load_graph()

    def _load_graph_config(self):
        """Load graph visual settings from config.json."""
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        defaults = {
            "node_base_radius": 2,
            "node_degree_scale": 0.8,
            "node_stroke_width": 1,
            "node_color": "#003b00",
            "node_stroke_color": "#00ff41",
            "link_color": "#003b00",
            "link_width": 0.5,
            "label_font_size": 7,
            "label_color": "#005511",
            "label_max_chars": 18,
            "highlight_font_size": 9,
            "highlight_color": "#00ff41",
            "search_highlight_stroke": "#ffffff",
            "search_highlight_width": 2,
            "force_link_distance": 80,
            "force_charge_strength": -150,
            "force_collision_base": 10,
            "force_collision_degree_scale": 2,
            "background_color": "#000000",
            "tooltip_max_chars": 500,
            "rainbow_mode": True,
        }
        try:
            if os.path.exists(config_path) and os.path.getsize(config_path) > 0:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                graph = cfg.get("graph", {})
                for k, v in graph.items():
                    if not k.startswith("_"):
                        defaults[k] = v
        except Exception:
            pass
        return defaults

    def initUI(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search neural nodes...")
        self.search_bar.setStyleSheet(
            "background-color: #001100; color: #00ff41; border: 1px solid #00ff41; padding: 5px; font-family: 'Courier New';"
        )
        header.addWidget(self.search_bar)

        self.btn_close = QPushButton("X")
        self.btn_close.setFixedSize(30, 30)
        self.btn_close.setStyleSheet(
            "background: #001100; color: #ff0000; border: 1px solid #ff0000;"
        )
        self.btn_close.clicked.connect(self.hide)
        header.addWidget(self.btn_close)

        layout.addLayout(header)

        self.web_view = QWebEngineView()
        self.web_view.setStyleSheet("background-color: #000;")
        self.web_view.loadFinished.connect(self._on_graph_loaded)

        self.channel = QWebChannel()
        self.bridge = NeuralBridge()
        self.channel.registerObject("pybridge", self.bridge)
        self.web_view.page().setWebChannel(self.channel)

        layout.addWidget(self.web_view)

        # Wire sidecar search bar with debounce
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._on_local_search)
        self.search_bar.textChanged.connect(lambda t: self._search_timer.start(300))

    def _on_local_search(self):
        """Handle search typed directly in sidecar search bar."""
        q = self.search_bar.text().strip()
        self.focus_query(q)

    def reload_config(self):
        """Re-read config.json and inject into D3."""
        self._graph_config = self._load_graph_config()
        bg = self._graph_config.get("background_color", "#000000")
        self.setStyleSheet(f"background-color: {bg}; color: #00ff41;")
        if self._page_ready:
            cfg_json = json.dumps(self._graph_config)
            self.web_view.page().runJavaScript(
                f"if(typeof applyConfig==='function') applyConfig({cfg_json})"
            )
            print("[Sidecar] Config reloaded and injected into D3")

    def load_graph(self):
        self._page_ready = False
        html_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "graph", "index.html")
        )
        print(f"[Sidecar] Loading graph HTML: {html_path}")
        self.web_view.setUrl(QUrl.fromLocalFile(html_path))

    def _on_graph_loaded(self, ok: bool):
        self._page_ready = bool(ok)
        print(f"[Sidecar] Page loadFinished ok={ok}, page_ready={self._page_ready}")
        if not self._page_ready:
            return
        # Inject config into D3 before sending data
        cfg_json = json.dumps(self._graph_config)
        self.web_view.page().runJavaScript(
            f"if(typeof applyConfig==='function') applyConfig({cfg_json})"
        )
        if self._pending_graph_payload is not None:
            n = len(self._pending_graph_payload.get("nodes", []))
            l = len(self._pending_graph_payload.get("links", []))
            print(f"[Sidecar] Flushing pending payload: {n} nodes, {l} links")
            self._run_update(self._pending_graph_payload)
            self._pending_graph_payload = None
        if self._pending_focus_id is not None:
            print(f"[Sidecar] Flushing pending focus: {self._pending_focus_id}")
            self.focus_node(self._pending_focus_id)
            self._pending_focus_id = None

    def _run_update(self, data):
        json_data = json.dumps(data)
        print(
            f"[Sidecar] runJavaScript updateGraph({len(data.get('nodes', []))} nodes, {len(data.get('links', []))} links)"
        )
        self.web_view.page().runJavaScript(f"updateGraph({json_data})")

    def update_data(self, nodes, links):
        data = {"nodes": nodes, "links": links}
        if not self._page_ready:
            print(
                f"[Sidecar] Page NOT ready — queuing {len(nodes)} nodes, {len(links)} links"
            )
            self._pending_graph_payload = data
            return
        print(
            f"[Sidecar] Page ready — sending {len(nodes)} nodes, {len(links)} links directly"
        )
        self._run_update(data)

    def focus_node(self, clip_id: int):
        if not self._page_ready:
            print(f"[Sidecar] Page NOT ready — queuing focus_node({clip_id})")
            self._pending_focus_id = clip_id
            return
        print(f"[Sidecar] focusNode({clip_id})")
        self.web_view.page().runJavaScript(f"focusNode({clip_id})")

    def focus_query(self, query: str):
        if not self._page_ready:
            print(f"[Sidecar] Page NOT ready — dropping focusQuery({query!r})")
            return
        print(f"[Sidecar] focusQuery({query!r})")
        self.web_view.page().runJavaScript(f"focusQuery({json.dumps(query)})")

    def changeEvent(self, e):
        if e.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            # Only auto-hide when in docked mode (tied to main UI)
            if self._docked_mode:
                active = QApplication.activeWindow()
                if active is not self._main_window:
                    self.hide()
                    if self._main_window and self._main_window.isVisible():
                        self._main_window.hide()
            # In floating mode (Neural button), never auto-hide
        super().changeEvent(e)

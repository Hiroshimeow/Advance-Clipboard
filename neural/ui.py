import os
import json
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QPushButton, QHBoxLayout
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtCore import Qt, QUrl
from neural.bridge import NeuralBridge

class SidecarWindow(QWidget):
    def __init__(self, storage, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.setWindowTitle("Neural Memory Map")
        self.resize(500, 600)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet("background-color: #000; color: #00ff41;")
        
        self.initUI()
        self.load_graph()

    def initUI(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        header = QHBoxLayout()
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search neural nodes...")
        self.search_bar.setStyleSheet("background-color: #001100; color: #00ff41; border: 1px solid #00ff41; padding: 5px; font-family: 'Courier New';")
        header.addWidget(self.search_bar)
        
        self.btn_close = QPushButton("X")
        self.btn_close.setFixedSize(30, 30)
        self.btn_close.setStyleSheet("background: #001100; color: #ff0000; border: 1px solid #ff0000;")
        self.btn_close.clicked.connect(self.hide)
        header.addWidget(self.btn_close)
        
        layout.addLayout(header)

        self.web_view = QWebEngineView()
        self.web_view.setStyleSheet("background-color: #000;")
        
        self.channel = QWebChannel()
        self.bridge = NeuralBridge()
        self.channel.registerObject("pybridge", self.bridge)
        self.web_view.page().setWebChannel(self.channel)
        
        layout.addWidget(self.web_view)

    def load_graph(self):
        html_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "graph", "index.html"))
        self.web_view.setUrl(QUrl.fromLocalFile(html_path))

    def update_data(self, nodes, links):
        data = {"nodes": nodes, "links": links}
        json_data = json.dumps(data)
        self.web_view.page().runJavaScript(f"updateGraph({json_data})")

    def focus_node(self, clip_id: int):
        self.web_view.page().runJavaScript(f"focusNode({clip_id})")

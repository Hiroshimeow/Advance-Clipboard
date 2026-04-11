"""
test_neural_show_screenshot.py — Show the Neural Memory graph and take a screenshot.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

from storage import get_storage
from neural.ui import SidecarWindow

app = QApplication(sys.argv)
store = get_storage()

indexed_ids = store.get_all_clip_ids_with_vectors(limit=100)
nodes, links = store.get_neural_data(indexed_ids)
formatted_links = [
    {"source": l["source_id"], "target": l["target_id"], "weight": float(l["weight"])}
    for l in links
]

sidecar = SidecarWindow(store)
sidecar.setWindowTitle(f"Neural Memory — {len(nodes)} nodes, {len(links)} links")
sidecar.resize(900, 700)
sidecar.show()


def send_data():
    sidecar.update_data(nodes, formatted_links)


def take_screenshot():
    screenshot_path = os.path.join(
        os.path.dirname(__file__), "neural_graph_screenshot.png"
    )
    sidecar.grab().save(screenshot_path)
    print(f"[Screenshot] Saved to {screenshot_path}")
    app.quit()


# Send data after 1.5s, screenshot after 5s (give D3 time to settle)
QTimer.singleShot(1500, send_data)
QTimer.singleShot(5000, take_screenshot)

app.exec()

import os
import sys
import random

# Add project root to path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

from storage import get_storage
from neural.ui import SidecarWindow

app = QApplication(sys.argv)

store = get_storage()
clip_count = store.get_clip_count()
indexed_ids = store.get_all_clip_ids_with_vectors(limit=100)

print(f"[DB] Total clips: {clip_count}")
print(f"[Neural] Indexed clips: {len(indexed_ids)}")

if not indexed_ids:
    print("[SKIP] No indexed clips. Run test_neural_index.py first.")
    sys.exit(0)

nodes, links = store.get_neural_data(indexed_ids)
print(f"[Graph] Nodes: {len(nodes)}, Links: {len(links)}")

if not nodes:
    print("[SKIP] No nodes to display.")
    sys.exit(0)

formatted_links = [
    {"source": l["source_id"], "target": l["target_id"], "weight": float(l["weight"])}
    for l in links
]

sidecar = SidecarWindow(store)
sidecar.setWindowTitle(f"Neural Memory — {len(nodes)} nodes, {len(links)} links")
sidecar.resize(800, 600)
sidecar.show()

random_timer = QTimer()


def send_data():
    print("[UI] Sending graph data to D3...")
    sidecar.update_data(nodes, formatted_links)

    # Bắt đầu random focus sau 2 giây
    QTimer.singleShot(2000, lambda: random_timer.start(2000))


def focus_random():
    if not nodes:
        return

    node = random.choice(nodes)
    node_id = node["id"]
    content = node.get("content", "")
    print(f'[UI] Random focus node {node_id}: "{content[:80]}..."')
    sidecar.focus_node(node_id)


random_timer.timeout.connect(focus_random)

QTimer.singleShot(1000, send_data)
QTimer.singleShot(60000, lambda: (print("[UI] Auto-closing after 30s."), app.quit()))

print("[UI] Window open for 30 seconds. Close manually or wait.")
sys.exit(app.exec())

import os
import sys

# Add project root to path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
from storage import get_storage
from neural.ui import SidecarWindow

app = QApplication(sys.argv)
store = get_storage()
sidecar = SidecarWindow(store)
sidecar.show()


def mock_data():
    ids = store.get_all_clip_ids_with_vectors(limit=50)
    print(f"Loading data for IDs: {ids}")
    nodes, links = store.get_neural_data(ids)
    # get_links returns a list of dicts now
    formatted_links = [
        {
            "source": l["source_id"],
            "target": l["target_id"],
            "weight": float(l["weight"]),
        }
        for l in links
    ]
    sidecar.update_data(nodes, formatted_links)
    if ids:
        QTimer.singleShot(2000, lambda: sidecar.focus_node(ids[0]))


QTimer.singleShot(1000, mock_data)
# Thoát tự động sau 5s để không bị treo khi test
QTimer.singleShot(5000, app.quit)
sys.exit(app.exec())

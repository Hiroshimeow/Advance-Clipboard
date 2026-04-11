from PyQt6.QtCore import QObject, pyqtSlot, pyqtSignal

class NeuralBridge(QObject):
    node_clicked = pyqtSignal(int)
    search_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

    @pyqtSlot(int)
    def onNodeClick(self, clip_id: int):
        self.node_clicked.emit(clip_id)

    @pyqtSlot(str)
    def onSearchRequested(self, query: str):
        self.search_requested.emit(query)

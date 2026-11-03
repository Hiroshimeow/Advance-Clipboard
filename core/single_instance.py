from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

APP_SERVER_NAME = "advance-clipboard-primary-v1"


class SingleInstanceCoordinator(QObject):
    activate_requested = pyqtSignal()

    def __init__(self, *, server_factory=QLocalServer, socket_factory=QLocalSocket):
        super().__init__()
        self._server = server_factory(self)
        self._socket_factory = socket_factory
        self._server.newConnection.connect(self._accept_connections)

    def acquire_or_notify(self) -> bool:
        probe = self._socket_factory()
        probe.connectToServer(APP_SERVER_NAME)
        if probe.waitForConnected(150):
            probe.write(b"activate\n")
            probe.waitForBytesWritten(150)
            probe.disconnectFromServer()
            return False

        QLocalServer.removeServer(APP_SERVER_NAME)
        if self._server.listen(APP_SERVER_NAME):
            return True

        probe.connectToServer(APP_SERVER_NAME)
        if probe.waitForConnected(150):
            probe.write(b"activate\n")
            probe.waitForBytesWritten(150)
            return False
        raise RuntimeError(self._server.errorString())

    def _accept_connections(self):
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket.waitForReadyRead(150):
                if bytes(socket.readAll()).strip() == b"activate":
                    self.activate_requested.emit()
            socket.disconnectFromServer()

    def close(self):
        self._server.close()

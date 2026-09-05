import unittest
from unittest.mock import Mock, patch

from core.single_instance import APP_SERVER_NAME, SingleInstanceCoordinator


class FakeSignal:
    def __init__(self):
        self.callback = None

    def connect(self, callback):
        self.callback = callback


class FakeSocket:
    def __init__(self, *, connects=False, payload=b""):
        self.connects = connects
        self.payload = payload
        self.write = Mock()
        self.disconnectFromServer = Mock()

    def connectToServer(self, _name):
        return None

    def waitForConnected(self, _timeout):
        return self.connects

    def waitForBytesWritten(self, _timeout):
        return True

    def waitForReadyRead(self, _timeout):
        return bool(self.payload)

    def readAll(self):
        return self.payload


class FakeServer:
    def __init__(self, *, listens=True, pending=()):
        self.listens = listens
        self.pending = list(pending)
        self.newConnection = FakeSignal()
        self.listen = Mock(side_effect=lambda _name: self.listens)
        self.close = Mock()

    def hasPendingConnections(self):
        return bool(self.pending)

    def nextPendingConnection(self):
        return self.pending.pop(0)

    def errorString(self):
        return "listen failed"


def make_coordinator(*, connects, listens, pending=()):
    server = FakeServer(listens=listens, pending=pending)
    socket = FakeSocket(connects=connects)
    coordinator = SingleInstanceCoordinator(
        server_factory=lambda _parent: server,
        socket_factory=lambda: socket,
    )
    return coordinator, server, socket


class SingleInstanceTests(unittest.TestCase):
    def test_first_process_listens_and_becomes_primary(self):
        coordinator, server, _socket = make_coordinator(connects=False, listens=True)
        with patch("core.single_instance.QLocalServer.removeServer"):
            self.assertTrue(coordinator.acquire_or_notify())
        server.listen.assert_called_once_with(APP_SERVER_NAME)

    def test_second_process_sends_activate_and_returns_false(self):
        coordinator, _server, socket = make_coordinator(connects=True, listens=False)
        self.assertFalse(coordinator.acquire_or_notify())
        socket.write.assert_called_once_with(b"activate\n")

    def test_activation_message_emits_once(self):
        socket = FakeSocket(payload=b"activate\n")
        coordinator, _server, _probe = make_coordinator(
            connects=False,
            listens=True,
            pending=[socket],
        )
        received = []
        coordinator.activate_requested.connect(lambda: received.append(True))
        coordinator._accept_connections()
        self.assertEqual([True], received)


if __name__ == "__main__":
    unittest.main()

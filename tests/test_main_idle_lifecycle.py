import os
import sys
import unittest
from unittest.mock import patch


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

import main


class _FakeApplication:
    def __init__(self, _argv):
        self.quit_calls = 0

    def setQuitOnLastWindowClosed(self, _enabled):
        pass

    def setStyle(self, _style):
        pass

    def setPalette(self, _palette):
        pass

    def quit(self):
        self.quit_calls += 1

    def exec(self):
        return 0


class _FakeSignal:
    def __init__(self):
        self.callback = None

    def connect(self, callback):
        self.callback = callback


class _FakeTimer:
    created = 0

    def __init__(self):
        type(self).created += 1
        self.timeout = _FakeSignal()

    def start(self, _interval):
        pass


class _FakeClientApp:
    def activate_from_secondary(self):
        pass


class _FakeCoordinator:
    is_primary = True

    def __init__(self):
        self.activate_requested = _FakeSignal()

    def acquire_or_notify(self):
        return type(self).is_primary

    def close(self):
        pass


class MainIdleLifecycleTests(unittest.TestCase):
    def test_main_does_not_create_unconditional_polling_timer(self):
        _FakeTimer.created = 0
        with (
            patch.object(main, "QApplication", _FakeApplication),
            patch.object(main, "QTimer", _FakeTimer),
            patch.object(main, "ClientApp", _FakeClientApp),
            patch.object(main, "SingleInstanceCoordinator", _FakeCoordinator, create=True),
            patch.object(main.sys, "exit", lambda _code: None),
        ):
            main.main()

        self.assertEqual(0, _FakeTimer.created)

    def test_secondary_process_exits_before_constructing_client_app(self):
        _FakeCoordinator.is_primary = False
        constructed = []
        try:
            with (
                patch.object(main, "QApplication", _FakeApplication),
                patch.object(main, "SingleInstanceCoordinator", _FakeCoordinator, create=True),
                patch.object(main, "ClientApp", lambda: constructed.append(True)),
                patch.object(main.sys, "exit", lambda _code: None),
            ):
                main.main()
        finally:
            _FakeCoordinator.is_primary = True

        self.assertEqual([], constructed)


if __name__ == "__main__":
    unittest.main()

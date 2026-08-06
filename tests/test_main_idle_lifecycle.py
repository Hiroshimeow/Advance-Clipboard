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
    def connect(self, _callback):
        pass


class _FakeTimer:
    created = 0

    def __init__(self):
        type(self).created += 1
        self.timeout = _FakeSignal()

    def start(self, _interval):
        pass


class MainIdleLifecycleTests(unittest.TestCase):
    def test_main_does_not_create_unconditional_polling_timer(self):
        _FakeTimer.created = 0
        with (
            patch.object(main, "QApplication", _FakeApplication),
            patch.object(main, "QTimer", _FakeTimer),
            patch.object(main, "ClientApp", lambda: object()),
            patch.object(main.sys, "exit", lambda _code: None),
        ):
            main.main()

        self.assertEqual(0, _FakeTimer.created)


if __name__ == "__main__":
    unittest.main()

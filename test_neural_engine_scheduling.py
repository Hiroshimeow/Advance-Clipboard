import importlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

# test_keyboard_navigation stubs neural.engine globally; force-load the real module here
if "neural.engine" in sys.modules:
    del sys.modules["neural.engine"]
NeuralEngine = importlib.import_module("neural.engine").NeuralEngine


class _DummyStorage:
    def get_neural_window_totals(self, recent_limit=0, include_pinned=True):
        return (0, 0)


class NeuralEngineSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self.tmp.name, "neural-config.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_enqueue_priority_sets_wake_event(self):
        engine = NeuralEngine(_DummyStorage(), self.config_path)
        engine._wake_event.clear()

        engine.enqueue_priority_reindex(42)

        self.assertTrue(engine._wake_event.is_set())

    def test_enqueue_new_clip_sets_wake_event(self):
        engine = NeuralEngine(_DummyStorage(), self.config_path)
        engine._wake_event.clear()

        engine.enqueue_new_clip(99)

        self.assertTrue(engine._wake_event.is_set())

    def test_wait_timeout_uses_flush_deadline_not_fixed_poll(self):
        engine = NeuralEngine(_DummyStorage(), self.config_path)
        engine.pending_flush_interval_seconds = 100
        engine._worker.state.flush_interval_seconds = 100
        engine._worker.enqueue_new_clip(1)
        engine._worker.state._pending_since = 1000

        timeout = engine._compute_wait_timeout(now=1050)

        self.assertEqual(50, timeout)


if __name__ == "__main__":
    unittest.main()

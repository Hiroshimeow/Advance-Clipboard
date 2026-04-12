import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))

from neural.batch_worker import QueueState


class QueueStateTests(unittest.TestCase):
    def test_new_clips_flush_when_batch_size_reached(self):
        state = QueueState(batch_size=4, flush_interval_seconds=14400)

        state.enqueue_new_clip(101)
        state.enqueue_new_clip(102)
        state.enqueue_new_clip(103)
        self.assertIsNone(state.pop_next_job(now=1000))

        state.enqueue_new_clip(104)
        self.assertEqual(("batch", [101, 102, 103, 104]), state.pop_next_job(now=1000))

    def test_priority_job_promotes_clip_out_of_new_queue(self):
        state = QueueState(batch_size=4, flush_interval_seconds=14400)

        state.enqueue_new_clip(10)
        state.enqueue_priority_reindex(10)

        self.assertEqual(("priority", [10]), state.pop_next_job(now=1000))
        self.assertIsNone(state.pop_next_job(now=1000))

    def test_timeout_flush_is_capped_to_batch_size(self):
        state = QueueState(batch_size=4, flush_interval_seconds=10)

        state.enqueue_new_clip(1)
        state.enqueue_new_clip(2)
        state.enqueue_new_clip(3)
        state.enqueue_new_clip(4)
        state.enqueue_new_clip(5)
        state.enqueue_new_clip(6)

        self.assertEqual(("batch", [1, 2, 3, 4]), state.pop_next_job(now=1000))
        self.assertIsNone(state.pop_next_job(now=1005))
        self.assertEqual(("batch", [5, 6]), state.pop_next_job(now=1011))


if __name__ == "__main__":
    unittest.main()

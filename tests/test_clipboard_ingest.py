import tempfile
import threading
import time
import unittest
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage

from core.clipboard_ingest import (
    CaptureJob,
    ClipboardIngestBridge,
    ClipboardIngestProcessor,
    MAX_IMAGE_PIXELS,
    MAX_TEXT_BYTES,
)


class FakeStorage:
    def __init__(self):
        self.added = []

    def add_clip(self, clip_type, content, tag=""):
        self.added.append((clip_type, content, tag))
        return len(self.added), True


class ClipboardIngestProcessorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.temp_dir = self.tmp.name

    def test_normal_text_is_added(self):
        storage = FakeStorage()
        processor = ClipboardIngestProcessor(storage, image_dir=self.temp_dir)
        result = processor.process(CaptureJob.text("hello"))
        self.assertTrue(result.accepted)
        self.assertEqual(("text", "hello", ""), storage.added[0])

    def test_rejects_text_over_two_mib_without_writing_storage(self):
        storage = FakeStorage()
        processor = ClipboardIngestProcessor(storage, image_dir=self.temp_dir)
        result = processor.process(CaptureJob.text("a" * (MAX_TEXT_BYTES + 1)))
        self.assertFalse(result.accepted)
        self.assertEqual("text_too_large", result.reason)
        self.assertEqual([], storage.added)
        self.assertEqual(MAX_TEXT_BYTES + 1, result.byte_count)

    def test_image_is_encoded_once_and_published_atomically(self):
        image = QImage(32, 32, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.red)
        storage = FakeStorage()
        processor = ClipboardIngestProcessor(storage, image_dir=self.temp_dir)
        result = processor.process(CaptureJob.image(image))
        self.assertTrue(result.accepted)
        self.assertTrue(result.content.endswith(".png"))
        self.assertTrue(
            (Path(self.temp_dir) / result.content).read_bytes().startswith(b"\x89PNG")
        )
        self.assertEqual([], list(Path(self.temp_dir).glob("*.tmp")))
        self.assertEqual(("image", result.content, ""), storage.added[0])

    def test_rejects_image_over_pixel_limit_before_encoding(self):
        image = QImage(7681, 4320, QImage.Format.Format_ARGB32)
        storage = FakeStorage()
        processor = ClipboardIngestProcessor(storage, image_dir=self.temp_dir)
        result = processor.process(CaptureJob.image(image))
        self.assertFalse(result.accepted)
        self.assertEqual("image_too_large", result.reason)
        self.assertGreater(result.pixel_count, MAX_IMAGE_PIXELS)
        self.assertEqual([], storage.added)

    def test_duplicate_image_filename_remains_valid_and_has_no_temp_file(self):
        image = QImage(16, 16, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.blue)
        storage = FakeStorage()
        processor = ClipboardIngestProcessor(storage, image_dir=self.temp_dir)
        first = processor.process(CaptureJob.image(image))
        second = processor.process(CaptureJob.image(image))
        self.assertTrue(first.accepted)
        self.assertTrue(second.accepted)
        self.assertEqual(first.content, second.content)
        self.assertEqual([], list(Path(self.temp_dir).glob("*.tmp")))

    def test_encode_failure_does_not_write_storage_or_leave_temp_file(self):
        image = QImage()
        storage = FakeStorage()
        processor = ClipboardIngestProcessor(storage, image_dir=self.temp_dir)
        result = processor.process(CaptureJob.image(image))
        self.assertFalse(result.accepted)
        self.assertEqual("image_encode_failed", result.reason)
        self.assertEqual([], storage.added)
        self.assertEqual([], list(Path(self.temp_dir).glob("*.tmp")))


class BlockingProcessor:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def process(self, job):
        self.started.set()
        self.release.wait(2)
        return type(
            "Result",
            (),
            {
                "accepted": True,
                "reason": "",
                "clip_type": job.clip_type,
                "content": job.content,
            },
        )()


class ClipboardIngestBridgeTests(unittest.TestCase):
    def test_queue_capacity_rejects_third_job_without_blocking(self):
        processor = BlockingProcessor()
        bridge = ClipboardIngestBridge(processor, queue_capacity=1)
        self.addCleanup(lambda: (processor.release.set(), bridge.stop()))
        rejected = []
        bridge.rejected.connect(rejected.append)

        self.assertTrue(bridge.submit(CaptureJob.text("first")))
        self.assertTrue(processor.started.wait(1))
        self.assertTrue(bridge.submit(CaptureJob.text("second")))
        started = time.perf_counter()
        self.assertFalse(bridge.submit(CaptureJob.text("third")))
        self.assertLess(time.perf_counter() - started, 0.1)
        self.assertEqual("queue_full", rejected[-1].reason)


if __name__ == "__main__":
    unittest.main()

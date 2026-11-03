from __future__ import annotations

import hashlib
import os
import queue
import threading
from dataclasses import dataclass

from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QObject, pyqtSignal
from PyQt6.QtGui import QImage

MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_IMAGE_PIXELS = 7680 * 4320
INGEST_QUEUE_CAPACITY = 16


@dataclass(frozen=True)
class CaptureJob:
    clip_type: str
    content: object

    @classmethod
    def text(cls, text: str) -> "CaptureJob":
        return cls("text", text)

    @classmethod
    def image(cls, image: QImage) -> "CaptureJob":
        return cls("image", image)


@dataclass(frozen=True)
class IngestResult:
    accepted: bool
    reason: str = ""
    clip_type: str = ""
    content: str = ""
    clip_id: int | None = None
    is_new: bool = False
    byte_count: int = 0
    pixel_count: int = 0


class ClipboardIngestProcessor:
    def __init__(self, storage, *, image_dir: str):
        self._storage = storage
        self._image_dir = image_dir

    def process(self, job: CaptureJob) -> IngestResult:
        if job.clip_type == "text":
            return self._process_text(str(job.content))
        if job.clip_type == "image":
            return self._process_image(job.content)
        return IngestResult(False, "unsupported_type", clip_type=job.clip_type)

    def _process_text(self, text: str) -> IngestResult:
        encoded = text.encode("utf-8")
        byte_count = len(encoded)
        if byte_count > MAX_TEXT_BYTES:
            return IngestResult(
                False,
                "text_too_large",
                clip_type="text",
                content=text,
                byte_count=byte_count,
            )
        try:
            clip_id, is_new = self._storage.add_clip("text", text)
        except Exception:
            return IngestResult(
                False,
                "storage_failed",
                clip_type="text",
                content=text,
                byte_count=byte_count,
            )
        return IngestResult(
            True,
            clip_type="text",
            content=text,
            clip_id=clip_id,
            is_new=is_new,
            byte_count=byte_count,
        )

    def _process_image(self, image: QImage) -> IngestResult:
        width = image.width()
        height = image.height()
        pixel_count = width * height
        if pixel_count > MAX_IMAGE_PIXELS:
            return IngestResult(
                False,
                "image_too_large",
                clip_type="image",
                pixel_count=pixel_count,
            )

        data = QByteArray()
        buffer = QBuffer(data)
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, "PNG"):
            return IngestResult(
                False,
                "image_encode_failed",
                clip_type="image",
                pixel_count=pixel_count,
            )
        png_bytes = bytes(data)
        filename = f"{hashlib.md5(png_bytes).hexdigest()}.png"
        os.makedirs(self._image_dir, exist_ok=True)
        final_path = os.path.join(self._image_dir, filename)
        temp_path = f"{final_path}.tmp"
        try:
            with open(temp_path, "wb") as handle:
                handle.write(png_bytes)
            os.replace(temp_path, final_path)
        except OSError:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            return IngestResult(
                False,
                "image_write_failed",
                clip_type="image",
                content=filename,
                byte_count=len(png_bytes),
                pixel_count=pixel_count,
            )

        try:
            clip_id, is_new = self._storage.add_clip("image", filename)
        except Exception:
            return IngestResult(
                False,
                "storage_failed",
                clip_type="image",
                content=filename,
                byte_count=len(png_bytes),
                pixel_count=pixel_count,
            )
        return IngestResult(
            True,
            clip_type="image",
            content=filename,
            clip_id=clip_id,
            is_new=is_new,
            byte_count=len(png_bytes),
            pixel_count=pixel_count,
        )


class ClipboardIngestBridge(QObject):
    completed = pyqtSignal(object)
    rejected = pyqtSignal(object)

    def __init__(self, processor, *, queue_capacity: int = INGEST_QUEUE_CAPACITY):
        super().__init__()
        self._processor = processor
        self._queue = queue.Queue(maxsize=queue_capacity)
        self._stopping = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="ClipboardIngestWorker",
        )
        self._thread.start()

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def submit(self, job: CaptureJob) -> bool:
        if self._stopping.is_set():
            self.rejected.emit(
                IngestResult(False, "worker_stopped", clip_type=job.clip_type)
            )
            return False
        try:
            self._queue.put_nowait(job)
            return True
        except queue.Full:
            self.rejected.emit(
                IngestResult(False, "queue_full", clip_type=job.clip_type)
            )
            return False

    def stop(self, timeout: float = 3.0) -> None:
        self._stopping.set()
        self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stopping.is_set() or not self._queue.empty():
            try:
                job = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                result = self._processor.process(job)
                if result.accepted:
                    self.completed.emit(result)
                else:
                    self.rejected.emit(result)
            finally:
                self._queue.task_done()

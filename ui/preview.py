from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

from PyQt6.QtCore import QObject, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QImageReader, QPixmap, QTextCursor
from PyQt6.QtWidgets import QLabel, QTextEdit, QVBoxLayout, QWidget

from .widgets import IMAGE_DIR

TEXT_INITIAL_CHARS = 32 * 1024
TEXT_APPEND_CHARS = 64 * 1024
SEARCH_PREVIEW_DEFER_MS = 1000
MAX_IMAGE_CACHE = 3
MAX_PREVIEW_WIDTH = 420


class PreviewWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(
            "QWidget { background:#181818; color:#eee; border:1px solid #3a3a3a; }"
            "QTextEdit { background:#181818; color:#eee; border:0; padding:10px; }"
            "QLabel { background:#181818; border:0; }"
        )


class PreviewController(QObject):
    rendered = pyqtSignal(dict)
    _imageReady = pyqtSignal(int, object, object)

    def __init__(self, main_window, image_dir=None):
        super().__init__(main_window)
        self.main_window = main_window
        self.image_dir = str(image_dir or IMAGE_DIR)
        self.enabled = False
        self.side = None
        self.current_clip_id = None
        self._current_key = None
        self._generation = 0
        self._pending_candidate = None
        self._search_deferred = False
        self._text_source = None
        self._text_offset = 0
        self._image_lock = threading.Lock()
        self._image_worker_running = False
        self._pending_image = None
        self._image_cache = OrderedDict()
        self._shutdown = False

        self.window = PreviewWindow(main_window)
        layout = QVBoxLayout(self.window)
        layout.setContentsMargins(0, 0, 0, 0)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setAcceptRichText(False)
        self.text_edit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.text_edit.verticalScrollBar().valueChanged.connect(self._maybe_append_text)
        layout.addWidget(self.image_label)
        layout.addWidget(self.text_edit)
        self.image_label.hide()
        self.text_edit.hide()

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._finish_search_defer)
        self._imageReady.connect(self._apply_image_result, Qt.ConnectionType.QueuedConnection)

    def activate(self, clip, *, screen=None):
        if not clip or "content" not in clip:
            return
        if not self.enabled:
            self.enabled = True
            self._freeze_geometry(screen or self.main_window.screen())
            self.window.show()
            self.window.raise_()
        self.request_preview(clip, force=True)

    def _freeze_geometry(self, screen):
        available = screen.availableGeometry()
        main = self.main_window.geometry()
        left_space = max(0, main.left() - available.left())
        right_space = max(0, available.right() - main.right())
        self.side = "left" if left_space >= right_space else "right"
        free = left_space if self.side == "left" else right_space
        width = max(1, min(MAX_PREVIEW_WIDTH, free))
        height = min(main.height(), available.height())
        y = max(available.top(), min(main.top(), available.bottom() - height + 1))
        if self.side == "left":
            x = main.left() - width
        else:
            x = main.right() + 1
        self.window.setGeometry(x, y, width, height)

    def reset(self):
        self.enabled = False
        self.side = None
        self.current_clip_id = None
        self._current_key = None
        self._pending_candidate = None
        self._search_deferred = False
        self._search_timer.stop()
        self._generation += 1
        self._clear_text()
        self.image_label.clear()
        self.image_label.hide()
        self.window.hide()
        with self._image_lock:
            self._pending_image = None

    def shutdown(self):
        self._shutdown = True
        self.reset()
        self.window.close()

    def begin_search_defer(self):
        if not self.enabled:
            return
        self._pending_candidate = None
        self._search_deferred = True
        self._search_timer.start(SEARCH_PREVIEW_DEFER_MS)

    def request_preview(self, clip, *, force=False):
        if not self.enabled or not clip or "content" not in clip:
            return
        key = (clip.get("id"), clip.get("type"), clip.get("content"))
        if not force and key == self._current_key:
            return
        if self._search_deferred:
            self._pending_candidate = clip
            return
        self._render_candidate(clip, key)

    def _finish_search_defer(self):
        self._search_deferred = False
        clip = self._pending_candidate
        self._pending_candidate = None
        if clip:
            self.request_preview(clip)

    def _render_candidate(self, clip, key=None):
        self._generation += 1
        generation = self._generation
        self._current_key = key or (clip.get("id"), clip.get("type"), clip.get("content"))
        self.current_clip_id = clip.get("id")
        if clip.get("type") == "image":
            self._show_image(clip, generation)
        else:
            self._show_text(clip)
        self.rendered.emit(clip)

    def _clear_text(self):
        self._text_source = None
        self._text_offset = 0
        self.text_edit.clear()
        self.text_edit.verticalScrollBar().setValue(0)
        self.text_edit.hide()

    def _show_text(self, clip):
        self.image_label.clear()
        self.image_label.hide()
        self._text_source = clip.get("content") or ""
        self._text_offset = min(len(self._text_source), TEXT_INITIAL_CHARS)
        self.text_edit.setPlainText(self._text_source[: self._text_offset])
        self.text_edit.verticalScrollBar().setValue(0)
        self.text_edit.show()

    def append_text_chunk(self):
        source = self._text_source
        if source is None or self._text_offset >= len(source):
            return False
        end = min(len(source), self._text_offset + TEXT_APPEND_CHARS)
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(source[self._text_offset:end])
        self._text_offset = end
        return True

    def _maybe_append_text(self, value):
        bar = self.text_edit.verticalScrollBar()
        if bar.maximum() and value >= bar.maximum() - max(20, bar.pageStep() // 3):
            self.append_text_chunk()

    def _show_image(self, clip, generation):
        self._clear_text()
        self.image_label.show()
        content = str(clip.get("content") or "")
        path = str(Path(self.image_dir, content).resolve())
        target = QSize(max(1, self.window.width() - 8), max(1, self.window.height() - 8))
        cache_key = (path, target.width(), target.height())
        cached = self._image_cache.get(cache_key)
        if cached is not None:
            self._image_cache.move_to_end(cache_key)
            self._set_pixmap(cached)
            return
        request = (generation, path, target, cache_key)
        with self._image_lock:
            if self._image_worker_running:
                self._pending_image = request
                return
            self._image_worker_running = True
        threading.Thread(target=self._decode_image_loop, args=(request,), daemon=True).start()

    def _decode_image_loop(self, request):
        current = request
        while current and not self._shutdown:
            generation, path, target, cache_key = current
            image = QImage()
            try:
                reader = QImageReader(path)
                original = reader.size()
                if original.isValid():
                    scaled = original.scaled(target, Qt.AspectRatioMode.KeepAspectRatio)
                    reader.setScaledSize(scaled)
                image = reader.read()
            except Exception:
                image = QImage()
            try:
                self._imageReady.emit(generation, cache_key, image)
            except RuntimeError:
                return
            with self._image_lock:
                current = self._pending_image
                self._pending_image = None
                if current is None:
                    self._image_worker_running = False

    def _apply_image_result(self, generation, cache_key, image):
        if image is None or image.isNull():
            if generation == self._generation:
                self.image_label.clear()
            return
        self._image_cache[cache_key] = image
        self._image_cache.move_to_end(cache_key)
        while len(self._image_cache) > MAX_IMAGE_CACHE:
            self._image_cache.popitem(last=False)
        if generation != self._generation:
            return
        self._set_pixmap(image)

    def _set_pixmap(self, image):
        pixmap = QPixmap.fromImage(image)
        if not pixmap.isNull():
            self.image_label.setPixmap(pixmap)

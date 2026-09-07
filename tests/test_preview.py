import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt6.QtCore import QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget

from ui.clip_models import ClipRow

_APP = None


def app():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def wait_until(fn, timeout=1.5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if fn():
            return True
        time.sleep(0.01)
    return fn()


class FakeScreen:
    def __init__(self, rect):
        self._rect = rect
    def availableGeometry(self):
        return QRect(self._rect)


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app()

    def setUp(self):
        from ui.preview import PreviewController
        self.main = QWidget()
        self.main.setGeometry(500, 100, 400, 300)
        self.main.show()
        QApplication.processEvents()
        self.preview = PreviewController(self.main)

    def tearDown(self):
        self.preview.shutdown()
        self.main.close()
        QApplication.processEvents()

    def test_activation_freezes_side_geometry_and_reset_turns_mode_off(self):
        screen = FakeScreen(QRect(0, 0, 1200, 700))
        self.preview.activate({"id": 1, "type": "text", "content": "hello"}, screen=screen)
        self.assertTrue(self.preview.enabled)
        self.assertEqual(self.preview.side, "left")
        geom = self.preview.window.geometry()
        self.assertEqual(geom.height(), self.main.height())
        self.assertLessEqual(geom.right(), self.main.geometry().left())
        frozen = QRect(geom)
        self.preview.request_preview({"id": 2, "type": "text", "content": "world"})
        self.assertEqual(self.preview.window.geometry(), frozen)
        self.preview.reset()
        self.assertFalse(self.preview.enabled)
        self.assertFalse(self.preview.window.isVisible())

    def test_text_is_chunked_and_appends_near_bottom(self):
        text = "x" * (32 * 1024 + 80 * 1024)
        self.preview.activate({"id": 1, "type": "text", "content": text}, screen=FakeScreen(QRect(0, 0, 1200, 700)))
        rendered = self.preview.text_edit.toPlainText()
        self.assertLess(len(rendered), len(text))
        self.assertLessEqual(len(rendered), 32 * 1024)
        self.preview.append_text_chunk()
        rendered2 = self.preview.text_edit.toPlainText()
        self.assertGreater(len(rendered2), len(rendered))
        self.assertLessEqual(len(rendered2), 96 * 1024)
        self.assertIs(self.preview._text_source, text)

    def test_search_defer_coalesces_latest_candidate(self):
        seen = []
        self.preview.rendered.connect(lambda clip: seen.append(clip["id"]))
        self.preview.activate({"id": 1, "type": "text", "content": "one"}, screen=FakeScreen(QRect(0, 0, 1200, 700)))
        seen.clear()
        self.preview.begin_search_defer()
        self.preview.request_preview({"id": 2, "type": "text", "content": "two"})
        self.preview.request_preview({"id": 3, "type": "text", "content": "three"})
        QTest.qWait(300)
        QApplication.processEvents()
        self.assertEqual(seen, [])
        QTest.qWait(800)
        QApplication.processEvents()
        self.assertEqual(seen, [3])

    def test_new_search_epoch_drops_candidate_from_prior_epoch(self):
        seen = []
        self.preview.rendered.connect(lambda clip: seen.append(clip["id"]))
        self.preview.activate({"id": 1, "type": "text", "content": "one"}, screen=FakeScreen(QRect(0, 0, 1200, 700)))
        seen.clear()
        self.preview.begin_search_defer()
        self.preview.request_preview({"id": 2, "type": "text", "content": "old-result"})
        self.preview.begin_search_defer()
        QTest.qWait(1100)
        QApplication.processEvents()
        self.assertEqual(seen, [])

    def test_duplicate_candidate_is_deduped(self):
        seen = []
        self.preview.rendered.connect(lambda clip: seen.append(clip["id"]))
        clip = {"id": 4, "type": "text", "content": "same"}
        self.preview.activate(clip, screen=FakeScreen(QRect(0, 0, 1200, 700)))
        seen.clear()
        self.preview.request_preview(clip)
        self.preview.request_preview(clip)
        self.assertEqual(seen, [])

    def test_image_decode_is_scaled_async_and_stale_result_cannot_win(self):
        with TemporaryDirectory() as td:
            p1 = Path(td, "a.png")
            p2 = Path(td, "b.png")
            image = QImage(800, 600, QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.white)
            image.save(str(p1)); image.save(str(p2))
            self.preview.image_dir = td
            self.preview.activate({"id": 1, "type": "image", "content": "a.png"}, screen=FakeScreen(QRect(0, 0, 1200, 700)))
            self.preview.request_preview({"id": 2, "type": "image", "content": "b.png"})
            self.assertTrue(wait_until(lambda: self.preview.current_clip_id == 2 and not self.preview.image_label.pixmap().isNull()))
            self.assertEqual(self.preview.current_clip_id, 2)
            pix = self.preview.image_label.pixmap()
            self.assertLessEqual(pix.width(), self.preview.window.width())
            self.assertLessEqual(pix.height(), self.preview.window.height())

    def test_preview_window_does_not_accept_focus(self):
        self.preview.activate({"id": 1, "type": "text", "content": "focus"}, screen=FakeScreen(QRect(0, 0, 1200, 700)))
        self.assertTrue(self.preview.window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        self.assertTrue(bool(self.preview.window.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus))


class ContextActionTests(unittest.TestCase):
    def test_preview_action_is_all_clips_and_image_actions_are_image_only(self):
        from ui.clip_context_menu import CONTEXT_ACTIONS
        image = {"id": 1, "type": "image", "content": "shot.png"}
        text = {"id": 2, "type": "text", "content": "hello"}
        image_labels = [label for label, applies, _ in CONTEXT_ACTIONS if applies(image)]
        text_labels = [label for label, applies, _ in CONTEXT_ACTIONS if applies(text)]
        self.assertIn("Show preview", image_labels)
        self.assertIn("Show preview", text_labels)
        for label in ("Open this image", "Copy image path", "Open with Paint"):
            self.assertIn(label, image_labels)
            self.assertNotIn(label, text_labels)

    def test_copy_image_path_uses_resolved_absolute_path_even_if_missing(self):
        from ui.clip_context_menu import _copy_image_path
        handler = MagicMock()
        with TemporaryDirectory() as td, patch("ui.widgets.IMAGE_DIR", td):
            _copy_image_path({"type": "image", "content": "missing.png"}, handler)
            handler.handle_copy_image_path.assert_called_once_with(str(Path(td, "missing.png").resolve()))

    def test_open_with_paint_is_non_shell_and_missing_file_is_safe(self):
        from ui.clip_context_menu import _open_with_paint
        with TemporaryDirectory() as td, patch("ui.widgets.IMAGE_DIR", td), patch("ui.clip_context_menu.subprocess.Popen") as popen:
            path = Path(td, "shot.png")
            path.write_bytes(b"image")
            _open_with_paint({"type": "image", "content": "shot.png"})
            popen.assert_called_once_with(["mspaint.exe", str(path.resolve())], shell=False)
            popen.reset_mock()
            _open_with_paint({"type": "image", "content": "missing.png"})
            popen.assert_not_called()


class ClipListPreviewSignalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app()

    def test_keyboard_selection_then_mouse_move_back_to_prior_hover_emits_again(self):
        from PyQt6.QtGui import QMouseEvent
        from ui.clip_list_view import ClipListView
        view = ClipListView()
        view.resize(400, 300)
        view.set_rows([
            ClipRow(row_kind="clip", clip={"id": 1, "type": "text", "content": "A"}),
            ClipRow(row_kind="clip", clip={"id": 2, "type": "text", "content": "B"}),
        ])
        view.show(); QApplication.processEvents()
        seen = []
        view.previewCandidate.connect(lambda clip: seen.append(clip["id"]))
        pos = view.visualRect(view.model().index(0, 0)).center()
        event = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(pos), QPointF(pos), Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(view.viewport(), event)
        view.setCurrentRow(1)
        QApplication.sendEvent(view.viewport(), event)
        self.assertEqual(seen[-3:], [1, 2, 1])
        view.close()

    def test_real_row_mouse_move_emits_once_and_group_does_not_emit(self):
        from PyQt6.QtGui import QMouseEvent
        from ui.clip_list_view import ClipListView
        view = ClipListView()
        view.resize(400, 300)
        view.set_rows([
            ClipRow(row_kind="clip", clip={"id": 1, "type": "text", "content": "A"}),
            ClipRow(row_kind="group_header", group_name="G", group_count=1),
        ])
        view.show(); QApplication.processEvents()
        seen = []
        view.previewCandidate.connect(lambda clip: seen.append(clip["id"]))
        rect = view.visualRect(view.model().index(0, 0))
        pos = rect.center()
        event = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(pos), QPointF(pos), Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(view.viewport(), event)
        QApplication.sendEvent(view.viewport(), event)
        self.assertEqual(seen, [1])
        view.close()


if __name__ == "__main__":
    unittest.main()

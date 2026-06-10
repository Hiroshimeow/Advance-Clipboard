import os
import sys
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from ui.widgets import COLLAPSED_MAX_LINES, ClipItemWidget, TEXT_FONT, _visible_text_height

_APP: QApplication | None = None


def _get_qapp() -> QApplication:
    global _APP
    if _APP is not None:
        return _APP

    inst = QApplication.instance()
    if isinstance(inst, QApplication):
        _APP = inst
        return _APP

    _APP = QApplication([])
    _APP.setQuitOnLastWindowClosed(False)
    return _APP


class WidgetLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_qapp()

    def test_single_line_text_gets_full_height(self):
        lines, height = _visible_text_height("single line", TEXT_FONT, 220, 4)
        self.assertGreaterEqual(lines, 1)
        self.assertLessEqual(lines, 2)
        self.assertGreaterEqual(height, 20)

    def test_four_line_text_keeps_last_line_visible(self):
        item = {
            "id": 1,
            "type": "text",
            "content": "line1\nline2\nline3\nline4",
            "tag": "",
            "group_name": "",
        }
        widget = ClipItemWidget(item, is_pinned=False, parent_list=None, available_width=260)
        line_height = widget.lbl_content.fontMetrics().height()
        self.assertGreaterEqual(
            widget.lbl_content.height(),
            (line_height * COLLAPSED_MAX_LINES) - 2,
        )
        self.assertEqual(COLLAPSED_MAX_LINES, 3)

    def test_single_line_row_is_tall_enough_for_button_columns(self):
        item = {
            "id": 2,
            "type": "text",
            "content": "short",
            "tag": "",
            "group_name": "",
        }
        widget = ClipItemWidget(item, is_pinned=False, parent_list=None, available_width=260)
        self.assertGreaterEqual(widget.height(), widget.btn_container.minimumHeight() + 10)
        self.assertGreaterEqual(widget.height(), widget.btn_v_widget.minimumHeight() + 10)

    def test_actions_stay_in_right_side_column(self):
        item = {
            "id": 3,
            "type": "text",
            "content": "This is a longer clip body that should keep readable width while tools stay compact on the right.",
            "tag": "demo",
            "group_name": "",
        }
        widget = ClipItemWidget(item, is_pinned=False, parent_list=None, available_width=320)
        widget.show()
        _get_qapp().processEvents()

        self.assertLess(widget.btn_container.width(), widget.lbl_content.width())
        self.assertGreater(widget.btn_container.x(), widget.content_container.x())
        self.assertGreaterEqual(widget.btn_container.x(), widget.content_container.width() - 4)
        self.assertLess(widget.btn_container.width(), 60)

    def test_actions_available_for_legacy_history_clip_data(self):
        item = {
            "id": 4,
            "type": "text",
            "content": "legacy clip body",
            "hash": "abc123",
            "created_at": "2025-01-01T00:00:00",
            "updated_at": "2025-01-01T00:00:00",
        }
        widget = ClipItemWidget(item, is_pinned=False, parent_list=None, available_width=320)

        buttons = widget.btn_container.findChildren(type(widget.btn_star))
        self.assertEqual(len(buttons), 3)
        self.assertTrue(any(btn.toolTip() == "Copy" for btn in buttons))
        self.assertTrue(any(btn.toolTip() == "Pin/Unpin" for btn in buttons))
        self.assertTrue(any(btn.toolTip() == "Delete" for btn in buttons))


if __name__ == "__main__":
    unittest.main()

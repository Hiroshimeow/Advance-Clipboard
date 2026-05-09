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
        self.assertEqual(lines, 1)
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


if __name__ == "__main__":
    unittest.main()

import os
import sys
import unittest
from types import SimpleNamespace

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QSize, QEvent, QPoint, QPointF
from PyQt6.QtGui import QContextMenuEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QLabel, QListWidgetItem, QMenu, QPushButton, QWidget

from ui.clipboard_browser_controller import ClipboardBrowserController
from ui.clip_delegate import ClipRowDelegate
from ui.clip_list_view import ClipListView
from ui.clip_models import ClipRow, HistoryListModel, ROW_ROLE
from ui.widgets import (
    COLLAPSED_MAX_LINES,
    ClipItemWidget,
    SmoothListWidget,
    TEXT_FONT,
    _visible_text_height,
)

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


class _BrowserHarness(QWidget):
    def __init__(self):
        super().__init__()
        self.storage = SimpleNamespace()
        self.list_history = ClipListView(HistoryListModel(self))
        self.list_pinned = ClipListView(HistoryListModel(self))
        self.search_input = SimpleNamespace(setFocus=lambda: None, text=lambda: "")
        self.sidecar = None
        self._updates_enabled = True
        self.browser = ClipboardBrowserController(self)

    def _do_search(self):
        return None

    def setUpdatesEnabled(self, enabled):
        self._updates_enabled = enabled

    def width(self):
        return 900


class _SearchProbeStorage:
    def __init__(self):
        self.calls = []

    def search_history(self, query, limit=None, semantic=True):
        self.calls.append(("history", query, limit, semantic))
        return []

    def search_pinned(self, query, limit=None, semantic=True):
        self.calls.append(("pinned", query, limit, semantic))
        return []


class _WheelEventStub:
    def __init__(self, *, pixel_y=0, angle_y=0):
        self._pixel_delta = QPoint(0, pixel_y)
        self._angle_delta = QPoint(0, angle_y)
        self.accepted = False
        self.ignored = False

    def pixelDelta(self):
        return self._pixel_delta

    def angleDelta(self):
        return self._angle_delta

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.ignored = True


class WidgetLayoutTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _get_qapp()

    def tearDown(self):
        app = _get_qapp()
        for widget in app.topLevelWidgets():
            widget.close()
        app.processEvents()

    def test_single_line_text_gets_full_height(self):
        lines, height = _visible_text_height("single line", TEXT_FONT, 220, 4)
        self.assertGreaterEqual(lines, 1)
        self.assertLessEqual(lines, 2)
        self.assertGreaterEqual(height, 20)

    def test_smooth_list_uses_touchpad_pixel_delta_direction(self):
        list_widget = SmoothListWidget()
        list_widget.verticalScrollBar().setRange(0, 100)
        list_widget.verticalScrollBar().setValue(50)

        up_event = _WheelEventStub(pixel_y=12)
        list_widget.wheelEvent(up_event)
        self.assertTrue(up_event.accepted)
        self.assertEqual(list_widget.verticalScrollBar().value(), 38)

        down_event = _WheelEventStub(pixel_y=-20)
        list_widget.wheelEvent(down_event)
        self.assertTrue(down_event.accepted)
        self.assertEqual(list_widget.verticalScrollBar().value(), 58)

    def test_smooth_list_large_touchpad_delta_can_reach_bottom(self):
        list_widget = SmoothListWidget()
        list_widget.verticalScrollBar().setRange(0, 300)

        list_widget.wheelEvent(_WheelEventStub(pixel_y=-10_000))

        self.assertEqual(
            list_widget.verticalScrollBar().value(),
            list_widget.verticalScrollBar().maximum(),
        )

    def test_smooth_list_tiny_angle_deltas_accumulate_without_forced_jump(self):
        list_widget = SmoothListWidget()
        list_widget.verticalScrollBar().setRange(0, 300)
        list_widget.verticalScrollBar().setValue(150)

        list_widget.wheelEvent(_WheelEventStub(angle_y=1))
        self.assertEqual(list_widget.verticalScrollBar().value(), 150)

        for _ in range(119):
            list_widget.wheelEvent(_WheelEventStub(angle_y=1))

        self.assertLess(list_widget.verticalScrollBar().value(), 150)

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
            (line_height * 4) + 2,
        )
        self.assertGreaterEqual(COLLAPSED_MAX_LINES, 4)

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
        self.assertLessEqual(widget.btn_v_widget.width(), 36)

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

    def test_action_buttons_are_visually_flush_right(self):
        item = {
            "id": 30,
            "type": "text",
            "content": "Tools should sit tightly at the right edge of the row.",
            "tag": "demo",
            "group_name": "",
        }
        widget = ClipItemWidget(item, is_pinned=False, parent_list=None, available_width=340)
        widget.show()
        _get_qapp().processEvents()

        buttons = widget.btn_container.findChildren(QPushButton)
        self.assertTrue(buttons)
        visual_right = max(
            widget.btn_container.x() + button.x() + button.width()
            for button in buttons
        )
        self.assertLessEqual(widget.width() - visual_right, 3)

    def test_line_count_lives_in_meta_column_not_content_overlay(self):
        item = {
            "id": 32,
            "type": "text",
            "content": "line1\nline2\nline3",
            "tag": "demo",
            "group_name": "",
        }
        widget = ClipItemWidget(item, is_pinned=False, parent_list=None, available_width=340)
        widget.show()
        _get_qapp().processEvents()

        self.assertTrue(hasattr(widget, "lbl_line_count"))
        self.assertEqual(widget.lbl_line_count.parent(), widget.btn_v_widget)
        self.assertEqual(widget.lbl_line_count.text(), "3")
        self.assertLessEqual(widget.btn_v_widget.width(), 36)

    def test_tag_lives_below_clip_content_not_in_line_count_meta_column(self):
        item = {
            "id": 38,
            "type": "text",
            "content": "clip body",
            "tag": "long-tag-that-must-not-be-cropped-in-the-line-count-column",
            "group_name": "",
        }
        widget = ClipItemWidget(item, is_pinned=False, parent_list=None, available_width=360)
        widget.show()
        _get_qapp().processEvents()

        self.assertTrue(hasattr(widget, "lbl_tag"))
        self.assertEqual(widget.lbl_tag.parent(), widget.content_container)
        self.assertNotEqual(widget.lbl_tag.parent(), widget.btn_v_widget)
        self.assertLess(widget.lbl_content.y(), widget.lbl_tag.y())
        self.assertGreaterEqual(widget.lbl_tag.width(), widget.lbl_content.width() - 4)
        self.assertGreaterEqual(
            widget.lbl_tag.x() + widget.lbl_tag.width(),
            widget.content_container.width() - 4,
        )
        self.assertGreaterEqual(
            widget.content_container.height(),
            widget.lbl_tag.y() + widget.lbl_tag.height(),
        )

    def test_right_click_does_not_trigger_row_click_action(self):
        view = ClipListView(HistoryListModel())
        view.resize(360, 240)
        view.set_rows([
            ClipRow(
                row_kind="clip",
                clip={"id": 41, "type": "text", "content": "right click me", "tag": ""},
            )
        ])
        view.show()
        _get_qapp().processEvents()

        calls = []
        view._emit_action = lambda index, action: calls.append(action)
        index = view.model().index(0, 0)
        pos = view.visualRect(index).center()
        if pos.isNull():
            pos = QPoint(10, 10)
        global_pos = view.viewport().mapToGlobal(pos)

        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(pos),
            QPointF(global_pos),
            Qt.MouseButton.RightButton,
            Qt.MouseButton.RightButton,
            Qt.KeyboardModifier.NoModifier,
        )
        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(pos),
            QPointF(global_pos),
            Qt.MouseButton.RightButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )

        view.mousePressEvent(press)
        view.mouseReleaseEvent(release)

        self.assertEqual(calls, [])
        self.assertFalse(view._pressed_index.isValid())
        self.assertIsNone(view._pressed_action)

    def test_context_menu_exposes_tag_and_group_actions(self):
        class _Storage:
            def get_groups(self):
                return ["Work"]

        parent = QWidget()
        parent.storage = _Storage()
        parent.handle_add_tag = lambda clip_id, tag: None
        parent.handle_set_group = lambda clip_id, group: None
        parent.handle_fix_clip = lambda clip_id, content: None
        view = ClipListView(HistoryListModel(), parent)
        view.resize(360, 240)
        view.set_rows([
            ClipRow(
                row_kind="clip",
                clip={"id": 42, "type": "text", "content": "menu me", "tag": ""},
            )
        ])
        parent.show()
        view.show()
        _get_qapp().processEvents()

        seen = []
        original_exec = QMenu.exec

        def fake_exec(menu, global_pos):
            for action in menu.actions():
                seen.append(action.text())
                submenu = action.menu()
                if submenu is not None:
                    seen.extend(sub_action.text() for sub_action in submenu.actions())
            return None

        index = view.model().index(0, 0)
        pos = view.visualRect(index).center()
        if pos.isNull():
            pos = QPoint(10, 10)
        event = QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse,
            pos,
            view.viewport().mapToGlobal(pos),
        )

        QMenu.exec = fake_exec
        try:
            view.contextMenuEvent(event)
        finally:
            QMenu.exec = original_exec

        self.assertIn("Add to Group", seen)
        self.assertIn("Work", seen)
        self.assertIn("Add Tag", seen)

    def test_initial_refresh_gives_every_history_row_action_hitboxes(self):
        harness = _BrowserHarness()
        clips = [
            {"id": idx, "type": "text", "content": f"clip {idx}", "tag": ""}
            for idx in range(1, 4)
        ]
        harness.storage = SimpleNamespace(
            get_history=lambda limit=20, offset=0: clips[offset : offset + limit],
            get_groups=lambda: [],
            get_ungrouped_pinned=lambda limit=50, offset=0: [],
        )
        harness.browser.refresh_lists(maintain_selection=False)

        self.assertEqual(harness.list_history.count(), 3)
        for row in range(harness.list_history.count()):
            item = harness.list_history.item(row)
            row_data = item.data(ROW_ROLE)
            self.assertTrue(row_data.is_clip)
            index = harness.list_history.model().index(row, 0)
            option = harness.list_history.viewOptions()
            option.rect = harness.list_history.visualRect(index)
            if option.rect.isNull():
                option.rect = harness.list_history.rect().adjusted(0, row * 80, 0, row * 80 + 80)
            rects = harness.list_history.itemDelegate().rects_for(option, row_data)
            self.assertFalse(rects.copy.isNull())
            self.assertFalse(rects.pin.isNull())
            self.assertFalse(rects.delete.isNull())

    def test_mixed_cjk_text_allocates_full_visible_text_height(self):
        item = {
            "id": 35,
            "type": "text",
            "content": (
                "da co nhieu su thay doi, o legend:\n"
                "書類        No        評価基準の書類.\n"
                "評価基準の内容\n"
                "共有        A        A. Function"
            ),
            "tag": "",
            "group_name": "",
        }
        widget = ClipItemWidget(item, is_pinned=False, parent_list=None, available_width=430)
        widget.show()
        _get_qapp().processEvents()

        expected_lines = min(COLLAPSED_MAX_LINES, len(item["content"].splitlines()))
        required_text_height = (
            widget.lbl_content.fontMetrics().lineSpacing() * expected_lines
        )
        self.assertGreaterEqual(widget.lbl_content.height(), required_text_height)
        self.assertGreaterEqual(
            widget.content_container.height(),
            widget.lbl_content.y() + widget.lbl_content.height(),
        )

    def test_content_meta_and_actions_do_not_overlap(self):
        item = {
            "id": 36,
            "type": "text",
            "content": "row columns should stay separate",
            "tag": "demo",
            "group_name": "",
        }
        widget = ClipItemWidget(item, is_pinned=False, parent_list=None, available_width=340)
        widget.show()
        _get_qapp().processEvents()

        content_right = widget.content_container.x() + widget.content_container.width()
        meta_right = widget.btn_v_widget.x() + widget.btn_v_widget.width()
        self.assertLessEqual(content_right, widget.btn_v_widget.x())
        self.assertLessEqual(meta_right, widget.btn_container.x())

    def test_hover_state_does_not_rewrite_child_widget_stylesheets(self):
        list_widget = SmoothListWidget()
        item = QListWidgetItem(list_widget)
        clip = {
            "id": 37,
            "type": "text",
            "content": "hover state should be row-frame state only",
            "tag": "",
            "group_name": "",
        }
        widget = ClipItemWidget(clip, is_pinned=False, parent_list=None, available_width=320)
        item.setData(Qt.ItemDataRole.UserRole, clip)
        item.setSizeHint(QSize(320, widget.height()))
        list_widget.addItem(item)
        list_widget.setItemWidget(item, widget)

        before = [child.styleSheet() for child in widget.findChildren(QLabel)]
        list_widget._set_item_border(item, SmoothListWidget.HOVER_BORDER_COLOR)
        after = [child.styleSheet() for child in widget.findChildren(QLabel)]

        self.assertEqual(before, after)
        self.assertTrue(getattr(widget, "hovered", False))

    def test_multiline_text_stays_top_aligned_without_vertical_clipping(self):
        item = {
            "id": 33,
            "type": "text",
            "content": "alpha\nbeta\ngamma",
            "tag": "",
            "group_name": "",
        }
        widget = ClipItemWidget(item, is_pinned=False, parent_list=None, available_width=320)
        widget.show()
        _get_qapp().processEvents()

        self.assertEqual(
            widget.lbl_content.alignment(),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
        )
        self.assertLessEqual(widget.lbl_content.y(), 1)
        self.assertGreaterEqual(
            widget.lbl_content.height(),
            (widget.lbl_content.fontMetrics().lineSpacing() * 3) + 2,
        )

    def test_image_thumbnail_alignment_is_consistent_between_history_and_pinned(self):
        clip = {"id": 31, "type": "image", "content": "missing-test-image.png", "tag": "", "group_name": "demo"}
        history = ClipItemWidget(clip, is_pinned=False, parent_list=None, available_width=340)
        pinned = ClipItemWidget(clip, is_pinned=True, parent_list=None, available_width=340)
        grouped = ClipItemWidget(clip, is_pinned=True, parent_list=None, is_grouped=True, available_width=340)

        for widget in (history, pinned, grouped):
            widget.show()
        _get_qapp().processEvents()

        history_x = history.content_container.x() + history.lbl_content.x()
        pinned_x = pinned.content_container.x() + pinned.lbl_content.x()
        grouped_x = grouped.content_container.x() + grouped.lbl_content.x()

        self.assertEqual(history_x, pinned_x)
        self.assertEqual(grouped_x, history_x + 14)

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

    def test_resize_reflows_history_delegate_width(self):
        harness = _BrowserHarness()
        harness.list_history.resize(420, 320)
        harness.list_history.show()
        _get_qapp().processEvents()

        clip = {
            "id": 10,
            "type": "text",
            "content": "This is a long clipboard entry that should wrap into more lines after the viewport gets narrower and therefore needs a taller row height to avoid clipping.",
            "tag": "demo",
            "group_name": "",
        }
        harness.list_history.set_rows(harness.browser.build_history_rows([clip]))
        _get_qapp().processEvents()

        delegate = harness.list_history.itemDelegate()
        index = harness.list_history.model().index(0, 0)
        option = harness.list_history.viewOptions()
        option.rect = harness.list_history.rect()
        initial_hint_width = delegate.sizeHint(option, index).width()

        harness.list_history.resize(280, 320)
        _get_qapp().processEvents()
        harness.browser._refresh_visible_row_layouts()
        _get_qapp().processEvents()

        option.rect = harness.list_history.rect()
        resized_hint_width = delegate.sizeHint(option, index).width()
        self.assertLess(resized_hint_width, initial_hint_width)

    def test_action_column_is_flush_to_list_viewport_not_just_row_widget(self):
        harness = _BrowserHarness()
        harness.list_history.resize(360, 320)
        harness.list_history.show()
        _get_qapp().processEvents()

        clip = {
            "id": 34,
            "type": "text",
            "content": "viewport edge verification for action column",
            "tag": "demo",
            "group_name": "",
        }
        harness.list_history.set_rows(harness.browser.build_history_rows([clip]))
        _get_qapp().processEvents()

        index = harness.list_history.model().index(0, 0)
        row_data = index.data(ROW_ROLE)
        option = harness.list_history.viewOptions()
        option.rect = harness.list_history.rect()
        rects = harness.list_history.itemDelegate().rects_for(option, row_data)
        visual_right = rects.actions.right()
        viewport_right = harness.list_history.viewport().width()
        self.assertLessEqual(viewport_right - visual_right, 12)

    def test_hotkey_open_skips_viewport_relayout_during_opening_cooldown(self):
        harness = _BrowserHarness()
        harness.list_history.resize(360, 320)
        harness.list_pinned.resize(360, 320)
        harness._ui_opening_until = 10**9
        scheduled = []

        original_start = harness.browser._layout_refresh_timer.start

        def tracking_start(ms):
            scheduled.append(ms)
            return original_start(ms)

        harness.browser._layout_refresh_timer.start = tracking_start
        try:
            resize_event = QEvent(QEvent.Type.Resize)
            harness.browser.handle_viewport_event(harness.list_history.viewport(), resize_event)
            harness.browser.handle_viewport_event(harness.list_pinned.viewport(), resize_event)
        finally:
            harness.browser._layout_refresh_timer.start = original_start

        self.assertEqual(scheduled, [])

    def test_active_side_marks_list_state_without_mutating_stylesheets(self):
        harness = _BrowserHarness()
        history_style = harness.list_history.styleSheet()
        pinned_style = harness.list_pinned.styleSheet()

        harness.browser.set_active_side("history")

        self.assertTrue(harness.list_history.property("activeSide"))
        self.assertFalse(harness.list_pinned.property("activeSide"))
        self.assertEqual(harness.list_history.styleSheet(), history_style)
        self.assertEqual(harness.list_pinned.styleSheet(), pinned_style)

        harness.browser.set_active_side("pinned")

        self.assertFalse(harness.list_history.property("activeSide"))
        self.assertTrue(harness.list_pinned.property("activeSide"))
        self.assertEqual(harness.list_history.styleSheet(), history_style)
        self.assertEqual(harness.list_pinned.styleSheet(), pinned_style)

    def test_search_uses_hybrid_storage_mode(self):
        harness = _BrowserHarness()
        storage = _SearchProbeStorage()
        harness.storage = storage
        harness.browser.current_search_query = "linux proxy"

        harness.browser.refresh_lists(maintain_selection=False)

        self.assertEqual(
            storage.calls,
            [("history", "linux proxy", 12, True), ("pinned", "linux proxy", 12, True)],
        )


if __name__ == "__main__":
    unittest.main()

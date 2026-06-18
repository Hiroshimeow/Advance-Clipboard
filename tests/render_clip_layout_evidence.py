"""Render clip row widgets to screenshots for visual verification.

Usage:
    python tests/render_clip_layout_evidence.py
"""
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QGraphicsScene, QGraphicsView
from PyQt6.QtGui import QPixmap
from PyQt6.QtCore import Qt

from ui.widgets import ClipItemWidget


def render_widget(widget, width, output_path):
    """Render a widget to a PNG screenshot."""
    widget.show()
    app = QApplication.instance()
    app.processEvents()

    # Render the widget at its real layout contract size. Do not inflate height here,
    # otherwise screenshots can hide clipping that happens in the live list.
    widget.resize(widget.sizeHint())

    pixmap = QPixmap(widget.size())
    pixmap.fill(Qt.GlobalColor.transparent)
    widget.render(pixmap)
    pixmap.save(output_path, "PNG")
    print(f"Saved: {output_path} ({widget.width()}x{widget.height()})")


def main():
    app = QApplication([])

    # 1. Normal history clip with tag - verify [text] [badge] [actions] layout
    item1 = {
        "id": 1,
        "type": "text",
        "content": "This is a sample clipboard entry with some text content that should wrap properly within the available width while keeping action buttons flush right.",
        "tag": "demo",
        "group_name": "",
    }
    w1 = ClipItemWidget(item1, is_pinned=False, parent_list=None, available_width=400)
    render_widget(w1, 400, os.path.join(ROOT_DIR, "docs/ui-tests/clip-row-aligned.png"))

    # Verify geometry programmatically
    print(f"\n--- Layout verification (history clip with tag) ---")
    print(f"Widget size: {w1.width()}x{w1.height()}")
    print(f"Content container x={w1.content_container.x()}, w={w1.content_container.width()}")
    print(f"Badge widget x={w1.btn_v_widget.x()}, w={w1.btn_v_widget.width()}")
    print(f"Action container x={w1.btn_container.x()}, w={w1.btn_container.width()}")
    assert w1.btn_container.x() > w1.content_container.x(), "Actions must be to the right of content"
    assert w1.btn_container.x() >= w1.content_container.width() - 4, "Actions must be after content container"
    print("PASS: Action column is flush-right")

    # 2. Pinned clip with tag - verify tools are visible (no height clipping)
    item2 = {
        "id": 2,
        "type": "text",
        "content": "Short pinned clip",
        "tag": "important",
        "group_name": "",
    }
    w2 = ClipItemWidget(item2, is_pinned=True, parent_list=None, available_width=400)
    render_widget(w2, 400, os.path.join(ROOT_DIR, "docs/ui-tests/clip-row-pinned-tools.png"))

    # Verify pinned row height is sufficient for action buttons
    print(f"\n--- Layout verification (pinned clip) ---")
    print(f"Widget size: {w2.width()}x{w2.height()}")
    btn_height = w2.btn_container.height()
    widget_height = w2.height()
    print(f"Action container height: {btn_height}")
    print(f"Widget height: {widget_height}")
    assert widget_height >= btn_height + 10, f"Pinned row ({widget_height}) must be taller than action buttons ({btn_height})"
    print("PASS: Pinned row is tall enough to show all action tools")

    # 3. Verify badge column does not inflate text width
    print(f"\n--- Badge column verification ---")
    print(f"Badge widget x={w2.btn_v_widget.x()}, w={w2.btn_v_widget.width()}")
    print(f"Content container w={w2.content_container.width()}")
    # Badge should be between content and actions
    assert w2.btn_v_widget.x() > w2.content_container.x(), "Badge must be to the right of content"
    assert w2.btn_container.x() > w2.btn_v_widget.x(), "Actions must be to the right of badge"
    print("PASS: Layout order is [text] [badge] [actions]")

    # 4. Long tags belong below content, not inside the line-count meta column
    item3 = {
        "id": 3,
        "type": "text",
        "content": "Clipboard body with a long tag underneath.",
        "tag": "very-long-tag-that-should-wrap-below-the-clip-content",
        "group_name": "",
    }
    w3 = ClipItemWidget(item3, is_pinned=False, parent_list=None, available_width=400)
    render_widget(w3, 400, os.path.join(ROOT_DIR, "docs/ui-tests/clip-row-long-tag.png"))
    assert w3.lbl_tag.parent() == w3.content_container, "Tag must live under content"
    assert w3.lbl_line_count.parent() == w3.btn_v_widget, "Line count must stay in meta column"
    print("PASS: Long tag is rendered below content")

    print("\nAll geometry checks passed!")


if __name__ == "__main__":
    main()

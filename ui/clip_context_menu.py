import subprocess
import webbrowser
from pathlib import Path

from PyQt6.QtWidgets import QInputDialog, QMenu

from core.action_runner import is_valid_url, open_url


def resolve_image_path(clip_data):
    from .widgets import IMAGE_DIR

    content = str(clip_data.get("content") or "")
    return Path(IMAGE_DIR, content).resolve() if content else None


def _open_image(clip_data, parent_handler=None):
    image_path = resolve_image_path(clip_data)
    if image_path and image_path.is_file():
        webbrowser.open(image_path.as_uri())


def _show_preview(clip_data, parent_handler=None):
    if parent_handler and hasattr(parent_handler, "handle_show_preview"):
        parent_handler.handle_show_preview(clip_data)


def _copy_image_path(clip_data, parent_handler=None):
    image_path = resolve_image_path(clip_data)
    if image_path and parent_handler and hasattr(parent_handler, "handle_copy_image_path"):
        parent_handler.handle_copy_image_path(str(image_path))


def _open_with_paint(clip_data, parent_handler=None):
    image_path = resolve_image_path(clip_data)
    if image_path and image_path.is_file():
        try:
            subprocess.Popen(["mspaint.exe", str(image_path)], shell=False)
        except OSError:
            pass


def _is_image(clip_data):
    return clip_data.get("type") == "image"


def _is_url(clip_data):
    if clip_data.get("type") != "text":
        return False
    return is_valid_url(str(clip_data.get("content") or ""))


def _open_url_action(clip_data, parent_handler=None):
    url = str(clip_data.get("content") or "").strip()
    open_url(url)


CONTEXT_ACTIONS = [
    ("Show preview", lambda clip: True, _show_preview),
    ("Open this image", _is_image, _open_image),
    ("Copy image path", _is_image, _copy_image_path),
    ("Open with Paint", _is_image, _open_with_paint),
    ("Mở URL này", _is_url, _open_url_action),
]


def show_clip_context_menu(owner, clip_data, is_pinned, parent_handler, global_pos):
    if not clip_data or not parent_handler:
        return
    clip_id = clip_data.get("id")
    if not clip_id:
        return

    menu = QMenu(owner)
    menu.setStyleSheet(
        """
        QMenu { background-color: #2d2d2d; color: #eee; border: 1px solid #444; }
        QMenu::item:selected { background-color: #d18616; color: white; }
        """
    )

    storage = getattr(parent_handler, "storage", None)

    # 1. RUN action if action exists
    existing_action = storage.get_clip_action(clip_id) if (storage and hasattr(storage, "get_clip_action")) else None
    if existing_action:
        run_act = menu.addAction("⚡ RUN Action")
        font = run_act.font()
        font.setBold(True)
        run_act.setFont(font)
        run_act.setData(("run_action", None))
        menu.addSeparator()

    # 2. Preview, Image, URL actions
    extra_action_count = 0
    for index, (label, applies_to, _handler) in enumerate(CONTEXT_ACTIONS):
        if applies_to(clip_data):
            action = menu.addAction(label)
            action.setData(("context_action", index))
            extra_action_count += 1
    if extra_action_count:
        menu.addSeparator()

    # 3. Shortcut binding
    existing_shortcut = storage.get_shortcut_for_clip(clip_id) if (storage and hasattr(storage, "get_shortcut_for_clip")) else None
    if existing_shortcut:
        sc_edit_act = menu.addAction(f"Đổi phím tắt (Ctrl+{existing_shortcut})...")
        sc_edit_act.setData(("assign_shortcut", None))
        sc_remove_act = menu.addAction(f"Gỡ phím tắt (Ctrl+{existing_shortcut})")
        sc_remove_act.setData(("remove_shortcut", None))
    else:
        sc_act = menu.addAction("Gán phím tắt (Ctrl+key)...")
        sc_act.setData(("assign_shortcut", None))
    menu.addSeparator()

    # 4. Action configuration
    if existing_action:
        act_edit_act = menu.addAction("Sửa Action...")
        act_edit_act.setData(("configure_action", None))
        act_remove_act = menu.addAction("Gỡ Action")
        act_remove_act.setData(("remove_action", None))
    else:
        act_add_act = menu.addAction("Gán Action...")
        act_add_act.setData(("configure_action", None))
    menu.addSeparator()

    # 5. Group management
    group_menu = menu.addMenu("Add to Group")
    if storage is not None:
        for group in storage.get_groups():
            action = group_menu.addAction(group)
            action.setData(("group", group))
        if group_menu.actions():
            group_menu.addSeparator()
    new_group_action = group_menu.addAction("New Group...")
    new_group_action.setData(("new_group", None))

    current_group = clip_data.get("group_name", "")
    if current_group:
        remove_action = menu.addAction(f"Remove from '{current_group}'")
        remove_action.setData(("remove_group", None))
    menu.addSeparator()

    # 6. Tag & Fix
    add_tag_action = menu.addAction("Add Tag")
    add_tag_action.setData(("tag", None))
    if is_pinned and clip_data.get("type") == "text":
        fix_action = menu.addAction("Fix")
        fix_action.setData(("fix", None))

    selected_action = menu.exec(global_pos)
    if not selected_action or not selected_action.data():
        return

    action_type, value = selected_action.data()
    if action_type == "context_action":
        CONTEXT_ACTIONS[value][2](clip_data, parent_handler)
    elif action_type == "run_action" and hasattr(parent_handler, "handle_run_action"):
        parent_handler.handle_run_action(clip_data)
    elif action_type == "assign_shortcut" and hasattr(parent_handler, "handle_assign_shortcut"):
        parent_handler.handle_assign_shortcut(clip_data)
    elif action_type == "remove_shortcut" and hasattr(parent_handler, "handle_remove_shortcut"):
        parent_handler.handle_remove_shortcut(clip_id)
    elif action_type == "configure_action" and hasattr(parent_handler, "handle_configure_action"):
        parent_handler.handle_configure_action(clip_data)
    elif action_type == "remove_action" and hasattr(parent_handler, "handle_remove_action"):
        parent_handler.handle_remove_action(clip_id)
    elif action_type == "tag" and hasattr(parent_handler, "handle_add_tag"):
        tag, ok = QInputDialog.getText(
            owner, "Add Tag", "Enter tag name:", text=clip_data.get("tag", "")
        )
        if ok:
            parent_handler.handle_add_tag(clip_id, tag)
    elif action_type == "group" and hasattr(parent_handler, "handle_set_group"):
        parent_handler.handle_set_group(clip_id, value)
    elif action_type == "new_group" and hasattr(parent_handler, "handle_set_group"):
        group_name, ok = QInputDialog.getText(owner, "New Group", "Enter group name:")
        if ok and group_name.strip():
            parent_handler.handle_set_group(clip_id, group_name.strip())
    elif action_type == "remove_group" and hasattr(parent_handler, "handle_set_group"):
        parent_handler.handle_set_group(clip_id, "")
    elif action_type == "fix":
        from .widgets import ClipEditPopup

        popup = ClipEditPopup(clip_data, parent_handler, owner)
        popup.move(global_pos)
        popup.show()
        owner._edit_popup = popup

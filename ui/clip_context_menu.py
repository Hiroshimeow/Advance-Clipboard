from PyQt6.QtWidgets import QInputDialog, QMenu


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

    group_menu = menu.addMenu("Add to Group")
    storage = getattr(parent_handler, "storage", None)
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

    add_tag_action = menu.addAction("Add Tag")
    add_tag_action.setData(("tag", None))
    if is_pinned and clip_data.get("type") == "text":
        fix_action = menu.addAction("Fix")
        fix_action.setData(("fix", None))

    action = menu.exec(global_pos)
    if not action or not action.data():
        return

    action_type, value = action.data()
    if action_type == "tag" and hasattr(parent_handler, "handle_add_tag"):
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

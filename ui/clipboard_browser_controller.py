from PyQt6.QtCore import Qt, QTimer, QSize, QSignalBlocker
from PyQt6.QtWidgets import QListWidgetItem
from .widgets import (
    ClipItemWidget,
    GroupHeaderWidget,
    PAGE_SIZE_HISTORY,
    PAGE_SIZE_PINNED,
)

SEARCH_PAGE_SIZE_HISTORY = 12
SEARCH_PAGE_SIZE_PINNED = 12
SEARCH_DEBOUNCE_MS = 120


class ClipboardBrowserController:
    def __init__(self, app):
        self.app = app

        # Pagination state
        self.history_offset = 0
        self.pinned_offset = 0
        self.history_has_more = True
        self.pinned_has_more = True

        # Group expansion state
        self.expanded_groups = set()
        self.group_headers = {}  # group_name -> QListWidgetItem
        self.expanded_clip_ids = set()

        # UI state
        self.current_search_query = ""
        self._last_search_query = ""
        self._search_generation = 0
        self._queued_search_after_refresh = False
        self.active_side = "history"
        self._is_refreshing = False
        # Timers
        self.search_debounce_timer = QTimer()
        self.search_debounce_timer.setSingleShot(True)
        self.search_debounce_timer.timeout.connect(self.app._do_search)

        self._focus_query_timer = None

    @property
    def storage(self):
        return self.app.storage

    def on_ui_opened(self):
        """Called when the UI window is shown/toggled open."""
        # Focus search input and put cursor at end
        self.app.search_input.setFocus()
        self.app.search_input.setCursorPosition(len(self.app.search_input.text()))

        # Ensure we have a valid selection in the active list
        self._ensure_current_item()
        self._sync_selection_to_map()

    def reset_for_hotkey_open(self, refresh=False):
        """Reset transient UI state when user re-opens via hotkey."""
        self.active_side = "history"
        self.expanded_clip_ids.clear()
        if refresh:
            self.refresh_lists(maintain_selection=False)
            return
        self.set_active_side("history")
        if self.app.list_history.count() > 0:
            self.app.list_history.scrollToTop()
        self._apply_default_selection()
        self._sync_selection_to_map()

    def set_active_side(self, side):
        """Switch between history and pinned columns."""
        if side not in ("history", "pinned"):
            return
        self.active_side = side

        # Update UI visuals (border highlights)
        h_style = self.app.list_history.styleSheet()
        p_style = self.app.list_pinned.styleSheet()

        if side == "history":
            if "#333" in h_style:
                h_style = h_style.replace("#333", "#aa8030")
            if "#aa8030" in p_style:
                p_style = p_style.replace("#aa8030", "#333")
        else:
            if "#333" in p_style:
                p_style = p_style.replace("#333", "#aa8030")
            if "#aa8030" in h_style:
                h_style = h_style.replace("#aa8030", "#333")

        self.app.list_history.setStyleSheet(h_style)
        self.app.list_pinned.setStyleSheet(p_style)

        self._ensure_current_item()
        self._sync_selection_to_map()

    def _active_list(self):
        return (
            self.app.list_history
            if self.active_side == "history"
            else self.app.list_pinned
        )

    def _is_pasteable_item(self, item):
        if not item:
            return False
        data = item.data(Qt.ItemDataRole.UserRole)
        return bool(data and isinstance(data, dict) and "content" in data)

    def _first_pasteable_row(self, list_widget, start_row=0):
        for i in range(start_row, list_widget.count()):
            if self._is_pasteable_item(list_widget.item(i)):
                return i
        return None

    def _next_pasteable_row(self, list_widget, from_row, direction):
        """Find next/prev pasteable item, skipping headers."""
        if list_widget.count() == 0:
            return None
        step = 1 if direction > 0 else -1
        r = from_row + step
        while 0 <= r < list_widget.count():
            if self._is_pasteable_item(list_widget.item(r)):
                return r
            r += step
        return None

    def _apply_default_selection(self):
        h_list = self.app.list_history
        first_h = self._first_pasteable_row(h_list)
        if first_h is not None:
            h_list.setCurrentRow(first_h)
            self.set_active_side("history")
            return

        p_list = self.app.list_pinned
        first_p = self._first_pasteable_row(p_list)
        if first_p is not None:
            p_list.setCurrentRow(first_p)
            self.set_active_side("pinned")

    def _select_with_fallback_rules(self, prev_clip_id=None, prev_row=-1, widget=None):
        """Apply selection fallback rules after a list refresh/filter."""
        # widget is ignored as controller uses its own reference
        w = self._active_list()
        if w.count() == 0:
            self._apply_default_selection()
            return

        # 1) Keep same clip if it still exists
        if prev_clip_id is not None:
            for r in range(w.count()):
                it = w.item(r)
                if not self._is_pasteable_item(it):
                    continue
                data = it.data(Qt.ItemDataRole.UserRole)
                if isinstance(data, dict) and data.get("id") == prev_clip_id:
                    w.setCurrentRow(r)
                    return

        # 2) Fallback to row position
        hint = prev_row
        if hint < 0:
            hint = -1
        if hint >= w.count():
            hint = w.count() - 1

        # Next selectable after hint
        for r in range(hint + 1, w.count()):
            it = w.item(r)
            if self._is_pasteable_item(it):
                w.setCurrentRow(r)
                return

        # Previous selectable before hint
        for r in range(min(hint - 1, w.count() - 1), -1, -1):
            it = w.item(r)
            if self._is_pasteable_item(it):
                w.setCurrentRow(r)
                return

        # First selectable
        first = self._first_pasteable_row(w, 0)
        if first is not None:
            w.setCurrentRow(first)
        else:
            self._apply_default_selection()

    def _ensure_current_item(self):
        w = self._active_list()
        if w.count() == 0:
            return
        r = w.currentRow()
        if r < 0 or not self._is_pasteable_item(w.item(r)):
            first = self._first_pasteable_row(w, 0)
            if first is not None:
                w.setCurrentRow(first)

    def nav_up(self):
        w = self._active_list()
        if w.count() == 0:
            return
        self._ensure_current_item()
        r = self._next_pasteable_row(w, w.currentRow(), -1)
        if r is not None:
            w.setCurrentRow(r)
        self.app.search_input.setFocus()
        self._sync_selection_to_map()

    def nav_down(self):
        w = self._active_list()
        if w.count() == 0:
            return
        self._ensure_current_item()
        r = self._next_pasteable_row(w, w.currentRow(), 1)
        if r is not None:
            w.setCurrentRow(r)
        self.app.search_input.setFocus()
        self._sync_selection_to_map()

    def nav_left(self):
        self.set_active_side("history")

    def nav_right(self):
        self.set_active_side("pinned")

    def activate_current(self):
        """Paste the currently active item."""
        w = self._active_list()
        if not w:
            return
        self._ensure_current_item()
        ci = w.currentItem()
        if not self._is_pasteable_item(ci):
            return
        data = ci.data(Qt.ItemDataRole.UserRole)
        self.app.handle_paste(data)
        self.app.search_input.setFocus()

    def on_search_text_changed(self, text):
        """Debounced search optimized for interactive typing."""
        previous_query = self.current_search_query
        self.current_search_query = text.strip()
        if previous_query and not self.current_search_query:
            # Clearing search is a navigation reset, not a filtered-refresh.
            # Drop stale selection state so Enter returns to newest history.
            self.active_side = "history"
            self.app.list_history.setCurrentRow(-1)
            self.app.list_pinned.setCurrentRow(-1)
        # Sync to sidecar immediately
        if self.app.sidecar:
            self.app.sidecar.search_bar.blockSignals(True)
            self.app.sidecar.search_bar.setText(text)
            self.app.sidecar.search_bar.blockSignals(False)
        # Debounce typing enough to avoid rebuilding QWidget rows for every key.
        # Search still feels live, but avoids UI-thread thrashing.
        self.search_debounce_timer.start(SEARCH_DEBOUNCE_MS)

    def _do_search(self):
        """Execute the actual search query."""
        query = self.current_search_query
        if query == self._last_search_query and not self._queued_search_after_refresh:
            return
        if self._is_refreshing:
            self._queued_search_after_refresh = True
            return

        self._search_generation += 1
        self._queued_search_after_refresh = False
        # Search result ordering is the selection contract: focus the first ranked
        # result. Clearing search resets to the newest history item.
        self.refresh_lists(maintain_selection=False)
        self._last_search_query = query

        # Sync search to sidecar map
        if self.app.sidecar:
            # Delayed zoom to matching nodes
            if not self._focus_query_timer:
                self._focus_query_timer = QTimer(self.app)
                self._focus_query_timer.setSingleShot(True)

            self._focus_query_timer.stop()
            try:
                self._focus_query_timer.timeout.disconnect()
            except:
                pass
            self._focus_query_timer.timeout.connect(
                lambda: self.app.sidecar.focus_query(query)
                if self.app.sidecar
                else None
            )
            self._focus_query_timer.start(300)

    def _sync_selection_to_map(self):
        """Sync the currently selected clip to the neural map (focus node)."""
        if (
            not self.app.sidecar
            or not hasattr(self.app.sidecar, "isVisible")
            or not self.app.sidecar.isVisible()
        ):
            return
        w = self._active_list()
        if not w:
            return
        ci = w.currentItem()
        if not self._is_pasteable_item(ci):
            return
        d = ci.data(Qt.ItemDataRole.UserRole)
        if isinstance(d, dict) and "id" in d:
            self.app.sidecar.focus_node(d["id"])

    def refresh_lists(self, maintain_selection=True):
        """Refresh both history and pinned lists."""
        if self._is_refreshing:
            return
        self._is_refreshing = True

        # Capture selection before refresh
        prev_clip_id = None
        prev_row = -1
        active_widget = self._active_list()
        if maintain_selection and active_widget:
            prev_row = active_widget.currentRow()
            ci = active_widget.currentItem()
            if self._is_pasteable_item(ci):
                d = ci.data(Qt.ItemDataRole.UserRole)
                if isinstance(d, dict):
                    prev_clip_id = d.get("id")

        try:
            self.app.setUpdatesEnabled(False)

            history_bar = self.app.list_history.verticalScrollBar()
            pinned_bar = self.app.list_pinned.verticalScrollBar()
            history_scroll_value = history_bar.value()
            pinned_scroll_value = pinned_bar.value()
            history_blocker = QSignalBlocker(history_bar)
            pinned_blocker = QSignalBlocker(pinned_bar)

            # 1. Refresh History
            self.app.list_history.clear()
            self.history_offset = 0

            if self.current_search_query:
                history_clips = self.storage.search_history(
                    self.current_search_query,
                    limit=SEARCH_PAGE_SIZE_HISTORY,
                    semantic=False,
                )
                self.history_has_more = False  # Search returns first fast page
            else:
                history_clips = self.storage.get_history(
                    limit=PAGE_SIZE_HISTORY, offset=0
                )
                self.history_has_more = len(history_clips) >= PAGE_SIZE_HISTORY

            self._append_items(self.app.list_history, history_clips, is_pinned=False)
            self.history_offset = len(history_clips)

            # 2. Refresh Pinned
            self.refresh_pinned_list()

            # 3. Maintain/Set selection
            if maintain_selection:
                self._select_with_fallback_rules(
                    prev_clip_id=prev_clip_id, prev_row=prev_row
                )
            else:
                self._apply_default_selection()

            if self.current_search_query and maintain_selection:
                history_bar.setValue(min(history_scroll_value, history_bar.maximum()))
                pinned_bar.setValue(min(pinned_scroll_value, pinned_bar.maximum()))

            del history_blocker
            del pinned_blocker
            self.app.setUpdatesEnabled(True)
        finally:
            self._is_refreshing = False
            if self._queued_search_after_refresh:
                self.search_debounce_timer.start(0)

    def refresh_pinned_list(self):
        """Refresh only the pinned list, preserving group expansion state."""
        self.app.list_pinned.clear()
        self.group_headers = {}
        self.pinned_offset = 0

        if self.current_search_query:
            # In search mode, flat list of pinned items (no groups)
            pinned_clips = self.storage.search_pinned(
                self.current_search_query,
                limit=SEARCH_PAGE_SIZE_PINNED,
                semantic=False,
            )
            self.pinned_has_more = False  # Search returns first fast page
            self._append_items(self.app.list_pinned, pinned_clips, is_pinned=True)
        else:
            # Grouped view
            groups = self.storage.get_groups()
            for g_name in groups:
                clips = self.storage.get_clips_by_group(g_name)
                if not clips:
                    continue

                # Add group header
                item = QListWidgetItem(self.app.list_pinned)
                header = GroupHeaderWidget(g_name, len(clips), self.app)
                item.setSizeHint(
                    QSize(self.app.list_pinned.viewport().width() or 300, 45)
                )
                self.app.list_pinned.addItem(item)
                self.app.list_pinned.setItemWidget(item, header)

                self.group_headers[g_name] = item

                # Check expansion state
                is_exp = g_name in self.expanded_groups
                header.set_expanded(is_exp)

                # Add children
                for c in clips:
                    child_item = QListWidgetItem(self.app.list_pinned)
                    child_width = self.app.list_pinned.viewport().width() or 300
                    ui = ClipItemWidget(
                        c,
                        True,
                        self.app,
                        is_grouped=True,
                        expanded=c.get("id") in self.expanded_clip_ids,
                        available_width=child_width,
                    )
                    child_item.setSizeHint(
                        QSize(
                            child_width, ui.height()
                        )
                    )
                    child_item.setData(Qt.ItemDataRole.UserRole, c)
                    child_item.setData(Qt.ItemDataRole.UserRole + 1, g_name)
                    self.app.list_pinned.addItem(child_item)
                    self.app.list_pinned.setItemWidget(child_item, ui)
                    if not is_exp:
                        child_item.setHidden(True)

            # Add ungrouped pinned items
            ungrouped = self.storage.get_ungrouped_pinned()
            self._append_items(self.app.list_pinned, ungrouped, is_pinned=True)
            self.pinned_has_more = False

    def remove_clip_from_ui(self, clip_id):
        """Optimistically remove a clip so delete feels instant."""
        self.expanded_clip_ids.discard(clip_id)
        for list_widget in (self.app.list_history, self.app.list_pinned):
            for row in range(list_widget.count() - 1, -1, -1):
                item = list_widget.item(row)
                if not self._is_pasteable_item(item):
                    continue
                data = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(data, dict) and data.get("id") == clip_id:
                    removed = list_widget.takeItem(row)
                    if removed is not None:
                        del removed

    def toggle_clip_expanded(self, clip_id):
        if clip_id in self.expanded_clip_ids:
            self.expanded_clip_ids.discard(clip_id)
        else:
            self.expanded_clip_ids.add(clip_id)
        self._rerender_clip_row_in_place(clip_id)

    def _rerender_clip_row_in_place(self, clip_id):
        """Update only one expanded/collapsed row without resetting scroll."""
        for list_widget, is_pinned in (
            (self.app.list_history, False),
            (self.app.list_pinned, True),
        ):
            for row in range(list_widget.count()):
                item = list_widget.item(row)
                if not self._is_pasteable_item(item):
                    continue
                clip = item.data(Qt.ItemDataRole.UserRole)
                if not isinstance(clip, dict) or clip.get("id") != clip_id:
                    continue
                scroll_bar = list_widget.verticalScrollBar()
                scroll_value = scroll_bar.value()
                horizontal_bar = list_widget.horizontalScrollBar()
                horizontal_value = horizontal_bar.value()
                width = list_widget.viewport().width() or ((self.app.width() // 2) - 25)
                group_name = item.data(Qt.ItemDataRole.UserRole + 1)

                list_widget.setUpdatesEnabled(False)
                scroll_blocker = QSignalBlocker(scroll_bar)
                horizontal_blocker = QSignalBlocker(horizontal_bar)
                try:
                    ui = ClipItemWidget(
                        clip,
                        is_pinned,
                        self.app,
                        is_grouped=bool(group_name),
                        expanded=clip_id in self.expanded_clip_ids,
                        available_width=width,
                    )
                    item.setSizeHint(QSize(width, ui.height()))
                    list_widget.setItemWidget(item, ui)
                    scroll_bar.setValue(min(scroll_value, scroll_bar.maximum()))
                    horizontal_bar.setValue(min(horizontal_value, horizontal_bar.maximum()))
                finally:
                    del scroll_blocker
                    del horizontal_blocker
                    list_widget.setUpdatesEnabled(True)

                def restore_scroll():
                    scroll_bar.setValue(min(scroll_value, scroll_bar.maximum()))
                    horizontal_bar.setValue(min(horizontal_value, horizontal_bar.maximum()))

                QTimer.singleShot(0, restore_scroll)
                return

    def reset_after_delete_refresh(self):
        self._ensure_current_item()
        self._sync_selection_to_map()

    def _append_items(self, list_widget, clips, is_pinned):
        width = list_widget.viewport().width() or ((self.app.width() // 2) - 25)
        for clip in clips:
            item = QListWidgetItem(list_widget)
            is_expanded = clip.get("id") in self.expanded_clip_ids
            ui = ClipItemWidget(
                clip,
                is_pinned,
                self.app,
                expanded=is_expanded,
                available_width=width,
            )
            item.setSizeHint(QSize(width, ui.height()))
            item.setData(Qt.ItemDataRole.UserRole, clip)
            list_widget.addItem(item)
            list_widget.setItemWidget(item, ui)

    def on_history_scroll(self, value):
        if not self.history_has_more or self._is_refreshing:
            return
        bar = self.app.list_history.verticalScrollBar()
        if value >= bar.maximum() - 50:
            self._load_more_history()

    def on_pinned_scroll(self, value):
        if not self.pinned_has_more or self._is_refreshing:
            return
        bar = self.app.list_pinned.verticalScrollBar()
        if value >= bar.maximum() - 50:
            self._load_more_pinned()

    def _load_more_history(self):
        clips = self.storage.get_history(
            limit=PAGE_SIZE_HISTORY, offset=self.history_offset
        )
        if not clips or len(clips) < PAGE_SIZE_HISTORY:
            self.history_has_more = False
        if clips:
            self._append_items(self.app.list_history, clips, is_pinned=False)
            self.history_offset += len(clips)

    def _load_more_pinned(self):
        if not self.current_search_query:
            return
        clips = self.storage.get_pinned(
            limit=PAGE_SIZE_PINNED, offset=self.pinned_offset
        )
        if not clips or len(clips) < PAGE_SIZE_PINNED:
            self.pinned_has_more = False
        if clips:
            self._append_items(self.app.list_pinned, clips, is_pinned=True)
            self.pinned_offset += len(clips)

    def expand_group(self, group_name):
        self.expanded_groups.add(group_name)
        for i in range(self.app.list_pinned.count()):
            item = self.app.list_pinned.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole + 1) == group_name:
                item.setHidden(False)

    def collapse_group(self, group_name):
        self.expanded_groups.discard(group_name)
        for i in range(self.app.list_pinned.count()):
            item = self.app.list_pinned.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole + 1) == group_name:
                item.setHidden(True)

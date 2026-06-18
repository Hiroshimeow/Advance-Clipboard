import queue
import threading
import time

from PyQt6.QtCore import Qt, QTimer, QSize, QSignalBlocker, QEvent
from PyQt6.QtWidgets import QListWidgetItem
from .widgets import (
    ClipItemWidget,
    GroupHeaderWidget,
    PAGE_SIZE_HISTORY,
    PAGE_SIZE_PINNED,
)

SEARCH_PAGE_SIZE_HISTORY = 12
SEARCH_PAGE_SIZE_PINNED = 12
SEARCH_DEBOUNCE_MS = 100
SEARCH_REFRESH_DELAY_MS = 50


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
        self._search_result_queue = queue.Queue()
        self._search_worker_generation = 0
        self._pending_search_generations = set()
        self._refresh_result_queue = queue.Queue()
        self._refresh_worker_generation = 0
        self._pending_refresh_generations = set()
        self.active_side = "history"
        self._is_refreshing = False
        # Timers
        self.search_debounce_timer = QTimer()
        self.search_debounce_timer.setSingleShot(True)
        self.search_debounce_timer.timeout.connect(self.app._do_search)

        self._search_refresh_timer = QTimer(self.app)
        self._search_refresh_timer.setSingleShot(True)
        self._search_refresh_timer.timeout.connect(self._perform_search_refresh)

        self._search_result_timer = QTimer(self.app)
        self._search_result_timer.setSingleShot(False)
        self._search_result_timer.timeout.connect(self._drain_search_results)

        self._refresh_result_timer = QTimer(self.app)
        self._refresh_result_timer.setSingleShot(False)
        self._refresh_result_timer.timeout.connect(self._drain_refresh_results)

        self._layout_refresh_timer = QTimer(self.app)
        self._layout_refresh_timer.setSingleShot(True)
        self._layout_refresh_timer.timeout.connect(self._refresh_visible_row_layouts)

        self._focus_query_timer = None
        self._last_history_layout_width = None
        self._last_pinned_layout_width = None

    def bind_viewports(self):
        if hasattr(self.app, "list_history") and hasattr(self.app, "list_pinned"):
            self.app.list_history.viewport().installEventFilter(self.app)
            self.app.list_pinned.viewport().installEventFilter(self.app)

    @property
    def storage(self):
        return self.app.storage

    def handle_viewport_event(self, watched, event):
        if watched in (
            self.app.list_history.viewport(),
            self.app.list_pinned.viewport(),
        ) and event.type() == QEvent.Type.Resize:
            self._schedule_layout_refresh()

    def _schedule_layout_refresh(self):
        if self._is_refreshing or getattr(self.app, "is_ui_dirty", False):
            return
        if not self.app.isVisible():
            return
        if time.monotonic() < getattr(self.app, "_ui_opening_until", 0.0):
            return
        self._layout_refresh_timer.start(16)

    def _refresh_visible_row_layouts(self):
        if self._is_refreshing:
            return
        self._refresh_list_row_layouts(self.app.list_history, is_pinned=False)
        self._refresh_list_row_layouts(self.app.list_pinned, is_pinned=True)

    def _build_clip_row(self, clip, is_pinned, *, is_grouped=False, expanded=False, width=None):
        row_width = width if width is not None else 300
        return ClipItemWidget(
            clip,
            is_pinned,
            self.app,
            is_grouped=is_grouped,
            expanded=expanded,
            available_width=row_width,
        )

    def _set_clip_row_widget(self, list_widget, item, row_widget):
        item.setSizeHint(row_widget.sizeHint())
        list_widget.setItemWidget(item, row_widget)

    def start_background_refresh(self):
        self._refresh_worker_generation += 1
        generation = self._refresh_worker_generation
        self._pending_refresh_generations.add(generation)
        if not self._refresh_result_timer.isActive():
            self._refresh_result_timer.start(15)
        worker = threading.Thread(
            target=self._run_refresh_worker,
            args=(generation,),
            daemon=True,
        )
        worker.start()

    def _run_refresh_worker(self, generation):
        try:
            history_clips = self.storage.get_history(limit=PAGE_SIZE_HISTORY, offset=0)
            groups = []
            for group_name in self.storage.get_groups():
                clips = self.storage.get_clips_by_group(group_name)
                if clips:
                    groups.append((group_name, clips))
            ungrouped = self.storage.get_ungrouped_pinned()
            self._refresh_result_queue.put(
                (generation, history_clips, groups, ungrouped, None)
            )
        except Exception as exc:
            self._refresh_result_queue.put((generation, [], [], [], exc))

    def _drain_refresh_results(self):
        latest = None
        while True:
            try:
                item = self._refresh_result_queue.get_nowait()
                self._pending_refresh_generations.discard(item[0])
                latest = item
            except queue.Empty:
                break

        if latest is None:
            if not self._pending_refresh_generations:
                self._refresh_result_timer.stop()
            return

        generation, history_clips, groups, ungrouped, error = latest
        if generation != self._refresh_worker_generation:
            if not self._pending_refresh_generations:
                self._refresh_result_timer.stop()
            return
        self._refresh_result_timer.stop()
        if error is not None:
            self.app.is_ui_dirty = True
            return
        self._apply_prefetched_refresh(history_clips, groups, ungrouped)
        if self.app.list_history.count() > 0:
            self.app.list_history.scrollToTop()
            self.app.list_history.setCurrentRow(0)
        self.app.pending_ui_clip_ids.clear()
        self.app._requires_full_ui_refresh = False
        self.app.is_ui_dirty = False
        self.app._on_ui_opened()

    def _apply_prefetched_refresh(self, history_clips, groups, ungrouped):
        if self._is_refreshing:
            return
        self._is_refreshing = True
        try:
            self.app.setUpdatesEnabled(False)
            self.app.list_history._clear_hover()
            self.app.list_pinned._clear_hover()
            self.app.list_history.clear()
            self.app.list_pinned.clear()
            self.group_headers = {}
            self.history_offset = 0
            self.pinned_offset = 0
            self.history_has_more = len(history_clips) >= PAGE_SIZE_HISTORY
            self.pinned_has_more = False
            self._append_items(self.app.list_history, history_clips, is_pinned=False)
            for group_name, clips in groups:
                item = QListWidgetItem(self.app.list_pinned)
                header = GroupHeaderWidget(group_name, len(clips), self.app)
                item.setSizeHint(QSize(self.app.list_pinned.viewport().width() or 300, 45))
                self.app.list_pinned.addItem(item)
                self.app.list_pinned.setItemWidget(item, header)
                self.group_headers[group_name] = item
                is_expanded = group_name in self.expanded_groups
                header.set_expanded(is_expanded)
                for clip in clips:
                    child_item = QListWidgetItem(self.app.list_pinned)
                    child_width = self._list_content_width(self.app.list_pinned)
                    ui = self._build_clip_row(
                        clip,
                        True,
                        is_grouped=True,
                        expanded=clip.get("id") in self.expanded_clip_ids,
                        width=child_width,
                    )
                    child_item.setData(Qt.ItemDataRole.UserRole, clip)
                    child_item.setData(Qt.ItemDataRole.UserRole + 1, group_name)
                    self.app.list_pinned.addItem(child_item)
                    self._set_clip_row_widget(self.app.list_pinned, child_item, ui)
                    if not is_expanded:
                        child_item.setHidden(True)
            self._append_items(self.app.list_pinned, ungrouped, is_pinned=True)
            self._apply_default_selection()
        finally:
            self.app.setUpdatesEnabled(True)
            self._is_refreshing = False

    def _refresh_list_row_layouts(self, list_widget, is_pinned):
        width = self._list_content_width(list_widget)
        width_attr = "_last_pinned_layout_width" if is_pinned else "_last_history_layout_width"
        if getattr(self, width_attr) == width:
            return
        setattr(self, width_attr, width)
        changed = False
        for row in range(list_widget.count()):
            item = list_widget.item(row)
            if item is None:
                continue
            current_hint = item.sizeHint()

            if not self._is_pasteable_item(item):
                widget = list_widget.itemWidget(item)
                if isinstance(widget, GroupHeaderWidget) and current_hint.width() != width:
                    item.setSizeHint(QSize(width, current_hint.height() or 45))
                    changed = True
                continue

            data = item.data(Qt.ItemDataRole.UserRole)
            group_name = item.data(Qt.ItemDataRole.UserRole + 1)
            ui = self._build_clip_row(
                data,
                is_pinned,
                is_grouped=bool(group_name),
                expanded=data.get("id") in self.expanded_clip_ids,
                width=width,
            )
            if current_hint == ui.sizeHint():
                continue

            self._set_clip_row_widget(list_widget, item, ui)
            changed = True

        if changed:
            list_widget.doItemsLayout()

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

        if hasattr(self.app.list_history, "set_active_visual"):
            self.app.list_history.set_active_visual(side == "history")
        if hasattr(self.app.list_pinned, "set_active_visual"):
            self.app.list_pinned.set_active_visual(side == "pinned")

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
            w.scrollToSelected()
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
            w.scrollToSelected()
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
        self._search_refresh_timer.start(SEARCH_REFRESH_DELAY_MS)

    def _perform_search_refresh(self):
        query = self.current_search_query
        if query == self._last_search_query and not self._queued_search_after_refresh:
            return
        if self._is_refreshing:
            self._queued_search_after_refresh = True
            self._search_refresh_timer.start(SEARCH_REFRESH_DELAY_MS)
            return
        self._queued_search_after_refresh = False
        if query:
            self._start_search_worker(query, self._search_generation)
            return
        # Search result ordering is the selection contract: focus the first ranked
        # result. Clearing search resets to the newest history item.
        self.refresh_lists(maintain_selection=False)
        self._last_search_query = query
        self._sync_sidecar_query(query)

    def _start_search_worker(self, query, generation):
        self._search_worker_generation = generation
        self._pending_search_generations.add(generation)
        if not self._search_result_timer.isActive():
            self._search_result_timer.start(15)
        worker = threading.Thread(
            target=self._run_search_worker,
            args=(query, generation),
            daemon=True,
        )
        worker.start()

    def _run_search_worker(self, query, generation):
        try:
            history_clips = self.storage.search_history(
                query,
                limit=SEARCH_PAGE_SIZE_HISTORY,
                semantic=True,
            )
            pinned_clips = self.storage.search_pinned(
                query,
                limit=SEARCH_PAGE_SIZE_PINNED,
                semantic=True,
            )
            self._search_result_queue.put(
                (generation, query, history_clips, pinned_clips, None)
            )
        except Exception as exc:
            self._search_result_queue.put((generation, query, [], [], exc))

    def _drain_search_results(self):
        latest = None
        while True:
            try:
                item = self._search_result_queue.get_nowait()
                self._pending_search_generations.discard(item[0])
                latest = item
            except queue.Empty:
                break

        if latest is None:
            if not self._pending_search_generations:
                self._search_result_timer.stop()
            return

        generation, query, history_clips, pinned_clips, error = latest
        if generation != self._search_generation or query != self.current_search_query:
            if not self._pending_search_generations:
                self._search_result_timer.stop()
            return
        if error is not None:
            self._last_search_query = query
            self._search_result_timer.stop()
            return

        self._apply_search_results(query, history_clips, pinned_clips)
        self._search_result_timer.stop()

    def _apply_search_results(self, query, history_clips, pinned_clips):
        if self._is_refreshing:
            self._queued_search_after_refresh = True
            return
        self._is_refreshing = True
        try:
            self.app.setUpdatesEnabled(False)
            self.app.list_history._clear_hover()
            self.app.list_pinned._clear_hover()
            self.app.list_history.clear()
            self.app.list_pinned.clear()
            self.group_headers = {}
            self.history_offset = 0
            self.pinned_offset = 0
            self.history_has_more = False
            self.pinned_has_more = False
            self._append_items(self.app.list_history, history_clips, is_pinned=False)
            self._append_items(self.app.list_pinned, pinned_clips, is_pinned=True)
            self._apply_default_selection()
        finally:
            self.app.setUpdatesEnabled(True)
            self._is_refreshing = False
        self._last_search_query = query
        self._sync_sidecar_query(query)

    def _sync_sidecar_query(self, query):
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

    def _list_content_width(self, list_widget):
        """Return a conservative row width that leaves room for list padding/scrollbar."""
        viewport_width = list_widget.viewport().width() or ((self.app.width() // 2) - 25)
        return max(240, viewport_width - 2)

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

            # Clear hover state before clearing lists
            self.app.list_history._clear_hover()
            self.app.list_pinned._clear_hover()

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
                    semantic=True,
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
                semantic=True,
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
                    child_width = self._list_content_width(self.app.list_pinned)
                    ui = self._build_clip_row(
                        c,
                        True,
                        is_grouped=True,
                        expanded=c.get("id") in self.expanded_clip_ids,
                        width=child_width,
                    )
                    child_item.setData(Qt.ItemDataRole.UserRole, c)
                    child_item.setData(Qt.ItemDataRole.UserRole + 1, g_name)
                    self.app.list_pinned.addItem(child_item)
                    self._set_clip_row_widget(self.app.list_pinned, child_item, ui)
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
            # Clear hover state before removing to prevent crashes
            list_widget._clear_hover()
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
                width = self._list_content_width(list_widget)
                group_name = item.data(Qt.ItemDataRole.UserRole + 1)

                list_widget.setUpdatesEnabled(False)
                scroll_blocker = QSignalBlocker(scroll_bar)
                horizontal_blocker = QSignalBlocker(horizontal_bar)
                try:
                    ui = self._build_clip_row(
                        clip,
                        is_pinned,
                        is_grouped=bool(group_name),
                        expanded=clip_id in self.expanded_clip_ids,
                        width=width,
                    )
                    self._set_clip_row_widget(list_widget, item, ui)
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


    def apply_pending_history_updates(self, clip_ids):
        """Incrementally prepend/move copied clips instead of rebuilding all UI."""
        if self.current_search_query or not clip_ids:
            return False

        width = self._list_content_width(self.app.list_history)
        changed = False
        self.app.list_history.setUpdatesEnabled(False)
        try:
            for clip_id in reversed(list(dict.fromkeys(clip_ids))):
                clip = self.storage.get_clip_by_id(clip_id)
                if not clip:
                    continue
                existing_row = None
                for row in range(self.app.list_history.count()):
                    item = self.app.list_history.item(row)
                    if not self._is_pasteable_item(item):
                        continue
                    data = item.data(Qt.ItemDataRole.UserRole)
                    if isinstance(data, dict) and data.get("id") == clip_id:
                        existing_row = row
                        break
                if existing_row is not None:
                    item = self.app.list_history.takeItem(existing_row)
                    if item is None:
                        continue
                else:
                    item = QListWidgetItem()

                item.setData(Qt.ItemDataRole.UserRole, clip)
                ui = self._build_clip_row(
                    clip,
                    False,
                    expanded=clip_id in self.expanded_clip_ids,
                    width=width,
                )
                self.app.list_history.insertItem(0, item)
                self._set_clip_row_widget(self.app.list_history, item, ui)
                changed = True

            if changed:
                self.history_offset = max(self.history_offset, self.app.list_history.count())
                self.app.list_history.scrollToTop()
                self.app.list_history.setCurrentRow(0)
                self.set_active_side("history")
        finally:
            self.app.list_history.setUpdatesEnabled(True)
        return changed

    def reset_after_delete_refresh(self):
        self._ensure_current_item()
        self._sync_selection_to_map()

    def _append_items(self, list_widget, clips, is_pinned):
        width = self._list_content_width(list_widget)
        for clip in clips:
            item = QListWidgetItem(list_widget)
            is_expanded = clip.get("id") in self.expanded_clip_ids
            ui = self._build_clip_row(
                clip,
                is_pinned,
                expanded=is_expanded,
                width=width,
            )
            item.setData(Qt.ItemDataRole.UserRole, clip)
            list_widget.addItem(item)
            self._set_clip_row_widget(list_widget, item, ui)

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

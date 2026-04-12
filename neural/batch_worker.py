from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional, Tuple


@dataclass
class QueueState:
    batch_size: int = 4
    flush_interval_seconds: int = 60 * 60 * 4
    _pending_new_ids: Deque[int] = field(default_factory=deque)
    _pending_priority_ids: Deque[int] = field(default_factory=deque)
    _queued_new: set[int] = field(default_factory=set)
    _queued_priority: set[int] = field(default_factory=set)
    _pending_since: Optional[float] = None

    def enqueue_new_clip(self, clip_id: int) -> None:
        if clip_id in self._queued_priority or clip_id in self._queued_new:
            return
        self._pending_new_ids.append(clip_id)
        self._queued_new.add(clip_id)
        if self._pending_since is None:
            self._pending_since = 0.0

    def enqueue_priority_reindex(self, clip_id: int) -> None:
        if clip_id in self._queued_priority:
            return
        if clip_id in self._queued_new:
            kept = deque()
            while self._pending_new_ids:
                current = self._pending_new_ids.popleft()
                if current != clip_id:
                    kept.append(current)
            self._pending_new_ids = kept
            self._queued_new.discard(clip_id)
            if not self._pending_new_ids:
                self._pending_since = None
        self._pending_priority_ids.append(clip_id)
        self._queued_priority.add(clip_id)

    def pop_next_job(self, now: float) -> Optional[Tuple[str, List[int]]]:
        if self._pending_priority_ids:
            clip_id = self._pending_priority_ids.popleft()
            self._queued_priority.discard(clip_id)
            return ("priority", [clip_id])

        if len(self._pending_new_ids) >= self.batch_size:
            ids = []
            for _ in range(self.batch_size):
                cid = self._pending_new_ids.popleft()
                self._queued_new.discard(cid)
                ids.append(cid)
            self._pending_since = now if self._pending_new_ids else None
            return ("batch", ids)

        if self._pending_new_ids and self._pending_since is not None:
            if self._pending_since == 0.0:
                self._pending_since = now
            elif now - self._pending_since >= self.flush_interval_seconds:
                ids = []
                while self._pending_new_ids and len(ids) < self.batch_size:
                    cid = self._pending_new_ids.popleft()
                    self._queued_new.discard(cid)
                    ids.append(cid)
                self._pending_since = now if self._pending_new_ids else None
                return ("batch", ids)

        return None


class BatchWorker:
    def __init__(self, batch_size: int = 4, flush_interval_seconds: int = 60 * 60 * 4):
        self.state = QueueState(
            batch_size=batch_size,
            flush_interval_seconds=flush_interval_seconds,
        )
        self._lock = threading.Lock()

    def enqueue_new_clip(self, clip_id: int) -> None:
        with self._lock:
            self.state.enqueue_new_clip(clip_id)

    def enqueue_priority_reindex(self, clip_id: int) -> None:
        with self._lock:
            self.state.enqueue_priority_reindex(clip_id)

    def pop_next_job(self, now: float):
        with self._lock:
            return self.state.pop_next_job(now)

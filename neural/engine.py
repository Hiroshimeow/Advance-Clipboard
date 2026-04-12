import json
import os
import threading
import time
import numpy as np
from typing import List, Dict, Any, Tuple
import logging

from neural.batch_worker import BatchWorker
from neural.indexer import NeuralIndexer

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None


class NeuralEngine(threading.Thread):
    def __init__(self, storage, config_path: str):
        super().__init__(name="NeuralEngine", daemon=True)
        self.storage = storage
        self.config_path = config_path
        self.model = None
        self.is_running = True
        self.batch_size = 2  # catch-up chunk size
        self.new_clip_batch_size = 4
        self.pending_flush_interval_seconds = 60 * 60 * 4
        self.max_legacy = 100
        self.max_recent_index = 200
        self.index_pinned_always = True
        self.similarity_threshold = 0.45
        self.max_neighbors = 5
        self.lexical_prefix_boost = 0.30
        self.lexical_word_boost = 0.15
        self.status_text = "warming"
        self.window_total = 0
        self.indexed_in_window = 0
        self.model_ready = False
        self._last_progress_log = None
        self._load_config()
        self._worker = BatchWorker(
            batch_size=self.new_clip_batch_size,
            flush_interval_seconds=self.pending_flush_interval_seconds,
        )
        self._wake_event = threading.Event()

    def _load_config(self):
        if os.path.exists(self.config_path) and os.path.getsize(self.config_path) > 0:
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    self.max_legacy = config.get("max_legacy_index", 100)
                    self.max_recent_index = config.get(
                        "max_recent_index", self.max_legacy
                    )
                    self.index_pinned_always = config.get("index_pinned_always", True)
                    self.similarity_threshold = config.get("similarity_threshold", 0.45)
                    self.max_neighbors = config.get("max_neighbors", 5)
                    self.lexical_prefix_boost = config.get("lexical_prefix_boost", 0.30)
                    self.lexical_word_boost = config.get("lexical_word_boost", 0.15)
                    self.new_clip_batch_size = config.get("new_clip_batch_size", 4)
                    self.pending_flush_interval_seconds = config.get(
                        "pending_flush_interval_seconds", 60 * 60 * 4
                    )
                return
            except json.JSONDecodeError:
                logging.warning(f"Config file {self.config_path} is corrupted. Regenerating defaults.")
                # Fall through to write defaults

        # Write default config if missing or corrupted
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "max_legacy_index": 100,
                    "max_recent_index": 200,
                    "index_pinned_always": True,
                    "new_clip_batch_size": 4,
                    "pending_flush_interval_seconds": 14400,
                },
                f,
                indent=4,
            )

    def run(self):
        print("[Neural Engine] Thread started")
        if SentenceTransformer:
            try:
                os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
                os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
                os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
                import warnings
                print("[Neural Engine] Loading model...")
                t0 = time.time()
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    self.model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
                print(f"[Neural Engine] Model loaded in {time.time()-t0:.1f}s")
                self.model_ready = True
                self.status_text = "ready"
            except Exception as e:
                print(f"[Neural Engine] FAILED to load model: {e}")
                return
        else:
            print("[Neural Engine] SentenceTransformer not available, engine idle")

        indexer = self._create_indexer()

        while self.is_running:
            try:
                self.indexed_in_window, self.window_total = self.storage.get_neural_window_totals(
                    recent_limit=self.max_recent_index,
                    include_pinned=self.index_pinned_always,
                )

                job = self._worker.pop_next_job(time.time())
                if job is not None:
                    _, clip_ids = job
                    start_time = time.time()
                    self.status_text = f"{self.indexed_in_window}/{self.window_total}"
                    print(
                        f"[Neural Engine] Processing queued job with {len(clip_ids)} clip(s)... ({self.indexed_in_window}/{self.window_total})"
                    )
                    indexer.index_clips(clip_ids)
                    elapsed = time.time() - start_time
                    self.indexed_in_window, self.window_total = self.storage.get_neural_window_totals(
                        recent_limit=self.max_recent_index,
                        include_pinned=self.index_pinned_always,
                    )
                    self.status_text = (
                        "ready"
                        if self.indexed_in_window >= self.window_total
                        else f"{self.indexed_in_window}/{self.window_total}"
                    )
                    self._wait_for_next_work(max(0.1, min(0.5, elapsed)))
                    continue

                if self.window_total > 0:
                    unindexed_ids = self.storage.get_unindexed_ids_within_window(
                        recent_limit=self.max_recent_index,
                        include_pinned=self.index_pinned_always,
                        limit=self.batch_size,
                    )
                    if unindexed_ids:
                        start_time = time.time()
                        self.status_text = f"{self.indexed_in_window}/{self.window_total}"
                        print(
                            f"[Neural Engine] Indexing {len(unindexed_ids)} clip(s)... ({self.indexed_in_window}/{self.window_total})"
                        )

                        indexer.index_clips(unindexed_ids)
                        elapsed = time.time() - start_time
                        self.indexed_in_window, self.window_total = self.storage.get_neural_window_totals(
                            recent_limit=self.max_recent_index,
                            include_pinned=self.index_pinned_always,
                        )
                        self.status_text = (
                            "ready"
                            if self.indexed_in_window >= self.window_total
                            else f"{self.indexed_in_window}/{self.window_total}"
                        )
                        print(f"[Neural Engine] Batch done in {elapsed:.2f}s — now {self.status_text}")
                        self._wait_for_next_work(max(0.1, min(0.5, elapsed)))
                        continue

                self.status_text = "ready"
                self._wait_for_next_work(self._compute_wait_timeout(time.time()))
            except Exception as e:
                self.status_text = "error"
                print(f"[Neural Engine] ERROR: {e}")
                self._wait_for_next_work(1.0)

    def _create_indexer(self):
        return NeuralIndexer(
            self.storage,
            self.model,
            similarity_threshold=self.similarity_threshold,
            max_neighbors=self.max_neighbors,
            lexical_prefix_boost=self.lexical_prefix_boost,
            lexical_word_boost=self.lexical_word_boost,
        )

    def _compute_wait_timeout(self, now: float) -> float:
        pending_since = self._worker.state._pending_since
        if pending_since is None or pending_since == 0.0:
            return 1.0
        deadline = pending_since + self.pending_flush_interval_seconds
        return max(0.0, deadline - now)

    def _wait_for_next_work(self, timeout: float):
        self._wake_event.wait(timeout=max(0.0, timeout))
        self._wake_event.clear()

    def _index_clips(self, clip_ids: List[int]):
        self._create_indexer().index_clips(clip_ids)

    def enqueue_new_clip(self, clip_id: int):
        self._worker.enqueue_new_clip(clip_id)
        self._wake_event.set()

    def enqueue_priority_reindex(self, clip_id: int):
        self._worker.enqueue_priority_reindex(clip_id)
        self._wake_event.set()

    def stop(self):
        self.is_running = False
        self._wake_event.set()
        # Do not join thread here, let it die gracefully

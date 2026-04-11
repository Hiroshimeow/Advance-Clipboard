import json
import os
import threading
import time
import numpy as np
from typing import List, Dict, Any, Tuple
import logging

try:
    from sentence_transformers import SentenceTransformer, util
except ImportError:
    SentenceTransformer = None
    util = None


class NeuralEngine(threading.Thread):
    def __init__(self, storage, config_path: str):
        super().__init__(name="NeuralEngine", daemon=True)
        self.storage = storage
        self.config_path = config_path
        self.model = None
        self.is_running = True
        self.batch_size = 2  # Giảm batch_size xuống 2 để giảm lag
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

        while self.is_running:
            try:
                self.indexed_in_window, self.window_total = self.storage.get_neural_window_totals(
                    recent_limit=self.max_recent_index,
                    include_pinned=self.index_pinned_always,
                )
                if self.window_total == 0:
                    self.status_text = "ready"
                    time.sleep(3)
                    continue

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

                    self._index_clips(unindexed_ids)
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
                    # Yield CPU to UI thread after each batch
                    time.sleep(max(0.5, elapsed))
                else:
                    self.status_text = "ready"
                    time.sleep(5)
            except Exception as e:
                self.status_text = "error"
                print(f"[Neural Engine] ERROR: {e}")
                time.sleep(10)

    def _index_clips(self, clip_ids: List[int]):
        if not self.model:
            return
        clips = []
        for cid in clip_ids:
            data = self.storage.get_clip_by_id(cid)
            if data and data.get("type") == "text":
                content = data.get("content", "")[:500]
                if content.strip():
                    clips.append((cid, content))
        if not clips:
            for cid in clip_ids:
                self.storage.save_vector(cid, b"")
            return
        contents = [c[1] for c in clips]
        t0 = time.time()
        embeddings = self.model.encode(contents, convert_to_numpy=True)
        print(f"[Neural Engine]   encode() took {time.time()-t0:.3f}s for {len(contents)} clip(s)")

        for i, (cid, _) in enumerate(clips):
            vec = embeddings[i].astype(np.float32)
            self.storage.save_vector(cid, vec.tobytes())

        time.sleep(0.1)  # Yield GIL to UI thread

        t1 = time.time()
        for i, (cid, _) in enumerate(clips):
            vec = embeddings[i].astype(np.float32)
            self._compute_and_save_links(cid, vec)
            time.sleep(0.05)  # Yield between each link computation
        print(f"[Neural Engine]   links took {time.time()-t1:.3f}s")

    def _compute_and_save_links(self, source_id: int, source_vec: np.ndarray):
        all_indexed = self.storage.get_all_clip_ids_with_vectors(limit=500)
        if not all_indexed:
            return

        source_data = self.storage.get_clip_by_id(source_id)
        source_content = (source_data.get("content", "") if source_data else "").strip()

        target_ids = []
        target_vecs = []
        target_contents = []

        for tid in all_indexed:
            if tid == source_id:
                continue
            v_bytes = self.storage.get_vector(tid)
            if v_bytes:
                target_ids.append(tid)
                target_vecs.append(np.frombuffer(v_bytes, dtype=np.float32))
                # Get content for lexical boosting
                t_data = self.storage.get_clip_by_id(tid)
                t_content = (t_data.get("content", "") if t_data else "").strip()
                target_contents.append(t_content)
        
        if not target_vecs:
            return

        # Yield to OS before matrix multiplication
        time.sleep(0.01)

        similarities = util.cos_sim(source_vec, np.array(target_vecs))[0]
        links = []
        
        for i, semantic_score in enumerate(similarities):
            # Base semantic threshold
            base_score = float(semantic_score)
            final_score = base_score
            
            # Lexical boosting
            t_content = target_contents[i]
            
            # 1. Prefix match (very strong indicator for commands like /acp spawn)
            if len(source_content) >= 10 and len(t_content) >= 10:
                if source_content[:10].lower() == t_content[:10].lower():
                    final_score += self.lexical_prefix_boost
            
            # 2. Long word match
            source_words = set(w.lower() for w in source_content.split() if len(w) > 6)
            target_words = set(w.lower() for w in t_content.split() if len(w) > 6)
            if source_words.intersection(target_words):
                final_score += self.lexical_word_boost

            if final_score > self.similarity_threshold:
                # Cap maximum score to 1.0
                final_score = min(1.0, final_score)
                links.append((source_id, int(target_ids[i]), float(final_score)))
                
        if links:
            links.sort(key=lambda x: x[2], reverse=True)
            self.storage.save_links(links[:self.max_neighbors])

    def stop(self):
        self.is_running = False
        # Do not join thread here, let it die gracefully

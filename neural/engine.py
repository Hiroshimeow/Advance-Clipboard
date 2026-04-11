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
        self.batch_size = 5
        self.max_legacy = 100
        self._load_config()

    def _load_config(self):
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r') as f:
                config = json.load(f)
                self.max_legacy = config.get("max_legacy_index", 100)
        else:
            with open(self.config_path, 'w') as f:
                json.dump({"max_legacy_index": 100}, f)

    def run(self):
        if SentenceTransformer:
            try:
                self.model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
                logging.info("Neural Engine: Model loaded on CPU.")
            except Exception as e:
                logging.error(f"Neural engine: Failed to load AI model: {e}")
                return

        while self.is_running:
            try:
                unindexed_ids = self.storage.get_unindexed_clip_ids(self.batch_size)
                if unindexed_ids:
                    self._index_clips(unindexed_ids)
                time.sleep(10) 
            except Exception as e:
                logging.error(f"Neural engine error: {e}")
                time.sleep(20)

    def _index_clips(self, clip_ids: List[int]):
        if not self.model: return
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
        embeddings = self.model.encode(contents, convert_to_numpy=True)
        for i, (cid, _) in enumerate(clips):
            vec = embeddings[i].astype(np.float32)
            self.storage.save_vector(cid, vec.tobytes())
            self._compute_and_save_links(cid, vec)

    def _compute_and_save_links(self, source_id: int, source_vec: np.ndarray):
        all_indexed = self.storage.get_all_clip_ids_with_vectors(limit=500)
        if not all_indexed: return
        target_ids = []
        target_vecs = []
        for tid in all_indexed:
            if tid == source_id: continue
            v_bytes = self.storage.get_vector(tid)
            if v_bytes:
                target_ids.append(tid)
                target_vecs.append(np.frombuffer(v_bytes, dtype=np.float32))
        if not target_vecs: return
        similarities = util.cos_sim(source_vec, np.array(target_vecs))[0]
        links = []
        for i, score in enumerate(similarities):
            if score > 0.65:
                links.append((source_id, int(target_ids[i]), float(score)))
        if links:
            links.sort(key=lambda x: x[2], reverse=True)
            self.storage.save_links(links[:5])

    def stop(self):
        self.is_running = False

from __future__ import annotations

import time
from typing import List

import numpy as np
from sentence_transformers import util


class NeuralIndexer:
    def __init__(
        self,
        storage,
        model,
        *,
        similarity_threshold: float,
        max_neighbors: int,
        lexical_prefix_boost: float,
        lexical_word_boost: float,
    ):
        self.storage = storage
        self.model = model
        self.similarity_threshold = similarity_threshold
        self.max_neighbors = max_neighbors
        self.lexical_prefix_boost = lexical_prefix_boost
        self.lexical_word_boost = lexical_word_boost

    def index_clips(self, clip_ids: List[int]):
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
        embeddings = self.model.encode(contents, convert_to_numpy=True)

        for i, (cid, _) in enumerate(clips):
            vec = embeddings[i].astype(np.float32)
            self.storage.save_vector(cid, vec.tobytes())

        time.sleep(0.1)

        for i, (cid, _) in enumerate(clips):
            vec = embeddings[i].astype(np.float32)
            self.compute_and_save_links(cid, vec)
            time.sleep(0.05)

    def compute_and_save_links(self, source_id: int, source_vec: np.ndarray):
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
                t_data = self.storage.get_clip_by_id(tid)
                t_content = (t_data.get("content", "") if t_data else "").strip()
                target_contents.append(t_content)

        if not target_vecs:
            return

        time.sleep(0.01)
        similarities = util.cos_sim(source_vec, np.array(target_vecs))[0]
        links = []

        for i, semantic_score in enumerate(similarities):
            base_score = float(semantic_score)
            final_score = base_score
            t_content = target_contents[i]

            if len(source_content) >= 10 and len(t_content) >= 10:
                if source_content[:10].lower() == t_content[:10].lower():
                    final_score += self.lexical_prefix_boost

            source_words = set(w.lower() for w in source_content.split() if len(w) > 6)
            target_words = set(w.lower() for w in t_content.split() if len(w) > 6)
            if source_words.intersection(target_words):
                final_score += self.lexical_word_boost

            if final_score > self.similarity_threshold:
                final_score = min(1.0, final_score)
                links.append((source_id, int(target_ids[i]), float(final_score)))

        if links:
            links.sort(key=lambda x: x[2], reverse=True)
            self.storage.save_links(links[: self.max_neighbors])

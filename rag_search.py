"""
Lightweight local RAG-style retriever for clipboard search.

The goal is not to introduce a full LLM stack. Instead, we build a tiny
hybrid retriever that combines:
- token-based lexical matching
- character n-gram similarity for fuzzy/semantic-ish recall
- TF-IDF cosine similarity for relevance ranking

This keeps the app dependency-free and fast enough for an interactive
clipboard manager.
"""

from __future__ import annotations

import math
import re
import threading
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


TOKEN_RE = re.compile(r"[a-zA-Z0-9_]{2,}")
NGRAM_SIZE = 3


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().split())


def _tokenize(text: str) -> List[str]:
    return TOKEN_RE.findall(_normalize(text))


def _char_ngrams(text: str, n: int = NGRAM_SIZE) -> List[str]:
    compact = re.sub(r"\s+", " ", _normalize(text))
    if not compact:
        return []
    padded = f"  {compact}  "
    if len(padded) <= n:
        return [padded]
    return [padded[i : i + n] for i in range(len(padded) - n + 1)]


def _cosine_similarity(
    left: Counter[str], left_norm: float, right: Counter[str], right_norm: float
) -> float:
    if not left or not right or left_norm == 0.0 or right_norm == 0.0:
        return 0.0

    if len(left) > len(right):
        left, right = right, left
        left_norm, right_norm = right_norm, left_norm

    dot = sum(value * right.get(term, 0.0) for term, value in left.items())
    if dot <= 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


@dataclass
class IndexedDocument:
    clip_id: int
    search_text: str
    tokens: set[str]
    token_tf_idf: Counter[str]
    token_norm: float
    ngrams: set[str]
    ngram_tf_idf: Counter[str]
    ngram_norm: float


class LightRAGRetriever:
    """Dependency-free hybrid retriever with background rebuilds."""

    def __init__(self):
        self._cache: Dict[str, Tuple[int, List[IndexedDocument]]] = {}
        self._doc_freq: Dict[str, Tuple[Counter[str], Counter[str]]] = {}
        self._indexed_ids: Dict[str, set[int]] = {}
        self._rebuild_lock = threading.Lock()
        self._rebuilding = False

    def invalidate(self, namespace: Optional[str] = None) -> None:
        if namespace is None:
            self._cache.clear()
            self._doc_freq.clear()
            self._indexed_ids.clear()
            return
        self._cache.pop(namespace, None)
        self._doc_freq.pop(namespace, None)
        self._indexed_ids.pop(namespace, None)

    def add_record(self, namespace: str, record: Dict[str, Any]) -> None:
        """Incrementally add a single record to an existing index."""
        cached = self._cache.get(namespace)
        if not cached:
            return

        clip_id = int(record["id"])
        if namespace not in self._indexed_ids:
            self._indexed_ids[namespace] = set()
        if clip_id in self._indexed_ids[namespace]:
            return

        revision, documents = cached
        token_doc_freq, ngram_doc_freq = self._doc_freq.get(
            namespace, (Counter(), Counter())
        )

        search_text = self._build_search_text(record)
        tokens = _tokenize(search_text)
        ngrams = _char_ngrams(search_text)

        token_doc_freq.update(set(tokens))
        ngram_doc_freq.update(set(ngrams))

        total_docs = max(len(documents) + 1, 1)
        token_tf_idf = self._tf_idf(tokens, token_doc_freq, total_docs)
        ngram_tf_idf = self._tf_idf(ngrams, ngram_doc_freq, total_docs)

        documents.append(
            IndexedDocument(
                clip_id=clip_id,
                search_text=_normalize(search_text),
                tokens=set(tokens),
                token_tf_idf=token_tf_idf,
                token_norm=math.sqrt(sum(v * v for v in token_tf_idf.values())),
                ngrams=set(ngrams),
                ngram_tf_idf=ngram_tf_idf,
                ngram_norm=math.sqrt(sum(v * v for v in ngram_tf_idf.values())),
            )
        )
        self._cache[namespace] = (revision + 1, documents)
        self._doc_freq[namespace] = (token_doc_freq, ngram_doc_freq)
        self._indexed_ids[namespace].add(clip_id)

    def rebuild_async(
        self,
        namespace: str,
        revision: int,
        records: Sequence[Dict[str, Any]],
        on_done=None,
    ) -> None:
        """Build index in background thread. Search uses old index until done.
        on_done is called (in background thread) when rebuild completes."""
        if self._rebuilding:
            return  # Already rebuilding, skip

        def _worker():
            self._rebuilding = True
            try:
                import time

                t0 = time.time()
                print(
                    f"[RAG] Background rebuild starting for '{namespace}' ({len(records)} records)..."
                )
                documents, token_doc_freq, ngram_doc_freq = self._build_index(records)
                with self._rebuild_lock:
                    self._cache[namespace] = (revision, documents)
                    self._doc_freq[namespace] = (token_doc_freq, ngram_doc_freq)
                    self._indexed_ids[namespace] = {d.clip_id for d in documents}
                print(
                    f"[RAG] Background rebuild done in {time.time() - t0:.1f}s ({len(documents)} docs)"
                )
                if on_done:
                    on_done()
            finally:
                self._rebuilding = False

        threading.Thread(target=_worker, daemon=True, name="RAG-Rebuild").start()

    def search(
        self,
        *,
        namespace: str,
        revision: int,
        records: Sequence[Dict[str, Any]],
        query: str,
        limit: int,
        lexical_ids: Optional[Iterable[int]] = None,
    ) -> List[int]:
        normalized_query = _normalize(query)
        if not normalized_query:
            return []

        # NEVER rebuild synchronously — use whatever index we have
        documents = self._get_index(namespace)
        if not documents:
            # No index at all — fall back to empty (rebuild_async should be called separately)
            return []

        lexical_boost_ids = set(lexical_ids or [])
        query_tokens = set(_tokenize(normalized_query))
        query_token_counter = Counter(_tokenize(normalized_query))
        query_ngram_counter = Counter(_char_ngrams(normalized_query))
        query_token_norm = math.sqrt(
            sum(value * value for value in query_token_counter.values())
        )
        query_ngram_norm = math.sqrt(
            sum(value * value for value in query_ngram_counter.values())
        )

        scored: List[Tuple[float, int]] = []
        phrase = normalized_query

        for doc in documents:
            token_overlap = (
                len(query_tokens & doc.tokens) / len(query_tokens)
                if query_tokens
                else 0.0
            )
            lexical_bonus = 0.18 if doc.clip_id in lexical_boost_ids else 0.0
            phrase_bonus = 0.28 if phrase and phrase in doc.search_text else 0.0
            token_cosine = _cosine_similarity(
                query_token_counter, query_token_norm, doc.token_tf_idf, doc.token_norm
            )
            ngram_cosine = _cosine_similarity(
                query_ngram_counter, query_ngram_norm, doc.ngram_tf_idf, doc.ngram_norm
            )
            fuzzy_overlap = (
                len(set(query_ngram_counter) & doc.ngrams)
                / len(set(query_ngram_counter))
                if query_ngram_counter
                else 0.0
            )

            score = (
                lexical_bonus
                + phrase_bonus
                + (token_overlap * 0.36)
                + (token_cosine * 0.32)
                + (ngram_cosine * 0.22)
                + (fuzzy_overlap * 0.10)
            )

            if score > 0.02:
                scored.append((score, doc.clip_id))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [clip_id for _, clip_id in scored[:limit]]

    def _get_index(self, namespace: str) -> List[IndexedDocument]:
        """Get current index without triggering rebuild."""
        with self._rebuild_lock:
            cached = self._cache.get(namespace)
            return cached[1] if cached else []

    def has_index(self, namespace: str) -> bool:
        return namespace in self._cache

    def _ensure_index(
        self,
        namespace: str,
        revision: int,
        records: Sequence[Dict[str, Any]],
    ) -> List[IndexedDocument]:
        """Legacy: only used by rebuild_async internally."""
        cached = self._cache.get(namespace)
        if cached and cached[0] == revision:
            return cached[1]

        documents, token_doc_freq, ngram_doc_freq = self._build_index(records)
        self._cache[namespace] = (revision, documents)
        self._doc_freq[namespace] = (token_doc_freq, ngram_doc_freq)
        self._indexed_ids[namespace] = {d.clip_id for d in documents}
        return documents

    def _build_index(
        self, records: Sequence[Dict[str, Any]]
    ) -> Tuple[List[IndexedDocument], Counter[str], Counter[str]]:
        prepared: List[Tuple[Dict[str, Any], List[str], List[str]]] = []
        token_doc_freq: Counter[str] = Counter()
        ngram_doc_freq: Counter[str] = Counter()

        for record in records:
            search_text = self._build_search_text(record)
            tokens = _tokenize(search_text)
            ngrams = _char_ngrams(search_text)
            prepared.append((record, tokens, ngrams))

            token_doc_freq.update(set(tokens))
            ngram_doc_freq.update(set(ngrams))

        total_docs = max(len(prepared), 1)
        documents: List[IndexedDocument] = []

        for record, tokens, ngrams in prepared:
            token_tf_idf = self._tf_idf(tokens, token_doc_freq, total_docs)
            ngram_tf_idf = self._tf_idf(ngrams, ngram_doc_freq, total_docs)

            documents.append(
                IndexedDocument(
                    clip_id=int(record["id"]),
                    search_text=_normalize(self._build_search_text(record)),
                    tokens=set(tokens),
                    token_tf_idf=token_tf_idf,
                    token_norm=math.sqrt(
                        sum(value * value for value in token_tf_idf.values())
                    ),
                    ngrams=set(ngrams),
                    ngram_tf_idf=ngram_tf_idf,
                    ngram_norm=math.sqrt(
                        sum(value * value for value in ngram_tf_idf.values())
                    ),
                )
            )

        return documents, token_doc_freq, ngram_doc_freq

    @staticmethod
    def _build_search_text(record: Dict[str, Any]) -> str:
        parts = [
            str(record.get("content", "")),
            str(record.get("tag", "")),
            str(record.get("group_name", "")),
            str(record.get("type", "")),
        ]
        return " \n ".join(part for part in parts if part)

    @staticmethod
    def _tf_idf(
        terms: Sequence[str], doc_freq: Counter[str], total_docs: int
    ) -> Counter[str]:
        counts = Counter(terms)
        weights: Counter[str] = Counter()
        total_terms = max(sum(counts.values()), 1)

        for term, count in counts.items():
            tf = count / total_terms
            idf = math.log((1 + total_docs) / (1 + doc_freq[term])) + 1.0
            weights[term] = tf * idf

        return weights

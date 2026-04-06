# Future Improvements

This file tracks deferred improvements for `Advance-Clipboard`.

## Search Roadmap

- Keep current default as `Hybrid`:
  lexical SQL search + lightweight local RAG reranking.
- Add search mode toggle:
  `Exact`, `Hybrid`, `Semantic`.
- Add search diagnostics:
  show why a result matched, score source, and ranking breakdown.
- Add benchmark script against real `clipboard.db` data:
  measure index build time, query latency, and memory usage.

## Indexing And Performance

- Persist the light RAG index to disk instead of rebuilding in memory.
- Incrementally update the search index per clip change instead of full invalidation.
- Add a corpus size threshold:
  keep history semantic search limited or sampled when the database grows large.
- Split indexing strategy by clip type:
  text clips indexed semantically, image clips kept mostly lexical/metadata-based.

## Embeddings

- Add optional local embeddings mode with a small model:
  `sentence-transformers/all-MiniLM-L6-v2` is the first candidate.
- Store embeddings in SQLite or a sidecar file for fast reload.
- Use hybrid ranking:
  lexical score + dense embedding similarity + metadata boosts.
- Add background indexing so first app launch stays responsive.

## LLM Features

- Add optional LLM-assisted query rewriting:
  expand vague queries into better search terms.
- Add optional result summarization for large clipboard sets.
- Add optional "find the clip about ..." natural-language intent search.
- Add optional auto-tagging / auto-grouping suggestions for pinned clips.
- Keep all LLM features strictly opt-in and isolated from the default fast path.

## UX

- Add a visual indicator for the active search mode.
- Add a settings panel for search behavior and resource limits.
- Add a "recent queries" dropdown.
- Add keyboard shortcut to cycle search modes.

## Query History And Search Optimization

- Add query history tracking with timestamps, hit counts, and last-selected result.
- Prioritize frequently used searches and frequently selected results.
- Demote rarely used searches over time with decay-based ranking.
- Detect duplicate or near-duplicate queries and merge them into a single normalized record.
- Auto-clean stale query history entries that were never useful.
- Add optional numeric shortcuts / ranking labels for top recurring searches.
- Add lightweight personalization:
  same query can prefer different results based on actual past user choices.
- Add stable ranking numbers for common searches:
  top patterns can be assigned visible rank IDs for quick recall and debugging.
- Add filter pipelines for search intent:
  exact-first, fuzzy, semantic, tag-first, group-first, and history-only / pinned-only.
- Add query normalization rules:
  lowercase, trim, whitespace collapse, command alias expansion, and typo folding.
- Add query deduplication policies:
  exact duplicate removal, near-duplicate merge review, and manual pinning of canonical searches.
- Add usefulness scoring for saved searches:
  combine query frequency, result click-through, and time-decay into a single priority score.

## Safety And Operability

- Add tests for hybrid ranking quality and regression cases.
- Add tests for search behavior on multilingual clipboard content.
- Add logging for slow queries and index rebuilds.
- Add a safe fallback to pure SQL search if semantic indexing fails.

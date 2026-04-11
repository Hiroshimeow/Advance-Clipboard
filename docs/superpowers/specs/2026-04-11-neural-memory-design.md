# Neural Memory Sidecar Spec

## Overview
A background "Neural Memory" system for `Advance-Clipboard` that visualizes relationships between clips using AI embeddings and a D3.js force-directed graph.

## Architecture
- **Engine (CPU-only):** Uses `all-MiniLM-L6-v2` via `sentence-transformers` to compute vectors.
- **Sync:** Bidirectional search and focus sync between the main UI and the Sidecar window.
- **UI:** Matrix-style (neon green/black) interactive graph view.
- **Background Worker:** Idle-priority thread for embedding generation.

## Data Schema
Adds two tables to `clipboard.db`:
1. `neural_vectors`: `clip_id (INT)`, `vector (BLOB)`
2. `neural_links`: `source_id (INT)`, `target_id (INT)`, `weight (FLOAT)`

## Features
- **Legacy Indexing:** Configurable limit for past clips.
- **Live Indexing:** Automatic embedding generation for new clips (batched every 10).
- **Galaxy Search:** Graph rotates and zooms into relevant node clusters on query.
- **Navigation:** Click node -> main UI jump to clip.

## Environment
- Managed via `uv`.
- Dependencies: `sentence-transformers`, `numpy`, `PyQt6-WebEngine`.

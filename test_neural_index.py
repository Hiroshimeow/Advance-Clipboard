"""
test_neural_index.py — Test Neural Engine indexing on REAL DB data.
Safe: does NOT delete or modify existing clips. Only adds vectors/links.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Suppress HF warnings
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

from storage import get_storage


def main():
    store = get_storage()

    clip_count = store.get_clip_count()
    print(f"[DB] Total clips: {clip_count}")

    if clip_count == 0:
        print("[SKIP] No clips in DB. Run test_restore_db.py first.")
        return

    # Check bounded neural window instead of the whole DB
    config_path = os.path.join(os.path.dirname(__file__), "neural", "config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = __import__("json").load(f)
    except Exception:
        cfg = {"max_recent_index": 200, "index_pinned_always": True}
        
    recent_limit = int(cfg.get("max_recent_index", cfg.get("max_legacy_index", 100)))
    include_pinned = bool(cfg.get("index_pinned_always", True))

    indexed_in_window, total_in_window = store.get_neural_window_totals(
        recent_limit=recent_limit,
        include_pinned=include_pinned,
    )
    unindexed = store.get_unindexed_ids_within_window(
        recent_limit=recent_limit,
        include_pinned=include_pinned,
        limit=999999,
    )
    indexed = store.get_all_clip_ids_with_vectors(limit=999999)
    print(f"[Neural] Window indexed: {indexed_in_window}/{total_in_window}")
    print(f"[Neural] Window unindexed: {len(unindexed)}")

    if not unindexed:
        print("[OK] All clips in bounded window already indexed.")
        # Show existing links
        nodes, links = store.get_neural_data(indexed[:50])
        print(f"\n[Graph] Nodes: {len(nodes)}, Links: {len(links)}")
        for l in links[:10]:
            print(f"  {l['source_id']} -> {l['target_id']} (w: {l['weight']:.2f})")
        return

    # Load the model and index a small batch
    print("\n[Model] Loading sentence-transformers model...")
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("[FAIL] sentence-transformers not installed!")
        return

    config_path = os.path.join(os.path.dirname(__file__), "neural", "config.json")
    from neural.engine import NeuralEngine

    engine = NeuralEngine(store, config_path)

    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        engine.model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
    print("[Model] Loaded OK.")

    # Index first 10 unindexed clips inside bounded window
    batch = unindexed[:100]
    print(f"\n[Index] Processing {len(batch)} clips...")
    t0 = time.time()
    engine._index_clips(batch)
    elapsed = time.time() - t0
    print(f"[Index] Done in {elapsed:.2f}s")

    # Check results
    indexed_now = store.get_all_clip_ids_with_vectors(limit=999999)
    indexed_in_window_now, total_in_window_now = store.get_neural_window_totals(
        recent_limit=recent_limit,
        include_pinned=include_pinned,
    )
    print(f"[Neural] Indexed now in window: {indexed_in_window_now}/{total_in_window_now}")
    print(f"[Neural] Total indexed rows now: {len(indexed_now)}")

    # Show graph data
    sample_ids = indexed_now[:500]
    nodes, links = store.get_neural_data(sample_ids)
    print(f"\n[Graph] Nodes: {len(nodes)}, Links: {len(links)}")

    if links:
        print("\n[Links] Top semantic connections:")
        sorted_links = sorted(links, key=lambda l: l["weight"], reverse=True)
        for l in sorted_links[:10]:
            # Find node content
            src = next((n for n in nodes if n["id"] == l["source_id"]), None)
            tgt = next((n for n in nodes if n["id"] == l["target_id"]), None)
            src_txt = (src["content"][:40] + "...") if src else "?"
            tgt_txt = (tgt["content"][:40] + "...") if tgt else "?"
            print(f'  [{l["weight"]:.2f}] "{src_txt}" <-> "{tgt_txt}"')
    else:
        print("[WARN] No semantic links found. Threshold may be too high (>0.65)")

    print("\n[OK] Neural indexing test passed.")


if __name__ == "__main__":
    main()

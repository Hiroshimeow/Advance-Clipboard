import os
import sys
import time

# Ensure correct path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from storage import get_storage
from neural.engine import NeuralEngine


def run_test():
    store = get_storage()

    print("1. Clearing database for test...")
    store.clear_history()
    store.clear_pinned()

    from storage import _get_connection

    conn = _get_connection()
    conn.execute("DELETE FROM neural_vectors")
    conn.execute("DELETE FROM neural_links")
    conn.commit()

    print("2. Inserting 10 test clips...")
    clips_content = [
        "Python is a programming language.",
        "Java is also a programming language.",
        "React is a UI library for JavaScript.",
        "Vue is another JS framework.",
        "Python and Java are widely used.",
        "A recipe for apple pie: flour, sugar, apples.",
        "Banana bread recipe.",
        "SQL is used for databases.",
        "MongoDB is a NoSQL database.",
        "PostgreSQL is a relational DB.",
    ]
    ids = []
    for c in clips_content:
        cid, _ = store.add_clip("text", c)
        ids.append(cid)

    print(f"Inserted IDs: {ids}")

    print("3. Starting Neural Engine manually to force indexing...")
    import os
    from sentence_transformers import SentenceTransformer

    config_path = os.path.join(os.path.dirname(__file__), "neural", "config.json")
    engine = NeuralEngine(store, config_path)
    engine.model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
    engine._index_clips(ids)
    print("Indexing completed.")

    print("4. Fetching Neural Data for Graph...")
    nodes, links = store.get_neural_data(ids)
    print(f"Nodes retrieved: {len(nodes)}")
    for n in nodes:
        print(f" - [{n['id']}] {n['content']}")

    print(f"Links retrieved: {len(links)}")
    for l in links:
        print(f" - {l['source_id']} -> {l['target_id']} (w: {l['weight']:.2f})")

    # Validation
    node_ids = {n["id"] for n in nodes}
    invalid_links = [
        l
        for l in links
        if l["source_id"] not in node_ids or l["target_id"] not in node_ids
    ]
    if invalid_links:
        print(
            f"WARNING: Found {len(invalid_links)} invalid links pointing to non-existent nodes!"
        )
        for il in invalid_links:
            print(f"   Invalid: {il['source_id']} -> {il['target_id']}")
    else:
        print("SUCCESS: All links point to valid nodes.")

    # Assert that we actually found some semantic relationships
    assert len(links) > 0, "No neural links were generated! Check model/threshold."
    print(f"Verified: Found {len(links)} semantic links.")


if __name__ == "__main__":
    run_test()

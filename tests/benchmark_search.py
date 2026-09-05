import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from storage import ClipboardStorage, compute_hash
from storage import db as storage_db

ROW_COUNT = 25_000
ITERATIONS = 50
WARMUPS = 3
P95_THRESHOLD_MS = 25.0
QUERIES = (
    "proxy",
    "deploy server",
    "tag work",
    "literal 100%",
    "missing-token",
)
COMMON_QUERIES = {"proxy", "deploy server", "tag work"}


def percentile_95(values):
    ordered = sorted(values)
    index = max(0, int(len(ordered) * 0.95 + 0.999999) - 1)
    return ordered[index]


def seed_rows(storage):
    conn = storage_db.get_connection()
    rows = []
    for index in range(ROW_COUNT):
        content_parts = [f"clipboard row {index}"]
        if index % 5 == 0:
            content_parts.append("proxy")
        if index % 11 == 0:
            content_parts.append("deploy server")
        if index % 17 == 0:
            content_parts.append("literal 100%")
        content = " ".join(content_parts)
        tag = "work" if index % 7 == 0 else ""
        group_name = "work" if index % 13 == 0 else ""
        is_pinned = 1 if index % 10 == 0 else 0
        timestamp = f"2026-09-05T12:{(index // 60) % 60:02d}:{index % 60:02d}.{index:05d}"
        rows.append(
            (
                "text",
                content,
                compute_hash(content),
                tag,
                group_name,
                is_pinned,
                index if is_pinned else 0,
                timestamp if is_pinned else None,
                timestamp,
                timestamp,
            )
        )
    with conn:
        conn.executemany(
            """INSERT INTO clips
               (type, content, hash, tag, group_name, is_pinned, pin_order,
                pinned_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.execute("ANALYZE")


def measure(storage, query):
    for _ in range(WARMUPS):
        storage.search_history(query)
        storage.search_pinned(query)

    timings_ms = []
    for _ in range(ITERATIONS):
        started = time.perf_counter_ns()
        storage.search_history(query)
        storage.search_pinned(query)
        timings_ms.append((time.perf_counter_ns() - started) / 1_000_000)
    return {
        "median_ms": round(statistics.median(timings_ms), 3),
        "p95_ms": round(percentile_95(timings_ms), 3),
    }


def main():
    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "clipboard-benchmark.db")
        with patch.object(storage_db, "DB_FILE", db_path):
            storage_db._local.conn = None
            storage = ClipboardStorage()
            seed_rows(storage)
            for query in QUERIES:
                results[query] = measure(storage, query)
            conn = getattr(storage_db._local, "conn", None)
            if conn is not None:
                conn.close()
                storage_db._local.conn = None

    payload = {
        "machine": platform.platform(),
        "python": sys.version.split()[0],
        "rows": ROW_COUNT,
        "iterations": ITERATIONS,
        "queries": results,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))

    slow = {
        query: metrics
        for query, metrics in results.items()
        if query in COMMON_QUERIES and metrics["p95_ms"] > P95_THRESHOLD_MS
    }
    if slow:
        print(json.dumps({"slow_queries": slow}, indent=2, sort_keys=True), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

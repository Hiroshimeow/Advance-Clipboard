import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class ClipAddConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "clipboard.db"
        self.db_patch = patch("storage.db.DB_FILE", str(self.db_path))
        self.db_patch.start()

        import storage.db as db

        db._local = threading.local()
        from storage import ClipboardStorage

        self.storage = ClipboardStorage()

    def tearDown(self):
        import storage.db as db

        conn = getattr(db._local, "conn", None)
        if conn is not None:
            conn.close()
            db._local.conn = None
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_same_content_from_two_threads_is_stored_once(self):
        from storage.clips import ClipRepository
        import storage.db as db

        barrier = threading.Barrier(2)
        results = []
        errors = []
        lock = threading.Lock()

        def worker():
            try:
                barrier.wait(timeout=5)
                result = ClipRepository().add_clip("text", "same clipboard content")
                with lock:
                    results.append(result)
            except Exception as exc:  # captured for assertion in main thread
                with lock:
                    errors.append(exc)
            finally:
                conn = getattr(db._local, "conn", None)
                if conn is not None:
                    conn.close()
                    db._local.conn = None

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual([], errors)
        self.assertEqual(2, len(results))
        self.assertEqual(1, sum(1 for _, is_new, _ in results if is_new))
        self.assertEqual(1, len({clip_id for clip_id, _, _ in results}))

        conn = db.get_connection()
        count = conn.execute(
            "SELECT COUNT(*) FROM clips WHERE content = ?",
            ("same clipboard content",),
        ).fetchone()[0]
        self.assertEqual(1, count)


if __name__ == "__main__":
    unittest.main()

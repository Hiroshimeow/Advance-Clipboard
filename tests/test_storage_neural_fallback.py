import os
import sys
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

import storage


class StorageNeuralFallbackTests(unittest.TestCase):
    def test_fallback_repository_returns_safe_defaults(self):
        original_repo = storage.NeuralRepository
        original_error = storage._NEURAL_REPOSITORY_IMPORT_ERROR

        class _FallbackRepo:
            def get_all_clip_ids_with_vectors(self, limit=500):
                return []

            def get_neural_data(self, clip_ids):
                return [], []

        storage.NeuralRepository = _FallbackRepo
        storage._NEURAL_REPOSITORY_IMPORT_ERROR = ImportError(
            "synthetic neural import failure"
        )
        self.addCleanup(setattr, storage, "NeuralRepository", original_repo)
        self.addCleanup(
            setattr,
            storage,
            "_NEURAL_REPOSITORY_IMPORT_ERROR",
            original_error,
        )

        self.assertIsNotNone(storage.get_neural_support_error())
        repo = storage.NeuralRepository()
        self.assertEqual([], repo.get_all_clip_ids_with_vectors())
        self.assertEqual(([], []), repo.get_neural_data([1, 2]))


if __name__ == "__main__":
    unittest.main()

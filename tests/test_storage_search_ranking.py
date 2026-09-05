import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))


class StorageSearchRankingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "clipboard.db"
        patcher = patch("storage.db.DB_FILE", str(self.db_path))
        patcher.start()
        self.addCleanup(patcher.stop)

        import storage.db as db
        db._local = type("Local", (), {})()
        from storage import ClipboardStorage

        self.storage = ClipboardStorage()

    def tearDown(self):
        import storage.db as db

        conn = getattr(db._local, "conn", None)
        if conn is not None:
            conn.close()
            db._local.conn = None
        self.tmp.cleanup()

    def _insert_clip(self, content, updated_at, *, pinned=False, tag="", group="", clip_type="text"):
        clip_id, is_new = self.storage.add_clip(clip_type, content, tag)
        if pinned:
            self.storage.pin_clip(clip_id)
        self.storage.update_group(clip_id, group)
        conn = self.storage.clips.get_clip_by_id  # keep import side-effect simple
        import storage.db as db
        with db.transaction() as sql:
            sql.execute(
                "UPDATE clips SET updated_at = ?, tag = ?, group_name = ? WHERE id = ?",
                (updated_at.isoformat(), tag, group, clip_id),
            )
        return clip_id

    def test_recent_matching_history_clip_wins_tie(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        old_id = self._insert_clip("deploy server config alpha", base)
        recent_id = self._insert_clip("deploy server config beta", base + timedelta(minutes=5))

        rows = self.storage.search_history("deploy server", limit=10, ranked=False)

        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], recent_id)
        self.assertEqual(rows[1]["id"], old_id)

    def test_exact_match_beats_recent_partial_match(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        exact_id = self._insert_clip("token", base)
        self._insert_clip("token renewal script for production", base + timedelta(minutes=5))

        rows = self.storage.search_history("token", limit=10, ranked=False)

        self.assertEqual(rows[0]["id"], exact_id)

    def test_deterministic_relevance_tiers_beat_recency_across_tiers(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        exact_tag = self._insert_clip("body exact tag", base, tag="alpha beta")
        exact_content = self._insert_clip("alpha beta", base + timedelta(minutes=1))
        tag_prefix = self._insert_clip("body tag prefix", base + timedelta(minutes=2), tag="alpha beta tools")
        tag_contains = self._insert_clip("body tag contains", base + timedelta(minutes=3), tag="tools alpha x beta")
        content_prefix = self._insert_clip("alpha beta tools", base + timedelta(minutes=4))
        phrase_content = self._insert_clip("tools alpha beta deploy", base + timedelta(minutes=5))
        token_content = self._insert_clip("alpha tools beta", base + timedelta(minutes=6))
        group_match = self._insert_clip("unrelated group body", base + timedelta(minutes=7), group="alpha tools beta")
        general_match = self._insert_clip("alpha only", base + timedelta(minutes=8), group="beta only")

        rows = self.storage.search_history("alpha beta", limit=20, ranked=False)

        self.assertEqual(
            [row["id"] for row in rows],
            [
                exact_tag,
                exact_content,
                tag_prefix,
                tag_contains,
                content_prefix,
                phrase_content,
                token_content,
                group_match,
                general_match,
            ],
        )

    def test_same_tier_prefers_most_recent_use(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        old_id = self._insert_clip("abc", base)
        recent_id = self._insert_clip("abd", base + timedelta(minutes=5))

        rows = self.storage.search_history("a", limit=10, ranked=False)

        self.assertEqual([row["id"] for row in rows[:2]], [recent_id, old_id])

    def test_old_exact_content_survives_recent_candidate_window(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        exact_id = self._insert_clip("needle", base)
        for index in range(120):
            self._insert_clip(
                f"weak needle result {index}",
                base + timedelta(minutes=index + 1),
            )

        rows = self.storage.search_history("needle", limit=12, ranked=False)

        self.assertEqual(rows[0]["id"], exact_id)
        self.assertIn(exact_id, [row["id"] for row in rows])

    def test_old_tag_match_survives_recent_candidate_window(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        tag_id = self._insert_clip("old tagged body", base, tag="needle")
        for index in range(120):
            self._insert_clip(
                f"weak needle result {index}",
                base + timedelta(minutes=index + 1),
            )

        rows = self.storage.search_history("needle", limit=12, ranked=False)

        self.assertEqual(rows[0]["id"], tag_id)
        self.assertIn(tag_id, [row["id"] for row in rows])

    def test_case_equivalent_content_is_not_exact_tier(self):
        from storage import _lexical_tier

        row = {"content": "Needle", "tag": "", "group_name": ""}

        self.assertEqual(_lexical_tier(row, "needle", include_meta=True), 5)

    def test_collapsed_whitespace_content_is_not_exact_tier(self):
        from storage import _lexical_tier

        row = {"content": "needle   value", "tag": "", "group_name": ""}

        self.assertEqual(_lexical_tier(row, "needle value", include_meta=True), 5)

    def test_trimmed_case_insensitive_exact_tag_survives_recent_content_window(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        exact_tag_id = self._insert_clip("old exact tag body", base, tag="  Alpha Beta  ")
        for index in range(120):
            self._insert_clip(
                f"weak alpha beta content {index}",
                base + timedelta(minutes=index + 1),
            )

        rows = self.storage.search_history("alpha beta", limit=12, ranked=False)

        self.assertEqual(rows[0]["id"], exact_tag_id)
        self.assertIn(exact_tag_id, [row["id"] for row in rows])

    def test_internal_whitespace_tag_is_all_terms_not_exact(self):
        from storage import _lexical_tier

        row = {"content": "unrelated", "tag": "alpha   beta", "group_name": ""}

        self.assertEqual(_lexical_tier(row, "alpha beta", include_meta=True), 4)

    def test_exact_tag_with_internal_whitespace_query_survives_newer_tag_matches(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        exact_tag_id = self._insert_clip("old exact spaced tag", base, tag="  Alpha   Beta  ")
        for index in range(120):
            self._insert_clip(
                f"newer tagged body {index}",
                base + timedelta(minutes=index + 1),
                tag=f"alpha beta weak {index}",
            )

        rows = self.storage.search_history("alpha   beta", limit=12, ranked=False)

        self.assertEqual(rows[0]["id"], exact_tag_id)
        self.assertIn(exact_tag_id, [row["id"] for row in rows])

    def test_duplicate_add_clip_persists_recency_used_by_search(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        old_id = self._insert_clip("abc", base)
        recent_id = self._insert_clip("abd", base + timedelta(minutes=5))
        self.assertEqual(
            [row["id"] for row in self.storage.search_history("a", limit=10)[:2]],
            [recent_id, old_id],
        )

        persisted_id, is_new = self.storage.add_clip("text", "abc")
        rows = self.storage.search_history("a", limit=10)

        self.assertFalse(is_new)
        self.assertEqual(persisted_id, old_id)
        self.assertEqual([row["id"] for row in rows[:2]], [old_id, recent_id])

    def test_history_search_includes_tag_and_group_metadata(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        tagged_id = self._insert_clip("unrelated body", base, tag="linux", group="workspace tools")

        tag_rows = self.storage.search_history("linux", limit=10, ranked=False)
        group_rows = self.storage.search_history("workspace", limit=10, ranked=False)

        self.assertEqual([row["id"] for row in tag_rows], [tagged_id])
        self.assertEqual([row["id"] for row in group_rows], [tagged_id])

    def test_tag_prefix_search_filters_to_tag_only_and_prefers_recent(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        older = self._insert_clip("body mentions proxy", base, tag="proxy")
        newer = self._insert_clip("unrelated body", base + timedelta(minutes=5), tag="proxy-tools")
        self._insert_clip("tag word only in content", base + timedelta(minutes=10), tag="")

        rows = self.storage.search_history("tag proxy", limit=10, ranked=False)

        self.assertEqual([row["id"] for row in rows], [newer, older])

    def test_tags_prefix_search_matches_partial_tag_keyword(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        tagged_id = self._insert_clip("unrelated body", base, tag="workspace-tools")
        self._insert_clip("workspace-tools appears only in body", base + timedelta(minutes=5), tag="")

        rows = self.storage.search_history("tags work", limit=10, ranked=False)

        self.assertEqual([row["id"] for row in rows], [tagged_id])

    def test_tag_colon_search_filters_to_tag_only(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        tagged_id = self._insert_clip("unrelated body", base, tag="work-tools")
        self._insert_clip("work-tools appears only in body", base + timedelta(minutes=5), tag="")

        rows = self.storage.search_history("tag:work", limit=10, ranked=False)

        self.assertEqual([row["id"] for row in rows], [tagged_id])

    def test_image_filter_variants_return_images_only_for_history_and_pinned(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        history_image = self._insert_clip("history.png", base, clip_type="image")
        self._insert_clip("image text only", base + timedelta(minutes=1))
        pinned_image = self._insert_clip("pinned.png", base + timedelta(minutes=2), pinned=True, clip_type="image")
        self._insert_clip("img text only", base + timedelta(minutes=3), pinned=True)

        variants = ["type: image", "type image", "type: img", "type img", "type:image", "type:img"]
        for query in variants:
            with self.subTest(query=query):
                self.assertEqual([row["id"] for row in self.storage.search_history(query)], [history_image])
                self.assertEqual([row["id"] for row in self.storage.search_pinned(query)], [pinned_image])

    def test_filter_search_supports_offset_paging_for_tag_and_image(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        history_tagged = []
        history_images = []
        pinned_tagged = []
        pinned_images = []
        for idx in range(15):
            history_tagged.append(
                self._insert_clip(
                    f"history tagged {idx}",
                    base + timedelta(minutes=idx),
                    tag="work",
                )
            )
            history_images.append(
                self._insert_clip(
                    f"history-{idx}.png",
                    base + timedelta(hours=1, minutes=idx),
                    clip_type="image",
                )
            )
            pinned_tagged.append(
                self._insert_clip(
                    f"pinned tagged {idx}",
                    base + timedelta(hours=2, minutes=idx),
                    pinned=True,
                    tag="work",
                )
            )
            pinned_images.append(
                self._insert_clip(
                    f"pinned-{idx}.png",
                    base + timedelta(hours=3, minutes=idx),
                    pinned=True,
                    clip_type="image",
                )
            )

        self.assertEqual(
            [row["id"] for row in self.storage.search_history("tag:work", limit=3, offset=12)],
            list(reversed(history_tagged))[12:15],
        )
        self.assertEqual(
            [row["id"] for row in self.storage.search_history("type img", limit=3, offset=12)],
            list(reversed(history_images))[12:15],
        )
        self.assertEqual(
            [row["id"] for row in self.storage.search_pinned("tag:work", limit=3, offset=12)],
            list(reversed(pinned_tagged))[12:15],
        )
        self.assertEqual(
            [row["id"] for row in self.storage.search_pinned("type img", limit=3, offset=12)],
            list(reversed(pinned_images))[12:15],
        )

    def test_image_filter_preserves_history_visibility_for_reused_pinned_clip(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        clip_id = self._insert_clip("reused.png", base, pinned=True, clip_type="image")
        import storage.db as db
        with db.transaction() as sql:
            sql.execute(
                "UPDATE clips SET pinned_at = ?, updated_at = ? WHERE id = ?",
                (base.isoformat(), (base + timedelta(minutes=1)).isoformat(), clip_id),
            )

        rows = self.storage.search_history("type:image")

        self.assertEqual([row["id"] for row in rows], [clip_id])

    def test_plain_image_query_keeps_free_text_semantics(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        text_id = self._insert_clip("image deployment notes", base)
        self._insert_clip("photo.png", base + timedelta(minutes=1), clip_type="image")

        rows = self.storage.search_history("image", limit=10, ranked=False)

        self.assertIn(text_id, [row["id"] for row in rows])

    def test_trigram_substring_and_cross_column_terms_preserve_search_semantics(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        substring_id = self._insert_clip("production deploy config", base)
        cross_column_id = self._insert_clip("deploy body", base + timedelta(minutes=1), group="server ops")

        self.assertIn(substring_id, [row["id"] for row in self.storage.search_history("ploy", limit=10)])
        self.assertIn(cross_column_id, [row["id"] for row in self.storage.search_history("deploy server", limit=10)])

    def test_short_term_fallback_preserves_recency(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        old_id = self._insert_clip("abc", base)
        recent_id = self._insert_clip("abd", base + timedelta(minutes=5))

        rows = self.storage.search_history("a", limit=10, ranked=False)

        self.assertEqual([row["id"] for row in rows[:2]], [recent_id, old_id])

    def test_literal_like_wildcards_do_not_match_everything(self):
        base = datetime(2026, 1, 1, 12, 0, 0)
        literal_id = self._insert_clip("literal 100% proxy_token", base)
        self._insert_clip("ordinary unrelated clip", base + timedelta(minutes=5))

        rows = self.storage.search_history("%", limit=10, ranked=False)

        self.assertEqual([row["id"] for row in rows], [literal_id])


if __name__ == "__main__":
    unittest.main()

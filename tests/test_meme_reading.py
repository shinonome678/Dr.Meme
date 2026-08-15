from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database import MemeDatabase


class MemeReadingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_context = tempfile.TemporaryDirectory()
        self.db = MemeDatabase(Path(self.tmp_context.name) / "memes.db")
        self.db.initialize()

    def tearDown(self) -> None:
        self.tmp_context.cleanup()

    def test_add_defaults_empty_reading_to_keyword(self) -> None:
        meme = self.db.add_meme(
            guild_id=1,
            keyword="何見てんだよ",
            match_type="partial",
            image_path="images/1.png",
            created_by=10,
            reading="",
        )

        self.assertEqual(meme.reading, "何見てんだよ")
        self.assertEqual(meme.voice_text, "何見てんだよ")

    def test_keyword_edit_updates_auto_reading(self) -> None:
        meme = self.db.add_meme(
            guild_id=1,
            keyword="old",
            match_type="partial",
            image_path="images/1.png",
            created_by=10,
            reading="",
        )

        updated = self.db.update_meme(
            guild_id=1,
            meme_id=meme.id,
            keyword="new",
            updated_by=20,
        )

        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.keyword, "new")
        self.assertEqual(updated.reading, "new")

    def test_keyword_edit_keeps_custom_reading(self) -> None:
        meme = self.db.add_meme(
            guild_id=1,
            keyword="old",
            match_type="partial",
            image_path="images/1.png",
            created_by=10,
            reading="custom",
        )

        updated = self.db.update_meme(
            guild_id=1,
            meme_id=meme.id,
            keyword="new",
            updated_by=20,
        )

        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.keyword, "new")
        self.assertEqual(updated.reading, "custom")

    def test_explicit_empty_reading_resets_to_keyword(self) -> None:
        meme = self.db.add_meme(
            guild_id=1,
            keyword="keyword",
            match_type="partial",
            image_path="images/1.png",
            created_by=10,
            reading="custom",
        )

        updated = self.db.update_meme(
            guild_id=1,
            meme_id=meme.id,
            reading="",
            updated_by=20,
        )

        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.reading, "keyword")


if __name__ == "__main__":
    unittest.main()

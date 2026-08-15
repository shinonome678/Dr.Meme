from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backends import ImagePayload
from config import Settings
from database import Meme
from karuta.manager import KarutaManager
from karuta.models import GameState, KarutaParticipant, VoiceStyle


def make_settings(tmp: Path) -> Settings:
    return Settings(
        discord_token=None,
        test_guild_id=None,
        backend="local",
        meme_editor_role="Dr.Meme",
        allow_everyone_to_edit=False,
        meme_cooldown_seconds=10,
        sync_global_commands=False,
        data_dir=tmp,
        images_dir=tmp / "images",
        db_path=tmp / "memes.db",
        supabase_url=None,
        supabase_service_role_key=None,
        supabase_bucket="memes",
        web_host="127.0.0.1",
        web_port=8080,
        public_base_url="http://127.0.0.1:8080",
        voicevox_base_url="http://127.0.0.1:50021",
        voicevox_excluded_style_ids=frozenset(),
        karuta_min_reaction_ms=0,
    )


def make_meme(meme_id: int, *, reading: str | None = None) -> Meme:
    return Meme(
        id=meme_id,
        guild_id=1,
        keyword=f"keyword-{meme_id}",
        match_type="partial",
        image_path=f"images/{meme_id}.png",
        created_by=1,
        created_at="2026-08-15T00:00:00Z",
        enabled=True,
        trigger_count=0,
        reading=reading,
    )


class FakeBackend:
    def __init__(self) -> None:
        self.memes = [make_meme(index) for index in range(1, 61)]
        self.updated: dict[int, tuple[str | None, int]] = {}

    async def list_karuta_candidates(self, *, guild_id: int) -> list[Meme]:
        return self.memes

    async def update_meme_readings(
        self,
        *,
        guild_id: int,
        changes: dict[int, tuple[str | None, int]],
    ) -> int:
        self.updated.update(changes)
        return len(changes)

    async def get_meme(self, *, guild_id: int, meme_id: int) -> Meme | None:
        return next((meme for meme in self.memes if meme.id == meme_id), None)

    async def count_search_memes(self, *, guild_id: int, query: str) -> int:
        return len(self.memes)

    async def search_memes(
        self,
        *,
        guild_id: int,
        query: str,
        limit: int,
        offset: int = 0,
    ) -> list[Meme]:
        return self.memes[offset : offset + limit]

    async def read_meme_image(self, meme: Meme) -> ImagePayload:
        return ImagePayload(data=b"image", content_type="image/png", filename="x.png")


class FakeVoicevox:
    async def check_available(self) -> bool:
        return True

    async def available_styles(self) -> list[VoiceStyle]:
        return [VoiceStyle("speaker", "style", 1)]

    async def synthesize_round(self, round_plan, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        intro = output_dir / f"round_{round_plan.round_no:02d}_intro.wav"
        keyword = output_dir / f"round_{round_plan.round_no:02d}_keyword.wav"
        intro.write_bytes(b"RIFF")
        keyword.write_bytes(b"RIFF")
        round_plan.intro_path = intro
        round_plan.keyword_path = keyword
        round_plan.audio_ready = True


class KarutaManagerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp_context = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_context.name)
        self.backend = FakeBackend()
        self.manager = KarutaManager(
            backend=self.backend,
            settings=make_settings(self.tmp),
            voicevox=FakeVoicevox(),
        )
        self.participants = [
            KarutaParticipant(10, "A", "https://example.com/a.png"),
            KarutaParticipant(20, "B", "https://example.com/b.png"),
        ]

    async def asyncTearDown(self) -> None:
        for session in self.manager.sessions.values():
            for task in (
                session.first_audio_task,
                session.background_audio_task,
                session.round_task,
                session.round_activation_task,
                session.round_timeout_task,
            ):
                if task and not task.done():
                    task.cancel()
        self.tmp_context.cleanup()

    async def create_session(self):
        session = await self.manager.create_game(
            guild_id=1,
            channel_id=100,
            organizer_id=10,
            participants=self.participants,
        )
        for round_plan in session.rounds:
            round_plan.audio_ready = True
        for player in session.players.values():
            player.connected = True
        return session

    async def test_board_and_round_plan(self) -> None:
        session = await self.create_session()
        self.assertEqual(len(session.board_ids), 50)
        self.assertEqual(len(session.rounds), 49)
        round_ids = {round_plan.meme_id for round_plan in session.rounds}
        self.assertNotIn(session.unread_meme_id, round_ids)

    async def test_initial_audio_ready_requires_only_first_round(self) -> None:
        session = await self.create_session()
        for round_plan in session.rounds:
            round_plan.audio_ready = False

        self.assertFalse(session.first_five_ready)
        session.rounds[0].audio_ready = True
        self.assertTrue(session.first_five_ready)

    async def test_all_ready_starts_without_waiting_for_image_loaded_flags(self) -> None:
        session = await self.create_session()
        session.state = GameState.LOBBY
        session.rounds[0].audio_ready = True
        for player in session.players.values():
            player.ready = True
            player.images_loaded = False

        await self.manager._maybe_start_game(session)

        self.assertEqual(session.state, GameState.COUNTDOWN)

    async def test_browser_tts_round_auto_activates(self) -> None:
        session = await self.create_session()
        round1 = session.rounds[0]
        round1.wait_ms = 0
        round1.tts_fallback = True
        session.current_round_index = 0

        with patch("karuta.manager.BROWSER_TTS_INTRO_SECONDS", 0):
            await self.manager._start_round(session, round1)
            await asyncio.sleep(0.01)

        self.assertEqual(session.state, GameState.ROUND_ACTIVE)
        self.assertIsNotNone(round1.active_started_at)

    async def test_inactive_round_times_out_and_advances(self) -> None:
        session = await self.create_session()
        round1 = session.rounds[0]
        round1.tts_fallback = True
        round1.wait_ms = 0
        session.current_round_index = 0

        with (
            patch("karuta.manager.BROWSER_TTS_INTRO_SECONDS", 0),
            patch("karuta.manager.ROUND_TIMEOUT_SECONDS", 0.01),
        ):
            await self.manager._start_round(session, round1)
            await asyncio.sleep(1.35)

        self.assertEqual(session.current_round_index, 1)
        self.assertEqual(session.current_round.round_no, 2)

    async def test_penalty_blocks_next_round_only(self) -> None:
        session = await self.create_session()
        player = session.players[10]
        round10 = session.rounds[9]
        session.current_round_index = 9
        await self.manager._start_round(session, round10)
        session.state = GameState.ROUND_ACTIVE
        wrong_id = next(meme_id for meme_id in session.remaining_ids if meme_id != round10.meme_id)
        await self.manager.handle_click(
            session,
            player,
            {"round_no": 10, "meme_id": wrong_id, "reaction_ms": 500},
        )

        round11 = session.rounds[10]
        session.current_round_index = 10
        await self.manager._start_round(session, round11)
        self.assertNotIn(10, round11.eligible_user_ids)

        round12 = session.rounds[11]
        session.current_round_index = 11
        await self.manager._start_round(session, round12)
        self.assertIn(10, round12.eligible_user_ids)

    async def test_all_mistake_finishes_game(self) -> None:
        session = await self.create_session()
        round1 = session.rounds[0]
        session.current_round_index = 0
        await self.manager._start_round(session, round1)
        session.state = GameState.ROUND_ACTIVE
        wrong_id = next(meme_id for meme_id in session.remaining_ids if meme_id != round1.meme_id)

        await self.manager.handle_click(
            session,
            session.players[10],
            {"round_no": 1, "meme_id": wrong_id, "reaction_ms": 500},
        )
        await self.manager.handle_click(
            session,
            session.players[20],
            {"round_no": 1, "meme_id": wrong_id, "reaction_ms": 600},
        )

        self.assertEqual(session.state, GameState.FINISHED)
        self.assertEqual(session.end_reason, "all_mistake")

    async def test_results_tie_break_by_average_reaction(self) -> None:
        session = await self.create_session()
        session.players[10].cards_won = 2
        session.players[10].reaction_times_ms.extend([900, 1000])
        session.players[20].cards_won = 2
        session.players[20].reaction_times_ms.extend([700, 800])

        rows = self.manager.calculate_results(session)
        self.assertEqual(rows[0].user_id, 20)
        self.assertEqual(rows[0].rank, 1)

    async def test_reading_priority_and_flush(self) -> None:
        meme = make_meme(999, reading="よみ")
        self.assertEqual(meme.voice_text, "よみ")

        session = await self.create_session()
        await self.manager.set_reading(
            session,
            session.players[10],
            {"meme_id": session.board_ids[0], "reading": "あとで読む"},
        )
        updated = await self.manager.flush_reading_changes(session)
        self.assertEqual(updated, 1)
        self.assertIn(session.board_ids[0], self.backend.updated)


if __name__ == "__main__":
    unittest.main()

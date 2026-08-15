from __future__ import annotations

import asyncio
import logging
import random
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backends import ImageNotFoundError, MemeBackend
from config import Settings
from database import Meme

from .models import (
    ClickSubmission,
    GameState,
    KarutaParticipant,
    PlayerState,
    ReadingChange,
    ResultRow,
    RoundPlan,
    VoiceStyle,
)
from .security import constant_time_equal, new_game_id, new_player_token
from .voicevox import VoicevoxClient, VoicevoxError


LOGGER = logging.getLogger(__name__)
MAX_PLAYERS = 10
MIN_PLAYERS = 2
BOARD_SIZE = 50
MAX_ROUNDS = 49
READING_MAX_LENGTH = 100
MIDGAME_CARD_COUNT = 25
ADJUDICATION_SECONDS = 0.75


class KarutaError(Exception):
    """Base karuta error."""


class ActiveGameExistsError(KarutaError):
    """A guild already has an active karuta room."""


class NotEnoughMemesError(KarutaError):
    """Memelist does not have enough valid memes."""


class VoicevoxUnavailableError(KarutaError):
    """VOICEVOX is unavailable."""


class InvalidTokenError(KarutaError):
    """Game token is invalid."""


@dataclass
class KarutaSession:
    game_id: str
    guild_id: int
    channel_id: int
    organizer_id: int
    players: dict[int, PlayerState]
    audio_root: Path
    match_no: int = 0
    state: GameState = GameState.PREPARING
    memes_by_id: dict[int, Meme] = field(default_factory=dict)
    board_ids: list[int] = field(default_factory=list)
    remaining_ids: set[int] = field(default_factory=set)
    unread_meme_id: int | None = None
    rounds: list[RoundPlan] = field(default_factory=list)
    current_round_index: int = -1
    reading_changes: dict[int, ReadingChange] = field(default_factory=dict)
    result_rows: list[ResultRow] = field(default_factory=list)
    end_reason: str | None = None
    reading_update_count: int = 0
    websockets: dict[int, set[Any]] = field(default_factory=dict)
    arrival_seq: int = 0
    first_audio_task: asyncio.Task[None] | None = None
    background_audio_task: asyncio.Task[None] | None = None
    round_task: asyncio.Task[None] | None = None
    result_notified: bool = False

    @property
    def current_round(self) -> RoundPlan | None:
        if 0 <= self.current_round_index < len(self.rounds):
            return self.rounds[self.current_round_index]
        return None

    @property
    def first_five_ready(self) -> bool:
        return bool(self.rounds) and self.rounds[0].audio_ready

    @property
    def audio_dir(self) -> Path:
        return self.audio_root / self.game_id / f"match_{self.match_no:02d}"

    def player_for_token(self, token: str) -> PlayerState | None:
        for player in self.players.values():
            if constant_time_equal(player.token, token):
                return player
        return None

    def reading_for_meme(self, meme: Meme) -> str:
        change = self.reading_changes.get(meme.id)
        if change is not None and change.reading and change.reading.strip():
            return change.reading.strip()
        return meme.voice_text

    def reset_players_for_match(self) -> None:
        for player in self.players.values():
            player.reset_for_match()


class KarutaManager:
    def __init__(
        self,
        *,
        backend: MemeBackend,
        settings: Settings,
        voicevox: VoicevoxClient | None = None,
    ) -> None:
        self.backend = backend
        self.settings = settings
        self.voicevox = voicevox or VoicevoxClient(settings)
        self.sessions: dict[str, KarutaSession] = {}
        self.active_by_guild: dict[int, str] = {}
        self.lock = asyncio.Lock()
        self.random = random.SystemRandom()
        self.discord_notifier: Callable[[KarutaSession], Awaitable[None]] | None = None

    def set_discord_notifier(
        self,
        notifier: Callable[[KarutaSession], Awaitable[None]],
    ) -> None:
        self.discord_notifier = notifier

    async def count_candidates(self, *, guild_id: int) -> int:
        return len(await self.backend.list_karuta_candidates(guild_id=guild_id))

    async def ensure_voicevox_available(self) -> None:
        if not await self.voicevox.check_available():
            raise VoicevoxUnavailableError

    async def create_game(
        self,
        *,
        guild_id: int,
        channel_id: int,
        organizer_id: int,
        participants: list[KarutaParticipant],
    ) -> KarutaSession:
        if len(participants) < MIN_PLAYERS:
            raise ValueError("karuta requires at least two players")
        if len(participants) > MAX_PLAYERS:
            raise ValueError("karuta allows up to ten players")

        async with self.lock:
            if guild_id in self.active_by_guild:
                raise ActiveGameExistsError

            game_id = new_game_id()
            players = {
                participant.user_id: PlayerState(
                    user_id=participant.user_id,
                    display_name=participant.display_name,
                    avatar_url=participant.avatar_url,
                    token=new_player_token(),
                )
                for participant in participants
            }
            session = KarutaSession(
                game_id=game_id,
                guild_id=guild_id,
                channel_id=channel_id,
                organizer_id=organizer_id,
                players=players,
                audio_root=self.settings.data_dir / "karuta_audio",
            )
            self.sessions[game_id] = session
            self.active_by_guild[guild_id] = game_id

        try:
            await self.prepare_new_match(session, initial=True)
        except Exception:
            async with self.lock:
                self.sessions.pop(game_id, None)
                self.active_by_guild.pop(guild_id, None)
            raise
        LOGGER.info("karuta game creation: game=%s guild=%s", game_id, guild_id)
        return session

    async def prepare_new_match(self, session: KarutaSession, *, initial: bool = False) -> None:
        candidates = await self.backend.list_karuta_candidates(guild_id=session.guild_id)
        if len(candidates) < BOARD_SIZE:
            raise NotEnoughMemesError

        styles = [VoiceStyle("ブラウザ", "読み上げ", 0)]

        selected = self.random.sample(candidates, BOARD_SIZE)
        board = selected[:]
        self.random.shuffle(board)
        reading_order = board[:]
        self.random.shuffle(reading_order)
        unread = reading_order.pop()

        session.match_no += 1
        session.reset_players_for_match()
        session.state = GameState.LOBBY
        session.current_round_index = -1
        session.memes_by_id = {meme.id: meme for meme in selected}
        session.board_ids = [meme.id for meme in board]
        session.remaining_ids = set(session.board_ids)
        session.unread_meme_id = unread.id
        session.rounds = [
            self._make_round_plan(
                round_no=index + 1,
                meme=meme,
                session=session,
                styles=styles,
            )
            for index, meme in enumerate(reading_order[:MAX_ROUNDS])
        ]
        session.result_rows = []
        session.end_reason = None
        session.reading_update_count = 0
        session.result_notified = False

        if not initial:
            self._cleanup_old_audio(session.audio_root / session.game_id, keep=session.audio_dir)

        await self.broadcast_state(session)
        LOGGER.info(
            "karuta match prepared with browser TTS: game=%s match=%s",
            session.game_id,
            session.match_no,
        )

    def _make_round_plan(
        self,
        *,
        round_no: int,
        meme: Meme,
        session: KarutaSession,
        styles: list[VoiceStyle],
    ) -> RoundPlan:
        return RoundPlan(
            round_no=round_no,
            meme_id=meme.id,
            reading_text=session.reading_for_meme(meme),
            voice_style=self.random.choice(styles),
            wait_ms=self.random.randint(0, 4000),
            audio_ready=True,
            tts_fallback=True,
        )

    def _cleanup_old_audio(self, game_dir: Path, *, keep: Path) -> None:
        if not game_dir.exists():
            return
        for path in game_dir.iterdir():
            if path == keep:
                continue
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)

    async def _generate_initial_audio(self, session: KarutaSession) -> None:
        try:
            await self._generate_audio_range(session, start=1, end=1)
            LOGGER.info("voice generation first buffer ready: game=%s", session.game_id)
            await self.broadcast_state(session)
            await self._maybe_start_game(session)
        except Exception:
            LOGGER.exception("voice generation failed for initial buffer: game=%s", session.game_id)
            await self.finish_game(session, reason="voice_error")

    async def _generate_background_audio(self, session: KarutaSession) -> None:
        try:
            await self._generate_audio_range(session, start=2, end=MAX_ROUNDS)
        except Exception:
            LOGGER.exception("background voice generation failed: game=%s", session.game_id)

    async def _generate_audio_range(self, session: KarutaSession, *, start: int, end: int) -> None:
        for round_plan in session.rounds[start - 1 : end]:
            if round_plan.audio_ready:
                continue
            LOGGER.info(
                "voice generation start: game=%s round=%s style=%s text=%r",
                session.game_id,
                round_plan.round_no,
                round_plan.voice_style.label(),
                round_plan.reading_text[:80],
            )
            await self.voicevox.synthesize_round(round_plan, session.audio_dir)
            LOGGER.info(
                "voice generation complete: game=%s round=%s style=%s",
                session.game_id,
                round_plan.round_no,
                round_plan.voice_style.label(),
            )

    async def connect(self, *, game_id: str, token: str, websocket: Any) -> PlayerState:
        session, player = self.authenticate(game_id=game_id, token=token)
        session.websockets.setdefault(player.user_id, set()).add(websocket)
        player.connected = True
        LOGGER.info("karuta reconnect/connect: game=%s user=%s", game_id, player.user_id)
        await self.send_state(session, player)
        await self.broadcast_state(session)
        return player

    async def disconnect(self, *, game_id: str, user_id: int, websocket: Any) -> None:
        session = self.sessions.get(game_id)
        if session is None:
            return
        sockets = session.websockets.get(user_id)
        if sockets is None:
            return
        sockets.discard(websocket)
        if not sockets:
            session.websockets.pop(user_id, None)
            player = session.players.get(user_id)
            if player:
                player.connected = False
                LOGGER.info("karuta disconnection: game=%s user=%s", game_id, user_id)
        await self.broadcast_state(session)

    def authenticate(self, *, game_id: str, token: str) -> tuple[KarutaSession, PlayerState]:
        session = self.sessions.get(game_id)
        if session is None or session.state == GameState.DISBANDED:
            raise InvalidTokenError
        player = session.player_for_token(token)
        if player is None:
            raise InvalidTokenError
        return session, player

    async def handle_client_message(
        self,
        *,
        game_id: str,
        token: str,
        data: dict[str, Any],
    ) -> None:
        session, player = self.authenticate(game_id=game_id, token=token)
        message_type = str(data.get("type") or "")
        if message_type == "ready":
            await self.mark_ready(session, player)
        elif message_type == "images_loaded":
            await self.mark_images_loaded(session, player)
        elif message_type == "keyword_started":
            await self.mark_keyword_started(session, player, data)
        elif message_type == "click_card":
            await self.handle_click(session, player, data)
        elif message_type == "set_reading":
            await self.set_reading(session, player, data)
        elif message_type == "midgame_ack":
            await self.ack_midgame(session, player)
        elif message_type == "return_home":
            await self.return_home(session)
        elif message_type == "disband":
            await self.finish_game(session, reason="disbanded")

    async def mark_ready(self, session: KarutaSession, player: PlayerState) -> None:
        if session.state not in {GameState.LOBBY, GameState.LOADING, GameState.FINISHED}:
            return
        player.ready = True
        LOGGER.info("karuta ready: game=%s user=%s", session.game_id, player.user_id)
        await self.broadcast_state(session)
        await self._maybe_start_game(session)

    async def mark_images_loaded(self, session: KarutaSession, player: PlayerState) -> None:
        if session.state not in {GameState.LOBBY, GameState.LOADING}:
            return
        player.images_loaded = True
        await self.broadcast_state(session)
        await self._maybe_start_game(session)

    async def _maybe_start_game(self, session: KarutaSession) -> None:
        if session.state not in {GameState.LOBBY, GameState.LOADING}:
            return
        all_ready = all(player.ready for player in session.players.values())
        if not (all_ready and session.first_five_ready):
            if all_ready:
                session.state = GameState.LOADING
                await self.broadcast_state(session)
            return

        session.state = GameState.COUNTDOWN
        await self.broadcast_state(session)
        if session.background_audio_task is None or session.background_audio_task.done():
            session.background_audio_task = asyncio.create_task(self._generate_background_audio(session))
        if session.round_task is None or session.round_task.done():
            session.round_task = asyncio.create_task(self._run_rounds(session))

    async def _run_rounds(self, session: KarutaSession) -> None:
        if session.state == GameState.COUNTDOWN:
            for value in (3, 2, 1):
                await self.broadcast_event(session, "countdown", {"value": value})
                await asyncio.sleep(0.75)
        for index, round_plan in enumerate(session.rounds):
            if session.state in {GameState.FINISHED, GameState.DISBANDED}:
                return
            session.current_round_index = index
            try:
                await self._start_round(session, round_plan)
            except Exception:
                LOGGER.exception(
                    "voice generation failed during round start: game=%s round=%s",
                    session.game_id,
                    round_plan.round_no,
                )
                await self.finish_game(session, reason="voice_error")
                return
            return

    async def _start_round(self, session: KarutaSession, round_plan: RoundPlan) -> None:
        if not round_plan.audio_ready:
            session.state = GameState.LOADING
            await self.broadcast_state(session)
            await self.voicevox.synthesize_round(round_plan, session.audio_dir)

        round_plan.eligible_user_ids = {
            player.user_id
            for player in session.players.values()
            if player.can_play_round(round_plan.round_no)
        }
        round_plan.wrong_user_ids.clear()
        round_plan.correct_submissions.clear()
        round_plan.winner_user_id = None
        round_plan.winner_reaction_ms = None
        round_plan.adjudication_started = False

        if not round_plan.eligible_user_ids:
            await self.finish_game(session, reason="all_mistake")
            return

        session.state = GameState.ROUND_INTRO
        await self.broadcast_event(session, "round_started", self.round_payload(session))
        LOGGER.info("round start: game=%s round=%s", session.game_id, round_plan.round_no)

    async def mark_keyword_started(
        self,
        session: KarutaSession,
        player: PlayerState,
        data: dict[str, Any],
    ) -> None:
        round_plan = session.current_round
        if round_plan is None:
            return
        if int(data.get("round_no", 0)) != round_plan.round_no:
            return
        if session.state in {GameState.ROUND_INTRO, GameState.ROUND_WAIT}:
            session.state = GameState.ROUND_ACTIVE
            await self.broadcast_event(session, "round_active", self.round_payload(session))
            LOGGER.info(
                "round active: game=%s round=%s user=%s",
                session.game_id,
                round_plan.round_no,
                player.user_id,
            )

    async def handle_click(
        self,
        session: KarutaSession,
        player: PlayerState,
        data: dict[str, Any],
    ) -> None:
        round_plan = session.current_round
        if round_plan is None or session.state != GameState.ROUND_ACTIVE:
            return
        if int(data.get("round_no", 0)) != round_plan.round_no:
            return
        if player.user_id not in round_plan.eligible_user_ids:
            return
        if round_plan.round_no in player.acted_rounds:
            return
        if round_plan.winner_user_id is not None:
            return

        meme_id = int(data.get("meme_id", 0))
        reaction_ms = float(data.get("reaction_ms", -1))
        if meme_id not in session.remaining_ids:
            return
        if reaction_ms < self.settings.karuta_min_reaction_ms or reaction_ms > 30000:
            return

        player.acted_rounds.add(round_plan.round_no)
        session.arrival_seq += 1
        LOGGER.info(
            "karuta click: game=%s round=%s user=%s meme=%s reaction=%.3f",
            session.game_id,
            round_plan.round_no,
            player.user_id,
            meme_id,
            reaction_ms,
        )

        if meme_id == round_plan.meme_id:
            round_plan.correct_submissions.append(
                ClickSubmission(
                    user_id=player.user_id,
                    meme_id=meme_id,
                    reaction_ms=reaction_ms,
                    arrival_seq=session.arrival_seq,
                )
            )
            if not round_plan.adjudication_started:
                round_plan.adjudication_started = True
                asyncio.create_task(self._resolve_after_adjudication(session, round_plan.round_no))
            return

        round_plan.wrong_user_ids.add(player.user_id)
        player.mistake_count += 1
        player.penalty_until_round = max(player.penalty_until_round, round_plan.round_no + 1)
        await self.broadcast_event(
            session,
            "mistake",
            {
                "round_no": round_plan.round_no,
                "user_id": str(player.user_id),
                "message": "せっかちニキ",
            },
        )
        LOGGER.info("karuta mistake: game=%s user=%s", session.game_id, player.user_id)

        if (
            not round_plan.correct_submissions
            and round_plan.eligible_user_ids
            and round_plan.eligible_user_ids.issubset(round_plan.wrong_user_ids)
        ):
            await self.finish_game(session, reason="all_mistake")

    async def _resolve_after_adjudication(self, session: KarutaSession, round_no: int) -> None:
        await asyncio.sleep(ADJUDICATION_SECONDS)
        round_plan = session.current_round
        if (
            round_plan is None
            or round_plan.round_no != round_no
            or session.state != GameState.ROUND_ACTIVE
            or not round_plan.correct_submissions
        ):
            return

        winner = min(
            round_plan.correct_submissions,
            key=lambda item: (item.reaction_ms, item.arrival_seq, item.user_id),
        )
        player = session.players[winner.user_id]
        round_plan.winner_user_id = player.user_id
        round_plan.winner_reaction_ms = winner.reaction_ms
        player.cards_won += 1
        player.reaction_times_ms.append(winner.reaction_ms)
        session.remaining_ids.discard(round_plan.meme_id)
        session.state = GameState.ROUND_RESULT

        await self.broadcast_event(
            session,
            "round_result",
            {
                **self.round_payload(session),
                "winner_user_id": str(player.user_id),
                "winner_name": player.display_name,
                "winner_reaction_ms": winner.reaction_ms,
                "meme_id": round_plan.meme_id,
                "keyword": session.memes_by_id[round_plan.meme_id].keyword,
                "reading": round_plan.reading_text,
            },
        )
        LOGGER.info(
            "karuta winner: game=%s round=%s user=%s reaction=%.3f",
            session.game_id,
            round_no,
            player.user_id,
            winner.reaction_ms,
        )

        acquired_count = BOARD_SIZE - len(session.remaining_ids)
        if acquired_count >= MAX_ROUNDS:
            await asyncio.sleep(1.2)
            await self.finish_game(session, reason="normal")
            return
        if acquired_count == MIDGAME_CARD_COUNT:
            await self.start_midgame_pause(session)
            return

        await asyncio.sleep(1.2)
        await self.advance_round(session)

    async def advance_round(self, session: KarutaSession) -> None:
        if session.state in {GameState.FINISHED, GameState.DISBANDED}:
            return
        next_index = session.current_round_index + 1
        if next_index >= len(session.rounds):
            await self.finish_game(session, reason="normal")
            return
        session.current_round_index = next_index
        await self._start_round(session, session.rounds[next_index])

    async def start_midgame_pause(self, session: KarutaSession) -> None:
        session.state = GameState.MIDGAME_PAUSE
        for player in session.players.values():
            player.midgame_ack = False
            await self.send_event(
                session,
                player,
                "midgame_pause",
                {
                    "message": "中間確認",
                    "your_cards_won": player.cards_won,
                    "round_no": session.current_round.round_no if session.current_round else 0,
                },
            )
        await self.broadcast_state(session)

    async def ack_midgame(self, session: KarutaSession, player: PlayerState) -> None:
        if session.state != GameState.MIDGAME_PAUSE:
            return
        player.midgame_ack = True
        if all(player.midgame_ack for player in session.players.values()):
            await self.advance_round(session)
        else:
            await self.broadcast_state(session)

    async def set_reading(
        self,
        session: KarutaSession,
        player: PlayerState,
        data: dict[str, Any],
    ) -> None:
        meme_id = int(data.get("meme_id", 0))
        reading = str(data.get("reading") or "").strip()
        if len(reading) > READING_MAX_LENGTH:
            reading = reading[:READING_MAX_LENGTH]
        if not reading:
            reading_value: str | None = None
        else:
            reading_value = reading
        session.reading_changes[meme_id] = ReadingChange(
            meme_id=meme_id,
            reading=reading_value,
            updated_by=player.user_id,
        )
        for round_plan in session.rounds:
            if round_plan.meme_id == meme_id and not round_plan.audio_ready:
                meme = session.memes_by_id.get(meme_id)
                if meme is not None:
                    round_plan.reading_text = session.reading_for_meme(meme)
        await self.broadcast_event(
            session,
            "reading_updated",
            {"meme_id": meme_id, "reading": reading_value},
        )
        LOGGER.info("reading update queued: game=%s meme=%s", session.game_id, meme_id)

    async def return_home(self, session: KarutaSession) -> None:
        if session.state != GameState.FINISHED:
            return
        await self.prepare_new_match(session)

    async def finish_game(self, session: KarutaSession, *, reason: str) -> None:
        if session.state == GameState.DISBANDED:
            return
        session.end_reason = reason
        session.state = GameState.DISBANDED if reason == "disbanded" else GameState.FINISHED
        session.result_rows = self.calculate_results(session)
        session.reading_update_count = await self.flush_reading_changes(session)
        if reason == "disbanded":
            self.active_by_guild.pop(session.guild_id, None)

        await self.broadcast_event(
            session,
            "game_finished",
            {
                "reason": reason,
                "results": [row.public_dict() for row in session.result_rows],
                "reading_update_count": session.reading_update_count,
            },
        )
        LOGGER.info("game finish: game=%s reason=%s", session.game_id, reason)

        if self.discord_notifier is not None and not session.result_notified:
            session.result_notified = True
            asyncio.create_task(self.discord_notifier(session))

    def calculate_results(self, session: KarutaSession) -> list[ResultRow]:
        rows: list[ResultRow] = []
        sorted_players = sorted(
            session.players.values(),
            key=lambda player: (
                -player.cards_won,
                self._average_or_infinity(player.reaction_times_ms),
                self._fastest_or_infinity(player.reaction_times_ms),
                player.mistake_count,
                player.user_id,
            ),
        )
        previous_key: tuple[int, float, float, int] | None = None
        current_rank = 0
        for index, player in enumerate(sorted_players, start=1):
            avg = self._average(player.reaction_times_ms)
            fastest = min(player.reaction_times_ms) if player.reaction_times_ms else None
            key = (
                player.cards_won,
                avg if avg is not None else float("inf"),
                fastest if fastest is not None else float("inf"),
                player.mistake_count,
            )
            if key != previous_key:
                current_rank = index
            previous_key = key
            rows.append(
                ResultRow(
                    rank=current_rank,
                    user_id=player.user_id,
                    display_name=player.display_name,
                    avatar_url=player.avatar_url,
                    cards_won=player.cards_won,
                    average_reaction_ms=avg,
                    fastest_reaction_ms=fastest,
                    mistake_count=player.mistake_count,
                )
            )
        return rows

    async def flush_reading_changes(self, session: KarutaSession) -> int:
        if not session.reading_changes:
            return 0
        changes = {
            meme_id: (change.reading, change.updated_by)
            for meme_id, change in session.reading_changes.items()
        }
        try:
            updated = await self.backend.update_meme_readings(
                guild_id=session.guild_id,
                changes=changes,
            )
            session.reading_changes.clear()
            return updated
        except Exception:
            LOGGER.exception("reading update failed: game=%s", session.game_id)
            return 0

    def _average(self, values: list[float]) -> float | None:
        if not values:
            return None
        return sum(values) / len(values)

    def _average_or_infinity(self, values: list[float]) -> float:
        value = self._average(values)
        return value if value is not None else float("inf")

    def _fastest_or_infinity(self, values: list[float]) -> float:
        return min(values) if values else float("inf")

    async def get_meme_for_image(self, *, game_id: str, token: str, meme_id: int) -> Meme:
        session, _ = self.authenticate(game_id=game_id, token=token)
        meme = session.memes_by_id.get(meme_id)
        if meme is None:
            meme = await self.backend.get_meme(guild_id=session.guild_id, meme_id=meme_id)
        if meme is None:
            raise ImageNotFoundError
        return meme

    async def list_memes_for_session(
        self,
        *,
        game_id: str,
        token: str,
        query: str,
        page: int,
        page_size: int = 20,
    ) -> dict[str, Any]:
        session, _ = self.authenticate(game_id=game_id, token=token)
        page = max(page, 1)
        offset = (page - 1) * page_size
        total = await self.backend.count_search_memes(guild_id=session.guild_id, query=query)
        memes = await self.backend.search_memes(
            guild_id=session.guild_id,
            query=query,
            limit=page_size,
            offset=offset,
        )
        return {
            "page": page,
            "page_size": page_size,
            "total": total,
            "items": [
                {
                    "id": meme.id,
                    "keyword": meme.keyword,
                    "reading": self._session_reading(session, meme),
                    "image_url": f"/karuta/api/games/{game_id}/memes/{meme.id}/image?token={token}",
                }
                for meme in memes
            ],
        }

    def _session_reading(self, session: KarutaSession, meme: Meme) -> str | None:
        change = session.reading_changes.get(meme.id)
        if change is not None:
            return change.reading
        return meme.reading

    def player_urls(self, session: KarutaSession) -> dict[int, str]:
        return {
            player.user_id: f"{self.settings.public_base_url}/karuta/{session.game_id}?token={player.token}"
            for player in session.players.values()
        }

    def round_payload(self, session: KarutaSession, *, player: PlayerState | None = None) -> dict[str, Any]:
        round_plan = session.current_round
        if round_plan is None:
            return {"round_no": 0, "total_rounds": MAX_ROUNDS}
        token = player.token if player is not None else ""
        payload = {
            "round_no": round_plan.round_no,
            "total_rounds": MAX_ROUNDS,
            "wait_ms": round_plan.wait_ms,
            "voice": round_plan.voice_style.label(),
            "audio_ready": round_plan.audio_ready,
            "reading_text": round_plan.reading_text,
            "tts_fallback": round_plan.tts_fallback,
            "state": session.state.value,
        }
        if player is not None:
            payload["audio"] = round_plan.audio_urls(game_id=session.game_id, token=token)
        return payload

    def session_payload(self, session: KarutaSession, player: PlayerState) -> dict[str, Any]:
        current_round_no = session.current_round.round_no if session.current_round else 0
        return {
            "type": "state",
            "game_id": session.game_id,
            "state": session.state.value,
            "self_user_id": str(player.user_id),
            "players": [
                other.public_dict(viewer_id=player.user_id, round_no=current_round_no)
                for other in session.players.values()
            ],
            "cards": [
                {
                    "id": meme_id,
                    "remaining": meme_id in session.remaining_ids,
                    "image_url": (
                        f"/karuta/api/games/{session.game_id}/memes/{meme_id}/image"
                        f"?token={player.token}"
                    ),
                }
                for meme_id in session.board_ids
            ],
            "round": self.round_payload(session, player=player),
            "first_five_ready": session.first_five_ready,
            "all_ready": all(other.ready for other in session.players.values()),
            "all_images_loaded": all(other.images_loaded for other in session.players.values()),
            "results": [row.public_dict() for row in session.result_rows],
            "end_reason": session.end_reason,
            "reading_update_count": session.reading_update_count,
        }

    async def send_state(self, session: KarutaSession, player: PlayerState) -> None:
        await self.send_event(session, player, "state", self.session_payload(session, player))

    async def broadcast_state(self, session: KarutaSession) -> None:
        for player in list(session.players.values()):
            await self.send_state(session, player)

    async def send_event(
        self,
        session: KarutaSession,
        player: PlayerState,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        sockets = list(session.websockets.get(player.user_id, set()))
        if not sockets:
            return
        message = {"type": event_type, **payload}
        dead: list[Any] = []
        for websocket in sockets:
            try:
                await websocket.send_json(message)
            except Exception:
                dead.append(websocket)
        for websocket in dead:
            session.websockets.get(player.user_id, set()).discard(websocket)

    async def broadcast_event(
        self,
        session: KarutaSession,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        for player in list(session.players.values()):
            player_payload = dict(payload)
            if event_type in {"round_started", "round_active"}:
                player_payload = self.round_payload(session, player=player)
            await self.send_event(session, player, event_type, player_payload)

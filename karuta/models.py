from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class GameState(str, Enum):
    RECRUITING = "RECRUITING"
    PREPARING = "PREPARING"
    LOBBY = "LOBBY"
    LOADING = "LOADING"
    COUNTDOWN = "COUNTDOWN"
    ROUND_INTRO = "ROUND_INTRO"
    ROUND_WAIT = "ROUND_WAIT"
    ROUND_ACTIVE = "ROUND_ACTIVE"
    ROUND_RESULT = "ROUND_RESULT"
    MIDGAME_PAUSE = "MIDGAME_PAUSE"
    FINISHED = "FINISHED"
    DISBANDED = "DISBANDED"


@dataclass(frozen=True)
class VoiceStyle:
    speaker_name: str
    style_name: str
    style_id: int

    def label(self) -> str:
        return f"{self.speaker_name} / {self.style_name}"


@dataclass(frozen=True)
class KarutaParticipant:
    user_id: int
    display_name: str
    avatar_url: str


@dataclass
class PlayerState:
    user_id: int
    display_name: str
    avatar_url: str
    token: str
    ready: bool = False
    images_loaded: bool = False
    connected: bool = False
    cards_won: int = 0
    mistake_count: int = 0
    penalty_until_round: int = 0
    reaction_times_ms: list[float] = field(default_factory=list)
    acted_rounds: set[int] = field(default_factory=set)
    midgame_ack: bool = False

    def can_play_round(self, round_no: int) -> bool:
        return self.connected and round_no > self.penalty_until_round

    def reset_for_match(self) -> None:
        self.ready = False
        self.images_loaded = False
        self.cards_won = 0
        self.mistake_count = 0
        self.penalty_until_round = 0
        self.reaction_times_ms.clear()
        self.acted_rounds.clear()
        self.midgame_ack = False

    def public_dict(self, *, viewer_id: int | None = None, round_no: int = 0) -> dict[str, Any]:
        return {
            "user_id": str(self.user_id),
            "display_name": self.display_name,
            "avatar_url": self.avatar_url,
            "ready": self.ready,
            "images_loaded": self.images_loaded,
            "connected": self.connected,
            "is_self": self.user_id == viewer_id,
            "penalty": round_no > 0 and round_no <= self.penalty_until_round,
            "mistakes": self.mistake_count,
        }

@dataclass
class ClickSubmission:
    user_id: int
    meme_id: int
    reaction_ms: float
    arrival_seq: int


@dataclass
class RoundPlan:
    round_no: int
    meme_id: int
    reading_text: str
    voice_style: VoiceStyle
    wait_ms: int
    intro_path: Path | None = None
    keyword_path: Path | None = None
    audio_ready: bool = False
    eligible_user_ids: set[int] = field(default_factory=set)
    wrong_user_ids: set[int] = field(default_factory=set)
    correct_submissions: list[ClickSubmission] = field(default_factory=list)
    winner_user_id: int | None = None
    winner_reaction_ms: float | None = None
    adjudication_started: bool = False

    def audio_urls(self, *, game_id: str, token: str) -> dict[str, str]:
        if self.intro_path is None or self.keyword_path is None:
            return {"intro": "", "keyword": ""}
        return {
            "intro": f"/karuta/audio/{game_id}/{self.intro_path.name}?token={token}",
            "keyword": f"/karuta/audio/{game_id}/{self.keyword_path.name}?token={token}",
        }


@dataclass(frozen=True)
class ReadingChange:
    meme_id: int
    reading: str | None
    updated_by: int


@dataclass(frozen=True)
class ResultRow:
    rank: int
    user_id: int
    display_name: str
    avatar_url: str
    cards_won: int
    average_reaction_ms: float | None
    fastest_reaction_ms: float | None
    mistake_count: int

    def public_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "user_id": str(self.user_id),
            "display_name": self.display_name,
            "avatar_url": self.avatar_url,
            "cards_won": self.cards_won,
            "average_reaction_ms": self.average_reaction_ms,
            "fastest_reaction_ms": self.fastest_reaction_ms,
            "mistake_count": self.mistake_count,
        }

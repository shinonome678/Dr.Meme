from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
IMAGES_DIR = DATA_DIR / "images"
DB_PATH = DATA_DIR / "memes.db"

DEFAULT_MEME_EDITOR_ROLE = "ミーム編集者"
DEFAULT_COOLDOWN_SECONDS = 10


def _parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_int(value: str | None, *, default: int | None = None) -> int | None:
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    discord_token: str | None
    test_guild_id: int | None
    meme_editor_role: str
    allow_everyone_to_edit: bool
    meme_cooldown_seconds: int
    sync_global_commands: bool
    data_dir: Path
    images_dir: Path
    db_path: Path


def load_settings() -> Settings:
    load_dotenv(BASE_DIR / ".env")

    cooldown = _parse_int(
        os.getenv("MEME_COOLDOWN_SECONDS"),
        default=DEFAULT_COOLDOWN_SECONDS,
    )
    if cooldown is None or cooldown < 0:
        cooldown = DEFAULT_COOLDOWN_SECONDS

    return Settings(
        discord_token=os.getenv("DISCORD_TOKEN"),
        test_guild_id=_parse_int(os.getenv("TEST_GUILD_ID")),
        meme_editor_role=os.getenv("MEME_EDITOR_ROLE", DEFAULT_MEME_EDITOR_ROLE),
        allow_everyone_to_edit=_parse_bool(
            os.getenv("ALLOW_EVERYONE_TO_EDIT"),
            default=False,
        ),
        meme_cooldown_seconds=cooldown,
        sync_global_commands=_parse_bool(
            os.getenv("SYNC_GLOBAL_COMMANDS"),
            default=True,
        ),
        data_dir=DATA_DIR,
        images_dir=IMAGES_DIR,
        db_path=DB_PATH,
    )

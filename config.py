from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = BASE_DIR / "data"

DEFAULT_MEME_EDITOR_ROLE = "ミーム編集者"
DEFAULT_COOLDOWN_SECONDS = 10
DEFAULT_WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 8080
DEFAULT_VOICEVOX_BASE_URL = "http://127.0.0.1:50021"


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
    backend: str
    meme_editor_role: str
    allow_everyone_to_edit: bool
    meme_cooldown_seconds: int
    sync_global_commands: bool
    data_dir: Path
    images_dir: Path
    db_path: Path
    supabase_url: str | None
    supabase_service_role_key: str | None
    supabase_bucket: str
    web_host: str
    web_port: int
    public_base_url: str
    voicevox_base_url: str
    voicevox_excluded_style_ids: frozenset[int]
    karuta_min_reaction_ms: int


def load_settings() -> Settings:
    load_dotenv(BASE_DIR / ".env")

    data_dir = Path(os.getenv("DATA_DIR", DEFAULT_DATA_DIR)).expanduser()
    if not data_dir.is_absolute():
        data_dir = BASE_DIR / data_dir

    cooldown = _parse_int(
        os.getenv("MEME_COOLDOWN_SECONDS"),
        default=DEFAULT_COOLDOWN_SECONDS,
    )
    if cooldown is None or cooldown < 0:
        cooldown = DEFAULT_COOLDOWN_SECONDS

    web_port = _parse_int(
        os.getenv("WEB_PORT") or os.getenv("PORT"),
        default=DEFAULT_WEB_PORT,
    )
    if web_port is None or web_port <= 0:
        web_port = DEFAULT_WEB_PORT

    public_base_url = os.getenv("PUBLIC_BASE_URL", "").strip()
    if not public_base_url:
        public_base_url = f"http://{DEFAULT_WEB_HOST}:{web_port}"

    excluded_style_ids: set[int] = set()
    for raw_id in os.getenv("VOICEVOX_EXCLUDED_STYLE_IDS", "").split(","):
        style_id = _parse_int(raw_id.strip())
        if style_id is not None:
            excluded_style_ids.add(style_id)

    min_reaction_ms = _parse_int(os.getenv("KARUTA_MIN_REACTION_MS"), default=80)
    if min_reaction_ms is None or min_reaction_ms < 0:
        min_reaction_ms = 80

    return Settings(
        discord_token=os.getenv("DISCORD_TOKEN"),
        test_guild_id=_parse_int(os.getenv("TEST_GUILD_ID")),
        backend=os.getenv("BACKEND", "local").strip().lower(),
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
        data_dir=data_dir,
        images_dir=data_dir / "images",
        db_path=data_dir / "memes.db",
        supabase_url=os.getenv("SUPABASE_URL"),
        supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        supabase_bucket=os.getenv("SUPABASE_BUCKET", "memes"),
        web_host=os.getenv("WEB_HOST", DEFAULT_WEB_HOST).strip() or DEFAULT_WEB_HOST,
        web_port=web_port,
        public_base_url=public_base_url.rstrip("/"),
        voicevox_base_url=os.getenv(
            "VOICEVOX_BASE_URL",
            DEFAULT_VOICEVOX_BASE_URL,
        ).strip().rstrip("/"),
        voicevox_excluded_style_ids=frozenset(excluded_style_ids),
        karuta_min_reaction_ms=min_reaction_ms,
    )

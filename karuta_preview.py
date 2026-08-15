from __future__ import annotations

import argparse
import asyncio
import math
import wave
from pathlib import Path

from backends import ImagePayload
from config import Settings
from database import Meme
from karuta.manager import KarutaManager
from karuta.models import KarutaParticipant, VoiceStyle
from karuta.web import KarutaWebServer


class PreviewBackend:
    def __init__(self) -> None:
        self.memes = [
            Meme(
                id=index,
                guild_id=1,
                keyword=f"preview-keyword-{index}",
                match_type="partial",
                image_path=f"preview/{index}.svg",
                created_by=1,
                created_at="2026-08-15T00:00:00Z",
                enabled=True,
                trigger_count=0,
                reading=f"プレビュー {index}",
            )
            for index in range(1, 61)
        ]
        self.reading_updates: dict[int, tuple[str | None, int]] = {}

    async def list_karuta_candidates(self, *, guild_id: int) -> list[Meme]:
        return self.memes

    async def get_meme(self, *, guild_id: int, meme_id: int) -> Meme | None:
        return next((meme for meme in self.memes if meme.id == meme_id), None)

    async def count_search_memes(self, *, guild_id: int, query: str) -> int:
        return len(self._filtered(query))

    async def search_memes(
        self,
        *,
        guild_id: int,
        query: str,
        limit: int,
        offset: int = 0,
    ) -> list[Meme]:
        return self._filtered(query)[offset : offset + limit]

    async def update_meme_readings(
        self,
        *,
        guild_id: int,
        changes: dict[int, tuple[str | None, int]],
    ) -> int:
        self.reading_updates.update(changes)
        return len(changes)

    async def read_meme_image(self, meme: Meme) -> ImagePayload:
        hue = (meme.id * 41) % 360
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320" viewBox="0 0 320 320">
<rect width="320" height="320" rx="18" fill="hsl({hue}, 68%, 54%)"/>
<rect x="22" y="22" width="276" height="276" rx="14" fill="rgba(255,255,255,0.18)" stroke="white" stroke-width="6"/>
<text x="160" y="145" text-anchor="middle" font-family="Arial, sans-serif" font-size="52" font-weight="700" fill="white">Dr.Meme</text>
<text x="160" y="205" text-anchor="middle" font-family="Arial, sans-serif" font-size="78" font-weight="900" fill="white">#{meme.id}</text>
</svg>"""
        return ImagePayload(
            data=svg.encode("utf-8"),
            content_type="image/svg+xml",
            filename=f"{meme.id}.svg",
        )

    def _filtered(self, query: str) -> list[Meme]:
        normalized = query.strip().lower()
        if not normalized:
            return self.memes
        return [
            meme
            for meme in self.memes
            if normalized in meme.keyword.lower()
            or normalized in (meme.reading or "").lower()
            or normalized == str(meme.id)
        ]


class PreviewVoicevox:
    async def check_available(self) -> bool:
        return True

    async def available_styles(self) -> list[VoiceStyle]:
        return [
            VoiceStyle("Preview", "Normal", 1),
            VoiceStyle("Preview", "High", 2),
            VoiceStyle("Preview", "Low", 3),
        ]

    async def synthesize_round(self, round_plan, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        intro = output_dir / f"round_{round_plan.round_no:02d}_intro.wav"
        keyword = output_dir / f"round_{round_plan.round_no:02d}_keyword.wav"
        if not intro.exists():
            write_tone(intro, frequency=440 + round_plan.round_no * 3, duration=0.22)
        if not keyword.exists():
            write_tone(keyword, frequency=660 + round_plan.round_no * 5, duration=0.42)
        round_plan.intro_path = intro
        round_plan.keyword_path = keyword
        round_plan.audio_ready = True


def write_tone(path: Path, *, frequency: float, duration: float) -> None:
    sample_rate = 22050
    amplitude = 9000
    frame_count = int(sample_rate * duration)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        for index in range(frame_count):
            value = int(amplitude * math.sin(2 * math.pi * frequency * index / sample_rate))
            wav.writeframesraw(value.to_bytes(2, byteorder="little", signed=True))


def make_settings(*, host: str, port: int, data_dir: Path) -> Settings:
    public_host = "127.0.0.1" if host == "0.0.0.0" else host
    return Settings(
        discord_token=None,
        test_guild_id=None,
        backend="preview",
        meme_editor_role="Dr.Meme",
        allow_everyone_to_edit=True,
        meme_cooldown_seconds=0,
        sync_global_commands=False,
        data_dir=data_dir,
        images_dir=data_dir / "images",
        db_path=data_dir / "preview.db",
        supabase_url=None,
        supabase_service_role_key=None,
        supabase_bucket="memes",
        web_host=host,
        web_port=port,
        public_base_url=f"http://{public_host}:{port}",
        voicevox_base_url="preview",
        voicevox_excluded_style_ids=frozenset(),
        karuta_min_reaction_ms=0,
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--url-file", default="data/karuta_preview_urls.txt")
    args = parser.parse_args()

    data_dir = Path("data") / "karuta_preview"
    settings = make_settings(host=args.host, port=args.port, data_dir=data_dir)
    backend = PreviewBackend()
    manager = KarutaManager(
        backend=backend,
        settings=settings,
        voicevox=PreviewVoicevox(),
    )
    server = KarutaWebServer(manager=manager, settings=settings)
    await server.start()

    participants = [
        KarutaParticipant(1001, "Preview A", "https://cdn.discordapp.com/embed/avatars/0.png"),
        KarutaParticipant(1002, "Preview B", "https://cdn.discordapp.com/embed/avatars/1.png"),
        KarutaParticipant(1003, "Preview C", "https://cdn.discordapp.com/embed/avatars/2.png"),
    ]
    session = await manager.create_game(
        guild_id=1,
        channel_id=1,
        organizer_id=1001,
        participants=participants,
    )

    for player in list(session.players.values())[1:]:
        player.ready = True
        player.images_loaded = True
        player.connected = False

    urls = manager.player_urls(session)
    url_file = Path(args.url_file)
    url_file.parent.mkdir(parents=True, exist_ok=True)
    url_file.write_text(
        "\n".join(
            [
                "Dr.Meme karuta preview",
                f"Primary: {urls[1001]}",
                f"Player B: {urls[1002]}",
                f"Player C: {urls[1003]}",
            ]
        ),
        encoding="utf-8",
    )
    print(url_file.read_text(encoding="utf-8"), flush=True)

    try:
        await asyncio.Event().wait()
    finally:
        await server.stop()


if __name__ == "__main__":
    asyncio.run(main())

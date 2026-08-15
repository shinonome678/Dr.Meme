from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import aiohttp

from config import Settings
from .models import RoundPlan, VoiceStyle


LOGGER = logging.getLogger(__name__)


class VoicevoxError(Exception):
    """VOICEVOX ENGINE request failed."""


class VoicevoxClient:
    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.voicevox_base_url.rstrip("/")
        self.excluded_style_ids = set(settings.voicevox_excluded_style_ids)
        self.timeout = aiohttp.ClientTimeout(total=45)

    async def check_available(self) -> bool:
        try:
            styles = await self.available_styles()
        except Exception:
            LOGGER.exception("VOICEVOX health check failed.")
            return False
        return bool(styles)

    async def available_styles(self) -> list[VoiceStyle]:
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.get(f"{self.base_url}/speakers") as response:
                if response.status >= 400:
                    raise VoicevoxError(f"speakers failed: {response.status}")
                speakers: list[dict[str, Any]] = await response.json()

        styles: list[VoiceStyle] = []
        for speaker in speakers:
            speaker_name = str(speaker.get("name") or "VOICEVOX")
            for style in speaker.get("styles", []):
                style_id = int(style.get("id"))
                if style_id in self.excluded_style_ids:
                    continue
                styles.append(
                    VoiceStyle(
                        speaker_name=speaker_name,
                        style_name=str(style.get("name") or "default"),
                        style_id=style_id,
                    )
                )
        return styles

    async def synthesize_round(self, round_plan: RoundPlan, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        intro_path = output_dir / f"round_{round_plan.round_no:02d}_intro.wav"
        keyword_path = output_dir / f"round_{round_plan.round_no:02d}_keyword.wav"

        if not intro_path.exists():
            await self.synthesize(
                text=f"第{round_plan.round_no}戦",
                speaker_id=round_plan.voice_style.style_id,
                output_path=intro_path,
            )
        if not keyword_path.exists():
            await self.synthesize(
                text=round_plan.reading_text,
                speaker_id=round_plan.voice_style.style_id,
                output_path=keyword_path,
            )

        round_plan.intro_path = intro_path
        round_plan.keyword_path = keyword_path
        round_plan.audio_ready = True

    async def synthesize(self, *, text: str, speaker_id: int, output_path: Path) -> None:
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.post(
                f"{self.base_url}/audio_query",
                params={"text": text, "speaker": str(speaker_id)},
            ) as query_response:
                query_text = await query_response.text()
                if query_response.status >= 400:
                    raise VoicevoxError(
                        f"audio_query failed: {query_response.status} {query_text[:200]}"
                    )
                query_json = await query_response.json()

            async with session.post(
                f"{self.base_url}/synthesis",
                params={"speaker": str(speaker_id)},
                json=query_json,
            ) as synth_response:
                wav = await synth_response.read()
                if synth_response.status >= 400:
                    raise VoicevoxError(
                        f"synthesis failed: {synth_response.status} {wav[:200]!r}"
                    )

        await asyncio.to_thread(output_path.write_bytes, wav)

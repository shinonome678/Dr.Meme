from __future__ import annotations

import logging
import time

import discord
from discord.ext import commands

from backends import BackendError, ImageNotFoundError
from meme_matching import find_best_match


LOGGER = logging.getLogger(__name__)


class MemeListener(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cooldowns: dict[tuple[int, int], float] = {}

    @property
    def backend(self):
        return self.bot.backend

    @property
    def settings(self):
        return self.bot.settings

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if message.guild is None:
            return
        if not message.content:
            return

        memes = await self.backend.list_enabled_memes(guild_id=message.guild.id)
        meme = find_best_match(memes, message.content)
        if meme is None:
            return

        cooldown_key = (message.guild.id, meme.id)
        now = time.monotonic()
        last_triggered = self.cooldowns.get(cooldown_key)
        if (
            last_triggered is not None
            and now - last_triggered < self.settings.meme_cooldown_seconds
        ):
            return

        try:
            file = await self.backend.to_discord_file(meme)
        except ImageNotFoundError:
            LOGGER.warning("Registered meme image is missing: %s", meme.image_path)
            return
        except BackendError:
            LOGGER.exception("Failed to read meme image: %s", meme.image_path)
            return

        try:
            await message.reply(
                file=file,
                mention_author=False,
            )
        except discord.HTTPException:
            LOGGER.exception("Failed to send meme image: %s", meme.image_path)
            return

        self.cooldowns[cooldown_key] = now
        await self.backend.increment_trigger_count(guild_id=message.guild.id, meme_id=meme.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MemeListener(bot))

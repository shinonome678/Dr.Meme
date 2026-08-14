from __future__ import annotations

import logging
import time

import discord
from discord.ext import commands

from meme_matching import find_best_match


LOGGER = logging.getLogger(__name__)


class MemeListener(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cooldowns: dict[tuple[int, int], float] = {}

    @property
    def db(self):
        return self.bot.db

    @property
    def storage(self):
        return self.bot.storage

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

        memes = self.db.list_enabled_memes(guild_id=message.guild.id)
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

        image_path = self.storage.path_for(meme.image_path)
        if not image_path.is_file():
            LOGGER.warning("Registered meme image is missing: %s", image_path)
            return

        try:
            await message.reply(
                file=discord.File(image_path, filename=image_path.name),
                mention_author=False,
            )
        except discord.HTTPException:
            LOGGER.exception("Failed to send meme image: %s", image_path)
            return

        self.cooldowns[cooldown_key] = now
        self.db.increment_trigger_count(guild_id=message.guild.id, meme_id=meme.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MemeListener(bot))

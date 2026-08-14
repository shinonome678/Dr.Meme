from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from config import Settings, load_settings
from database import MemeDatabase
from image_storage import ImageStorage


class MemeBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.messages = True

        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.settings = settings
        self.db = MemeDatabase(settings.db_path)
        self.storage = ImageStorage(
            data_dir=settings.data_dir,
            images_dir=settings.images_dir,
        )

    async def setup_hook(self) -> None:
        self.db.initialize()
        self.storage.initialize()

        await self.load_extension("cogs.meme_commands")
        await self.load_extension("cogs.meme_listener")

        if self.settings.test_guild_id is not None:
            guild = discord.Object(id=self.settings.test_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logging.info(
                "Synced %s application commands to test guild %s.",
                len(synced),
                self.settings.test_guild_id,
            )
        elif self.settings.sync_global_commands:
            synced = await self.tree.sync()
            logging.info("Synced %s global application commands.", len(synced))
        else:
            logging.info("Skipped application command sync by configuration.")

    async def on_ready(self) -> None:
        user = self.user
        if user is None:
            logging.info("Bot is ready.")
            return
        logging.info("Logged in as %s (ID: %s)", user, user.id)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    settings = load_settings()
    if not settings.discord_token:
        raise SystemExit(".env に DISCORD_TOKEN を設定してください。")

    bot = MemeBot(settings)
    async with bot:
        await bot.start(settings.discord_token)


if __name__ == "__main__":
    asyncio.run(main())

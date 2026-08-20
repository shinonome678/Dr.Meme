from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from karuta.manager import (
    ActiveGameExistsError,
    MAX_PLAYERS,
    MIN_PLAYERS,
    NotEnoughMemesError,
    VoicevoxUnavailableError,
)
from karuta.models import KarutaParticipant
from permissions import can_manage_memes, permission_denied_message


LOGGER = logging.getLogger(__name__)


async def send_ephemeral(interaction: discord.Interaction, content: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True)
    else:
        await interaction.response.send_message(content, ephemeral=True)


def participant_from_user(user: discord.User | discord.Member) -> KarutaParticipant:
    display_name = user.display_name if isinstance(user, discord.Member) else user.name
    return KarutaParticipant(
        user_id=user.id,
        display_name=display_name,
        avatar_url=user.display_avatar.url,
    )


class KarutaRecruitmentView(discord.ui.View):
    def __init__(self, cog: KarutaCommands, organizer: discord.Member) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.guild_id = organizer.guild.id
        self.channel_id = 0
        self.organizer_id = organizer.id
        self.organizer_name = organizer.display_name
        self.participants: dict[int, KarutaParticipant] = {
            organizer.id: participant_from_user(organizer)
        }
        self.started = False

    def embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Dr.Memeかるた 募集中",
            color=discord.Color.gold(),
        )
        names = "\n".join(
            f"- {participant.display_name}"
            for participant in self.participants.values()
        )
        embed.add_field(name="募集者", value=self.organizer_name, inline=True)
        embed.add_field(
            name="参加人数",
            value=f"{len(self.participants)} / {MAX_PLAYERS}",
            inline=True,
        )
        embed.add_field(name="現在の参加者", value=names or "-", inline=False)
        if self.started:
            embed.title = "Dr.Memeかるた 準備中"
            embed.color = discord.Color.green()
        return embed

    async def _edit_message(self, interaction: discord.Interaction) -> None:
        if interaction.response.is_done():
            await interaction.message.edit(embed=self.embed(), view=self)
        else:
            await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="参加する", style=discord.ButtonStyle.primary)
    async def join(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        if interaction.guild_id != self.guild_id:
            await send_ephemeral(interaction, "この募集には参加できません。")
            return
        if self.started:
            await send_ephemeral(interaction, "この募集はすでに締め切られています。")
            return
        if interaction.user.id in self.participants:
            await send_ephemeral(interaction, "すでに参加しています。")
            return
        if len(self.participants) >= MAX_PLAYERS:
            await send_ephemeral(interaction, "参加人数が10人に達しています。")
            return

        self.participants[interaction.user.id] = participant_from_user(interaction.user)
        LOGGER.info("karuta player join: guild=%s user=%s", self.guild_id, interaction.user.id)
        await self._edit_message(interaction)

    @discord.ui.button(label="ゲームを開始", style=discord.ButtonStyle.success)
    async def start(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        if interaction.user.id != self.organizer_id:
            await send_ephemeral(interaction, "ゲームを開始できるのは募集者だけです。")
            return
        if self.started:
            await send_ephemeral(interaction, "すでに開始処理中です。")
            return
        if len(self.participants) < MIN_PLAYERS:
            await send_ephemeral(interaction, "2人以上で開始できます。")
            return
        if interaction.guild_id is None or interaction.channel_id is None:
            await send_ephemeral(interaction, "Discordサーバー内で実行してください。")
            return

        self.started = True
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            session = await self.cog.manager.create_game(
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
                organizer_id=self.organizer_id,
                participants=list(self.participants.values()),
            )
        except ActiveGameExistsError:
            self.started = False
            await interaction.followup.send(
                "このサーバーではすでにDr.Memeかるたが進行中です。",
                ephemeral=True,
            )
            return
        except NotEnoughMemesError:
            self.started = False
            await interaction.followup.send(
                "かるたを開始するにはMemelistに50件以上の有効なミームが必要です。",
                ephemeral=True,
            )
            return
        except VoicevoxUnavailableError:
            self.started = False
            await interaction.followup.send(
                "VOICEVOX ENGINEに接続できないためゲームを開始できません。",
                ephemeral=True,
            )
            return
        except Exception:
            LOGGER.exception("Failed to start karuta game")
            self.started = False
            await interaction.followup.send(
                "Dr.Memeかるたの開始に失敗しました。ログを確認してください。",
                ephemeral=True,
            )
            return

        urls = self.cog.manager.player_urls(session)
        dm_failed: list[str] = []
        for participant in self.participants.values():
            try:
                user = interaction.client.get_user(participant.user_id)
                if user is None:
                    user = await interaction.client.fetch_user(participant.user_id)
                await user.send(
                    "Dr.Memeかるたの専用URLです。\n"
                    f"{urls[participant.user_id]}"
                )
            except discord.HTTPException:
                dm_failed.append(participant.display_name)

        if interaction.message:
            await interaction.message.edit(embed=self.embed(), view=self)

        channel = interaction.channel
        if isinstance(channel, discord.abc.Messageable):
            await channel.send(
                "Dr.Memeかるたを開始しました。参加者へ専用URLをDMしました。"
            )

        if dm_failed:
            await interaction.followup.send(
                "開始しました。ただしDMを送れない参加者がいます: "
                + ", ".join(dm_failed),
                ephemeral=True,
            )
        else:
            await interaction.followup.send("開始しました。", ephemeral=True)


class KarutaCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.manager = bot.karuta_manager
        self.manager.set_discord_notifier(self.notify_game_finished)

    @app_commands.command(name="karuta", description="Dr.Memeかるたの募集を開始します。")
    @app_commands.guild_only()
    async def karuta(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or not isinstance(interaction.user, discord.Member):
            await send_ephemeral(interaction, "Discordサーバー内で実行してください。")
            return

        if interaction.guild_id in self.manager.active_by_guild:
            await send_ephemeral(interaction, "このサーバーではすでにDr.Memeかるたが進行中です。")
            return

        await interaction.response.defer(thinking=True)

        candidate_count = await self.manager.count_candidates(guild_id=interaction.guild_id)
        if candidate_count < 50:
            await send_ephemeral(
                interaction,
                "かるたを開始するにはMemelistに50件以上の有効なミームが必要です。",
            )
            return

        view = KarutaRecruitmentView(self, interaction.user)
        view.channel_id = interaction.channel_id or 0
        await interaction.followup.send(embed=view.embed(), view=view)

    @app_commands.command(name="karuta_stop", description="進行中のDr.Memeかるたを強制終了します。")
    @app_commands.guild_only()
    async def karuta_stop(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or not isinstance(interaction.user, discord.Member):
            await send_ephemeral(interaction, "Discordサーバー内で実行してください。")
            return
        if not can_manage_memes(interaction.user, self.bot.settings):
            await send_ephemeral(interaction, permission_denied_message(self.bot.settings))
            return

        game_id = self.manager.active_by_guild.get(interaction.guild_id)
        if game_id is None:
            await send_ephemeral(interaction, "このサーバーで進行中のDr.Memeかるたはありません。")
            return

        session = self.manager.sessions.get(game_id)
        if session is None:
            self.manager.active_by_guild.pop(interaction.guild_id, None)
            await send_ephemeral(interaction, "残っていたかるた進行中フラグを解除しました。")
            return

        await self.manager.finish_game(session, reason="disbanded")
        await send_ephemeral(interaction, "進行中のDr.Memeかるたを強制終了しました。")

    async def notify_game_finished(self, session) -> None:
        channel = self.bot.get_channel(session.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(session.channel_id)
            except discord.HTTPException:
                LOGGER.exception("Failed to fetch karuta channel: %s", session.channel_id)
                return
        if not isinstance(channel, discord.abc.Messageable):
            return

        title = "Dr.Memeかるた終了！"
        if session.end_reason == "all_mistake":
            title = "全員お手付きのためDr.Memeかるた終了！"
        elif session.end_reason == "disbanded":
            title = "Dr.Memeかるたは解散されました。"

        lines = [title]
        for row in session.result_rows[:10]:
            lines.append(f"{row.rank}位 {row.display_name} {row.cards_won}枚")
        if session.reading_update_count:
            lines.append(f"読み方が{session.reading_update_count}件更新されました。")

        try:
            await channel.send("\n".join(lines))
        except discord.HTTPException:
            LOGGER.exception("Failed to send karuta result.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(KarutaCommands(bot))

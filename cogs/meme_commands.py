from __future__ import annotations

import logging
import math

import discord
from discord import app_commands
from discord.ext import commands

from backends import BackendError, ImageNotFoundError
from database import DuplicateMemeError, Meme, match_type_label, validate_match_type
from image_storage import ImageDownloadError, UnsupportedImageError
from permissions import can_manage_memes, permission_denied_message


PAGE_SIZE = 10
LOGGER = logging.getLogger(__name__)


async def send_ephemeral(interaction: discord.Interaction, content: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True)
    else:
        await interaction.response.send_message(content, ephemeral=True)


class MemeRegisterModal(discord.ui.Modal):
    def __init__(
        self,
        cog: MemeCommands,
        attachment: discord.Attachment,
    ) -> None:
        super().__init__(title="ミームとして登録", timeout=300)
        self.cog = cog
        self.attachment = attachment
        self.keyword_input = discord.ui.TextInput(
            label="キーワード",
            placeholder="例: 何見てんだよ",
            required=True,
            max_length=100,
        )
        self.match_type_input = discord.ui.TextInput(
            label="判定方法 (partial / exact)",
            default="partial",
            placeholder="partial または exact",
            required=True,
            max_length=20,
        )
        self.reading_input = discord.ui.TextInput(
            label="読み方（空欄ならキーワードと同じ）",
            placeholder="例: なんかみてる",
            required=False,
            max_length=100,
        )
        self.add_item(self.keyword_input)
        self.add_item(self.match_type_input)
        self.add_item(self.reading_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.register_attachment(
            interaction,
            keyword=str(self.keyword_input.value),
            match_type=str(self.match_type_input.value),
            reading=str(self.reading_input.value),
            attachment=self.attachment,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        LOGGER.exception("Unexpected modal error", exc_info=error)
        await send_ephemeral(interaction, "ミーム登録中に予期しないエラーが発生しました。")


class MemeCommands(commands.Cog):
    meme = app_commands.Group(
        name="meme",
        description="ネットミーム画像を管理します。",
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.register_context_menu = app_commands.ContextMenu(
            name="ミームとして登録",
            callback=self.register_as_meme,
            allowed_contexts=app_commands.AppCommandContext(
                guild=True,
                dm_channel=False,
                private_channel=False,
            ),
        )
        self.bot.tree.add_command(self.register_context_menu)

    def cog_unload(self) -> None:
        self.bot.tree.remove_command(
            self.register_context_menu.name,
            type=self.register_context_menu.type,
        )

    @property
    def backend(self):
        return self.bot.backend

    @property
    def settings(self):
        return self.bot.settings

    async def _ensure_guild(self, interaction: discord.Interaction) -> int | None:
        if interaction.guild_id is None or interaction.guild is None:
            await send_ephemeral(interaction, "このコマンドはDiscordサーバー内でのみ使用できます。")
            return None
        return interaction.guild_id

    async def _ensure_can_manage(self, interaction: discord.Interaction) -> bool:
        guild_id = await self._ensure_guild(interaction)
        if guild_id is None:
            return False

        if not isinstance(interaction.user, discord.Member):
            await send_ephemeral(interaction, "サーバーメンバー情報を確認できませんでした。")
            return False

        if can_manage_memes(interaction.user, self.settings):
            return True

        await send_ephemeral(interaction, permission_denied_message(self.settings))
        return False

    async def register_attachment(
        self,
        interaction: discord.Interaction,
        *,
        keyword: str,
        match_type: str,
        reading: str | None = None,
        attachment: discord.Attachment,
    ) -> Meme | None:
        guild_id = await self._ensure_guild(interaction)
        if guild_id is None:
            return None

        normalized_keyword = keyword.strip()
        if not normalized_keyword:
            await send_ephemeral(interaction, "キーワードを入力してください。")
            return None

        try:
            normalized_match_type = validate_match_type(match_type)
        except ValueError:
            await send_ephemeral(interaction, "判定方法は partial または exact を指定してください。")
            return None

        if not self.backend.is_supported_attachment(attachment):
            await send_ephemeral(
                interaction,
                "対応している画像形式は jpg / jpeg / png / webp / gif です。",
            )
            return None

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            meme = await self.backend.add_meme_with_attachment(
                guild_id=guild_id,
                keyword=normalized_keyword,
                match_type=normalized_match_type,
                attachment=attachment,
                created_by=interaction.user.id,
                reading=reading,
            )
        except UnsupportedImageError:
            await interaction.followup.send(
                "対応している画像形式は jpg / jpeg / png / webp / gif です。",
                ephemeral=True,
            )
            return None
        except ImageDownloadError:
            await interaction.followup.send(
                "画像のダウンロードに失敗しました。少し待ってからもう一度試してください。",
                ephemeral=True,
            )
            return None
        except DuplicateMemeError:
            await interaction.followup.send(
                "同じキーワードと判定方法のミームが既に登録されています。",
                ephemeral=True,
            )
            return None
        except BackendError:
            LOGGER.exception("Backend failed to register meme")
            await interaction.followup.send(
                "ミーム保存先の処理に失敗しました。設定やログを確認してください。",
                ephemeral=True,
            )
            return None
        except Exception:
            LOGGER.exception("Failed to register meme")
            await interaction.followup.send(
                "ミームの登録に失敗しました。管理者にログを確認してもらってください。",
                ephemeral=True,
            )
            return None

        await interaction.followup.send(
            f"「{meme.keyword}」を登録しました。"
            f" 判定方法: {match_type_label(meme.match_type)} / 読み方: {meme.voice_text}",
            ephemeral=True,
        )
        return meme

    @meme.command(name="add", description="キーワードと画像を指定してミームを登録します。")
    @app_commands.describe(
        keyword="反応させるキーワード",
        image="返信に使う画像ファイル",
        match_type="部分一致または完全一致",
        reading="かるたで読み上げる読み方。空欄ならキーワードと同じ",
    )
    @app_commands.choices(
        match_type=[
            app_commands.Choice(name="部分一致", value="partial"),
            app_commands.Choice(name="完全一致", value="exact"),
        ]
    )
    async def add(
        self,
        interaction: discord.Interaction,
        keyword: str,
        image: discord.Attachment,
        match_type: str,
        reading: str | None = None,
    ) -> None:
        if not await self._ensure_can_manage(interaction):
            return
        await self.register_attachment(
            interaction,
            keyword=keyword,
            match_type=match_type,
            reading=reading,
            attachment=image,
        )

    @meme.command(name="delete", description="登録済みミームを削除します。")
    @app_commands.rename(meme_id="id")
    @app_commands.describe(meme_id="削除するミームID")
    async def delete(self, interaction: discord.Interaction, meme_id: int) -> None:
        if not await self._ensure_can_manage(interaction):
            return

        guild_id = interaction.guild_id
        assert guild_id is not None

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)

        meme = await self.backend.delete_meme(guild_id=guild_id, meme_id=meme_id)
        if meme is None:
            await interaction.followup.send("指定されたIDのミームは見つかりません。", ephemeral=True)
            return

        await interaction.followup.send(f"ID {meme.id} のミームを削除しました。", ephemeral=True)

    @meme.command(name="list", description="このサーバーのミーム一覧を表示します。")
    @app_commands.describe(page="表示するページ番号")
    async def list(self, interaction: discord.Interaction, page: int = 1) -> None:
        guild_id = await self._ensure_guild(interaction)
        if guild_id is None:
            return

        page = max(page, 1)
        total = await self.backend.count_memes(guild_id=guild_id)
        max_page = max(1, math.ceil(total / PAGE_SIZE))
        if page > max_page:
            page = max_page

        memes = await self.backend.list_memes(
            guild_id=guild_id,
            limit=PAGE_SIZE,
            offset=(page - 1) * PAGE_SIZE,
        )

        if not memes:
            await send_ephemeral(interaction, "このサーバーにはまだミームが登録されていません。")
            return

        lines = ["ID | 状態 | 判定 | 回数 | キーワード"]
        for meme in memes:
            status = "有効" if meme.enabled else "無効"
            keyword = meme.keyword.replace("\n", " ")[:40]
            lines.append(
                f"{meme.id} | {status} | {match_type_label(meme.match_type)} | "
                f"{meme.trigger_count} | {keyword}"
            )

        content = f"ミーム一覧 {page}/{max_page}\n```text\n" + "\n".join(lines) + "\n```"
        await send_ephemeral(interaction, content)

    @meme.command(name="show", description="指定IDのミーム詳細と画像を表示します。")
    @app_commands.rename(meme_id="id")
    @app_commands.describe(meme_id="表示するミームID")
    async def show(self, interaction: discord.Interaction, meme_id: int) -> None:
        guild_id = await self._ensure_guild(interaction)
        if guild_id is None:
            return

        meme = await self.backend.get_meme(guild_id=guild_id, meme_id=meme_id)
        if meme is None:
            await send_ephemeral(interaction, "指定されたIDのミームは見つかりません。")
            return

        try:
            file = await self.backend.to_discord_file(meme)
        except ImageNotFoundError:
            await send_ephemeral(interaction, "登録画像ファイルが見つかりません。")
            return
        except BackendError:
            LOGGER.exception("Failed to read meme image")
            await send_ephemeral(interaction, "登録画像の読み込みに失敗しました。")
            return

        embed = discord.Embed(
            title=f"ミーム #{meme.id}",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="キーワード", value=meme.keyword, inline=False)
        embed.add_field(name="読み方", value=meme.voice_text, inline=False)
        embed.add_field(name="判定方法", value=match_type_label(meme.match_type), inline=True)
        embed.add_field(name="状態", value="有効" if meme.enabled else "無効", inline=True)
        embed.add_field(name="発動回数", value=str(meme.trigger_count), inline=True)
        embed.add_field(name="登録者", value=f"<@{meme.created_by}>", inline=True)
        embed.add_field(name="登録日時", value=meme.created_at, inline=True)
        embed.set_image(url=f"attachment://{file.filename}")

        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, file=file, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, file=file, ephemeral=True)

    @meme.command(name="edit", description="登録済みミームのキーワードや判定方法を編集します。")
    @app_commands.rename(meme_id="id")
    @app_commands.describe(
        meme_id="編集するミームID",
        keyword="新しいキーワード",
        match_type="新しい判定方法",
        reading="新しい読み方。空欄ならキーワードと同じ",
    )
    @app_commands.choices(
        match_type=[
            app_commands.Choice(name="部分一致", value="partial"),
            app_commands.Choice(name="完全一致", value="exact"),
        ]
    )
    async def edit(
        self,
        interaction: discord.Interaction,
        meme_id: int,
        keyword: str | None = None,
        match_type: str | None = None,
        reading: str | None = None,
    ) -> None:
        if not await self._ensure_can_manage(interaction):
            return

        next_keyword = keyword.strip() if keyword is not None else None
        if next_keyword == "":
            await send_ephemeral(interaction, "キーワードを空にはできません。")
            return

        if next_keyword is None and match_type is None and reading is None:
            await send_ephemeral(interaction, "変更するキーワード、判定方法、読み方のいずれかを指定してください。")
            return

        guild_id = interaction.guild_id
        assert guild_id is not None

        try:
            updated = await self.backend.update_meme(
                guild_id=guild_id,
                meme_id=meme_id,
                keyword=next_keyword,
                match_type=match_type,
                reading=reading,
                updated_by=interaction.user.id,
            )
        except DuplicateMemeError:
            await send_ephemeral(
                interaction,
                "同じキーワードと判定方法のミームが既に登録されています。",
            )
            return

        if updated is None:
            await send_ephemeral(interaction, "指定されたIDのミームは見つかりません。")
            return

        await send_ephemeral(
            interaction,
            f"ID {updated.id} を更新しました。"
            f" キーワード: {updated.keyword} / 判定方法: {match_type_label(updated.match_type)}"
            f" / 読み方: {updated.voice_text}",
        )

    @meme.command(name="enable", description="指定ミームを有効化します。")
    @app_commands.rename(meme_id="id")
    @app_commands.describe(meme_id="有効化するミームID")
    async def enable(self, interaction: discord.Interaction, meme_id: int) -> None:
        await self._set_enabled(interaction, meme_id=meme_id, enabled=True)

    @meme.command(name="disable", description="指定ミームを無効化します。")
    @app_commands.rename(meme_id="id")
    @app_commands.describe(meme_id="無効化するミームID")
    async def disable(self, interaction: discord.Interaction, meme_id: int) -> None:
        await self._set_enabled(interaction, meme_id=meme_id, enabled=False)

    async def _set_enabled(
        self,
        interaction: discord.Interaction,
        *,
        meme_id: int,
        enabled: bool,
    ) -> None:
        if not await self._ensure_can_manage(interaction):
            return

        guild_id = interaction.guild_id
        assert guild_id is not None

        meme = await self.backend.set_enabled(guild_id=guild_id, meme_id=meme_id, enabled=enabled)
        if meme is None:
            await send_ephemeral(interaction, "指定されたIDのミームは見つかりません。")
            return

        status = "有効化" if enabled else "無効化"
        await send_ephemeral(interaction, f"ID {meme.id} を{status}しました。")

    async def register_as_meme(
        self,
        interaction: discord.Interaction,
        message: discord.Message,
    ) -> None:
        if not await self._ensure_can_manage(interaction):
            return

        attachment = self.backend.first_supported_attachment(message.attachments)
        if attachment is None:
            await send_ephemeral(interaction, "このメッセージには登録可能な画像がありません。")
            return

        await interaction.response.send_modal(MemeRegisterModal(self, attachment))

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        LOGGER.exception("Application command failed", exc_info=error)
        await send_ephemeral(interaction, "コマンドの実行中にエラーが発生しました。")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MemeCommands(bot))

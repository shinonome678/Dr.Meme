from __future__ import annotations

import discord

from config import Settings


def can_manage_memes(member: discord.Member, settings: Settings) -> bool:
    if settings.allow_everyone_to_edit:
        return True

    if member.guild_permissions.administrator:
        return True

    return any(role.name == settings.meme_editor_role for role in member.roles)


def permission_denied_message(settings: Settings) -> str:
    return (
        "この操作にはサーバー管理者権限、または "
        f"「{settings.meme_editor_role}」ロールが必要です。"
    )

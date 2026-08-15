from __future__ import annotations

import io
import logging
import mimetypes
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import aiohttp
import discord

from config import Settings
from database import DuplicateMemeError, Meme, MemeDatabase, validate_match_type
from image_storage import (
    AttachmentLike,
    ImageDownloadError,
    ImageStorage,
    UnsupportedImageError,
)


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImagePayload:
    data: bytes
    content_type: str
    filename: str


class BackendError(Exception):
    """保存バックエンドの操作に失敗しました。"""


class ImageNotFoundError(BackendError):
    """登録されている画像が見つかりません。"""


class MemeBackend(Protocol):
    async def initialize(self) -> None:
        ...

    async def close(self) -> None:
        ...

    def is_supported_attachment(self, attachment: AttachmentLike) -> bool:
        ...

    def first_supported_attachment(
        self,
        attachments: list[discord.Attachment],
    ) -> discord.Attachment | None:
        ...

    async def add_meme_with_attachment(
        self,
        *,
        guild_id: int,
        keyword: str,
        match_type: str,
        attachment: discord.Attachment,
        created_by: int,
    ) -> Meme:
        ...

    async def delete_meme(self, *, guild_id: int, meme_id: int) -> Meme | None:
        ...

    async def get_meme(self, *, guild_id: int, meme_id: int) -> Meme | None:
        ...

    async def list_memes(self, *, guild_id: int, limit: int, offset: int = 0) -> list[Meme]:
        ...

    async def search_memes(
        self,
        *,
        guild_id: int,
        query: str,
        limit: int,
        offset: int = 0,
    ) -> list[Meme]:
        ...

    async def count_memes(self, *, guild_id: int) -> int:
        ...

    async def count_search_memes(self, *, guild_id: int, query: str) -> int:
        ...

    async def list_enabled_memes(self, *, guild_id: int) -> list[Meme]:
        ...

    async def list_karuta_candidates(self, *, guild_id: int) -> list[Meme]:
        ...

    async def update_meme(
        self,
        *,
        guild_id: int,
        meme_id: int,
        keyword: str | None = None,
        match_type: str | None = None,
    ) -> Meme | None:
        ...

    async def set_enabled(
        self,
        *,
        guild_id: int,
        meme_id: int,
        enabled: bool,
    ) -> Meme | None:
        ...

    async def update_meme_reading(
        self,
        *,
        guild_id: int,
        meme_id: int,
        reading: str | None,
        updated_by: int,
    ) -> Meme | None:
        ...

    async def update_meme_readings(
        self,
        *,
        guild_id: int,
        changes: dict[int, tuple[str | None, int]],
    ) -> int:
        ...

    async def increment_trigger_count(self, *, guild_id: int, meme_id: int) -> None:
        ...

    async def to_discord_file(self, meme: Meme) -> discord.File:
        ...

    async def read_meme_image(self, meme: Meme) -> ImagePayload:
        ...


class LocalMemeBackend:
    def __init__(self, settings: Settings) -> None:
        self.db = MemeDatabase(settings.db_path)
        self.storage = ImageStorage(
            data_dir=settings.data_dir,
            images_dir=settings.images_dir,
        )

    async def initialize(self) -> None:
        self.db.initialize()
        self.storage.initialize()

    async def close(self) -> None:
        self.db.close()

    def is_supported_attachment(self, attachment: AttachmentLike) -> bool:
        return self.storage.is_supported_attachment(attachment)

    def first_supported_attachment(
        self,
        attachments: list[discord.Attachment],
    ) -> discord.Attachment | None:
        return self.storage.first_supported_attachment(attachments)

    async def add_meme_with_attachment(
        self,
        *,
        guild_id: int,
        keyword: str,
        match_type: str,
        attachment: discord.Attachment,
        created_by: int,
    ) -> Meme:
        if self.db.duplicate_exists(
            guild_id=guild_id,
            keyword=keyword,
            match_type=match_type,
        ):
            raise DuplicateMemeError

        relative_path: str | None = None
        try:
            relative_path = await self.storage.save_attachment(attachment)
            return self.db.add_meme(
                guild_id=guild_id,
                keyword=keyword,
                match_type=match_type,
                image_path=relative_path,
                created_by=created_by,
            )
        except Exception:
            if relative_path is not None:
                self.storage.delete_image(relative_path)
            raise

    async def delete_meme(self, *, guild_id: int, meme_id: int) -> Meme | None:
        meme = self.db.delete_meme(guild_id=guild_id, meme_id=meme_id)
        if meme is not None:
            self.storage.delete_image(meme.image_path)
        return meme

    async def get_meme(self, *, guild_id: int, meme_id: int) -> Meme | None:
        return self.db.get_meme(guild_id=guild_id, meme_id=meme_id)

    async def list_memes(self, *, guild_id: int, limit: int, offset: int = 0) -> list[Meme]:
        return self.db.list_memes(guild_id=guild_id, limit=limit, offset=offset)

    async def search_memes(
        self,
        *,
        guild_id: int,
        query: str,
        limit: int,
        offset: int = 0,
    ) -> list[Meme]:
        if not query.strip():
            return await self.list_memes(guild_id=guild_id, limit=limit, offset=offset)
        return self.db.search_memes(
            guild_id=guild_id,
            query=query.strip(),
            limit=limit,
            offset=offset,
        )

    async def count_memes(self, *, guild_id: int) -> int:
        return self.db.count_memes(guild_id=guild_id)

    async def count_search_memes(self, *, guild_id: int, query: str) -> int:
        if not query.strip():
            return await self.count_memes(guild_id=guild_id)
        return self.db.count_search_memes(guild_id=guild_id, query=query.strip())

    async def list_enabled_memes(self, *, guild_id: int) -> list[Meme]:
        return self.db.list_enabled_memes(guild_id=guild_id)

    async def list_karuta_candidates(self, *, guild_id: int) -> list[Meme]:
        memes = await self.list_enabled_memes(guild_id=guild_id)
        return [
            meme
            for meme in memes
            if self.storage.path_for(meme.image_path).is_file()
        ]

    async def update_meme(
        self,
        *,
        guild_id: int,
        meme_id: int,
        keyword: str | None = None,
        match_type: str | None = None,
    ) -> Meme | None:
        return self.db.update_meme(
            guild_id=guild_id,
            meme_id=meme_id,
            keyword=keyword,
            match_type=match_type,
        )

    async def set_enabled(
        self,
        *,
        guild_id: int,
        meme_id: int,
        enabled: bool,
    ) -> Meme | None:
        return self.db.set_enabled(guild_id=guild_id, meme_id=meme_id, enabled=enabled)

    async def update_meme_reading(
        self,
        *,
        guild_id: int,
        meme_id: int,
        reading: str | None,
        updated_by: int,
    ) -> Meme | None:
        return self.db.update_meme_reading(
            guild_id=guild_id,
            meme_id=meme_id,
            reading=reading,
            updated_by=updated_by,
        )

    async def update_meme_readings(
        self,
        *,
        guild_id: int,
        changes: dict[int, tuple[str | None, int]],
    ) -> int:
        return self.db.update_meme_readings(guild_id=guild_id, changes=changes)

    async def increment_trigger_count(self, *, guild_id: int, meme_id: int) -> None:
        self.db.increment_trigger_count(guild_id=guild_id, meme_id=meme_id)

    async def to_discord_file(self, meme: Meme) -> discord.File:
        image_path = self.storage.path_for(meme.image_path)
        if not image_path.is_file():
            raise ImageNotFoundError
        return discord.File(image_path, filename=image_path.name)

    async def read_meme_image(self, meme: Meme) -> ImagePayload:
        image_path = self.storage.path_for(meme.image_path)
        try:
            image_path.relative_to(self.storage.images_dir.resolve())
        except ValueError as exc:
            raise ImageNotFoundError from exc
        if not image_path.is_file():
            raise ImageNotFoundError
        content_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
        return ImagePayload(
            data=image_path.read_bytes(),
            content_type=content_type,
            filename=image_path.name,
        )


class SupabaseRequestError(BackendError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Supabase request failed: {status} {body[:300]}")
        self.status = status
        self.body = body


class SupabaseMemeBackend:
    def __init__(self, settings: Settings) -> None:
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise SystemExit(
                "BACKEND=supabase の場合は SUPABASE_URL と "
                "SUPABASE_SERVICE_ROLE_KEY を設定してください。"
            )

        self.url = settings.supabase_url.rstrip("/")
        self.service_role_key = settings.supabase_service_role_key
        self.bucket = settings.supabase_bucket
        self.attachment_helper = ImageStorage(
            data_dir=settings.data_dir,
            images_dir=settings.images_dir,
        )
        self.session: aiohttp.ClientSession | None = None

    async def initialize(self) -> None:
        self.session = aiohttp.ClientSession()

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None

    def is_supported_attachment(self, attachment: AttachmentLike) -> bool:
        return self.attachment_helper.is_supported_attachment(attachment)

    def first_supported_attachment(
        self,
        attachments: list[discord.Attachment],
    ) -> discord.Attachment | None:
        return self.attachment_helper.first_supported_attachment(attachments)

    def _headers(self, *, content_type: str | None = "application/json") -> dict[str, str]:
        headers = {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
        }
        if content_type is not None:
            headers["Content-Type"] = content_type
        return headers

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: Any | None = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        if self.session is None:
            raise BackendError("Supabase backend is not initialized.")

        request_headers = self._headers()
        if headers:
            request_headers.update(headers)

        async with self.session.request(
            method,
            f"{self.url}{path}",
            params=params,
            json=json_body,
            data=data,
            headers=request_headers,
        ) as response:
            text = await response.text()
            if response.status >= 400:
                raise SupabaseRequestError(response.status, text)
            if not text:
                return None
            content_type = response.headers.get("Content-Type", "")
            if "application/json" not in content_type:
                return text
            return await response.json()

    async def _request_bytes(self, method: str, path: str) -> bytes:
        if self.session is None:
            raise BackendError("Supabase backend is not initialized.")

        async with self.session.request(
            method,
            f"{self.url}{path}",
            headers=self._headers(content_type=None),
        ) as response:
            data = await response.read()
            if response.status == 404:
                raise ImageNotFoundError
            if response.status >= 400:
                raise SupabaseRequestError(response.status, data[:300].decode(errors="replace"))
            return data

    def _meme_from_row(self, row: dict[str, Any]) -> Meme:
        return Meme(
            id=int(row["id"]),
            guild_id=int(row["guild_id"]),
            keyword=str(row["keyword"]),
            match_type=str(row["match_type"]),
            image_path=str(row["image_path"]),
            created_by=int(row["created_by"]),
            created_at=str(row["created_at"]),
            enabled=bool(row["enabled"]),
            trigger_count=int(row["trigger_count"]),
            reading=str(row["reading"]) if row.get("reading") is not None else None,
            reading_updated_by=(
                int(row["reading_updated_by"])
                if row.get("reading_updated_by") is not None
                else None
            ),
            reading_updated_at=(
                str(row["reading_updated_at"])
                if row.get("reading_updated_at") is not None
                else None
            ),
        )

    async def _upload_attachment(
        self,
        *,
        guild_id: int,
        attachment: discord.Attachment,
    ) -> str:
        try:
            extension = self.attachment_helper.extension_for_attachment(attachment)
            file_bytes = await attachment.read(use_cached=False)
        except UnsupportedImageError:
            raise
        except Exception as exc:
            raise ImageDownloadError from exc

        object_path = f"{guild_id}/{uuid.uuid4().hex}{extension}"
        content_type = attachment.content_type or "application/octet-stream"
        try:
            await self._request_json(
                "POST",
                f"/storage/v1/object/{self.bucket}/{quote(object_path, safe='/')}",
                data=file_bytes,
                headers={
                    "Content-Type": content_type,
                    "x-upsert": "false",
                },
            )
        except Exception as exc:
            LOGGER.exception("Failed to upload image to Supabase Storage")
            raise ImageDownloadError from exc

        return object_path

    async def _delete_storage_object(self, image_path: str) -> None:
        try:
            await self._request_json(
                "DELETE",
                f"/storage/v1/object/{self.bucket}",
                json_body={"prefixes": [image_path]},
            )
        except Exception:
            LOGGER.exception("Failed to delete Supabase Storage object: %s", image_path)

    async def add_meme_with_attachment(
        self,
        *,
        guild_id: int,
        keyword: str,
        match_type: str,
        attachment: discord.Attachment,
        created_by: int,
    ) -> Meme:
        if await self.duplicate_exists(
            guild_id=guild_id,
            keyword=keyword,
            match_type=match_type,
        ):
            raise DuplicateMemeError

        image_path: str | None = None
        try:
            image_path = await self._upload_attachment(guild_id=guild_id, attachment=attachment)
            rows = await self._request_json(
                "POST",
                "/rest/v1/memes",
                params={"select": "*"},
                json_body={
                    "guild_id": guild_id,
                    "keyword": keyword,
                    "match_type": validate_match_type(match_type),
                    "image_path": image_path,
                    "created_by": created_by,
                },
                headers={"Prefer": "return=representation"},
            )
        except SupabaseRequestError as exc:
            if image_path is not None:
                await self._delete_storage_object(image_path)
            if exc.status == 409 or "duplicate" in exc.body.lower():
                raise DuplicateMemeError from exc
            raise
        except Exception:
            if image_path is not None:
                await self._delete_storage_object(image_path)
            raise

        if not rows:
            raise BackendError("Supabase did not return inserted meme.")
        return self._meme_from_row(rows[0])

    async def duplicate_exists(self, *, guild_id: int, keyword: str, match_type: str) -> bool:
        rows = await self._request_json(
            "GET",
            "/rest/v1/memes",
            params={
                "select": "id",
                "guild_id": f"eq.{guild_id}",
                "keyword": f"eq.{keyword}",
                "match_type": f"eq.{match_type}",
                "limit": "1",
            },
        )
        return bool(rows)

    async def delete_meme(self, *, guild_id: int, meme_id: int) -> Meme | None:
        meme = await self.get_meme(guild_id=guild_id, meme_id=meme_id)
        if meme is None:
            return None

        await self._request_json(
            "DELETE",
            "/rest/v1/memes",
            params={
                "guild_id": f"eq.{guild_id}",
                "id": f"eq.{meme_id}",
            },
        )
        await self._delete_storage_object(meme.image_path)
        return meme

    async def get_meme(self, *, guild_id: int, meme_id: int) -> Meme | None:
        rows = await self._request_json(
            "GET",
            "/rest/v1/memes",
            params={
                "select": "*",
                "guild_id": f"eq.{guild_id}",
                "id": f"eq.{meme_id}",
                "limit": "1",
            },
        )
        return self._meme_from_row(rows[0]) if rows else None

    async def list_memes(self, *, guild_id: int, limit: int, offset: int = 0) -> list[Meme]:
        rows = await self._request_json(
            "GET",
            "/rest/v1/memes",
            params={
                "select": "*",
                "guild_id": f"eq.{guild_id}",
                "order": "id.asc",
                "limit": str(limit),
                "offset": str(offset),
            },
        )
        return [self._meme_from_row(row) for row in rows]

    async def search_memes(
        self,
        *,
        guild_id: int,
        query: str,
        limit: int,
        offset: int = 0,
    ) -> list[Meme]:
        query = query.strip().lower()
        if not query:
            return await self.list_memes(guild_id=guild_id, limit=limit, offset=offset)

        rows = await self._request_json(
            "GET",
            "/rest/v1/memes",
            params={
                "select": "*",
                "guild_id": f"eq.{guild_id}",
                "order": "id.asc",
                "limit": "1000",
            },
        )
        memes = [
            self._meme_from_row(row)
            for row in rows
            if query in str(row.get("keyword", "")).lower()
            or query in str(row.get("reading", "")).lower()
            or query == str(row.get("id", ""))
        ]
        return memes[offset : offset + limit]

    async def count_memes(self, *, guild_id: int) -> int:
        rows = await self._request_json(
            "GET",
            "/rest/v1/memes",
            params={
                "select": "id",
                "guild_id": f"eq.{guild_id}",
            },
        )
        return len(rows)

    async def count_search_memes(self, *, guild_id: int, query: str) -> int:
        if not query.strip():
            return await self.count_memes(guild_id=guild_id)
        return len(
            await self.search_memes(
                guild_id=guild_id,
                query=query,
                limit=1000,
                offset=0,
            )
        )

    async def list_enabled_memes(self, *, guild_id: int) -> list[Meme]:
        rows = await self._request_json(
            "GET",
            "/rest/v1/memes",
            params={
                "select": "*",
                "guild_id": f"eq.{guild_id}",
                "enabled": "eq.true",
                "order": "id.asc",
            },
        )
        return [self._meme_from_row(row) for row in rows]

    async def list_karuta_candidates(self, *, guild_id: int) -> list[Meme]:
        return await self.list_enabled_memes(guild_id=guild_id)

    async def update_meme(
        self,
        *,
        guild_id: int,
        meme_id: int,
        keyword: str | None = None,
        match_type: str | None = None,
    ) -> Meme | None:
        body: dict[str, str] = {}
        if keyword is not None:
            body["keyword"] = keyword
        if match_type is not None:
            body["match_type"] = validate_match_type(match_type)

        try:
            rows = await self._request_json(
                "PATCH",
                "/rest/v1/memes",
                params={
                    "select": "*",
                    "guild_id": f"eq.{guild_id}",
                    "id": f"eq.{meme_id}",
                },
                json_body=body,
                headers={"Prefer": "return=representation"},
            )
        except SupabaseRequestError as exc:
            if exc.status == 409 or "duplicate" in exc.body.lower():
                raise DuplicateMemeError from exc
            raise
        return self._meme_from_row(rows[0]) if rows else None

    async def set_enabled(
        self,
        *,
        guild_id: int,
        meme_id: int,
        enabled: bool,
    ) -> Meme | None:
        rows = await self._request_json(
            "PATCH",
            "/rest/v1/memes",
            params={
                "select": "*",
                "guild_id": f"eq.{guild_id}",
                "id": f"eq.{meme_id}",
            },
            json_body={"enabled": enabled},
            headers={"Prefer": "return=representation"},
        )
        return self._meme_from_row(rows[0]) if rows else None

    async def update_meme_reading(
        self,
        *,
        guild_id: int,
        meme_id: int,
        reading: str | None,
        updated_by: int,
    ) -> Meme | None:
        normalized_reading = reading.strip() if reading is not None else None
        if normalized_reading == "":
            normalized_reading = None
        rows = await self._request_json(
            "PATCH",
            "/rest/v1/memes",
            params={
                "select": "*",
                "guild_id": f"eq.{guild_id}",
                "id": f"eq.{meme_id}",
            },
            json_body={
                "reading": normalized_reading,
                "reading_updated_by": updated_by,
                "reading_updated_at": datetime.now(timezone.utc).isoformat(),
            },
            headers={"Prefer": "return=representation"},
        )
        return self._meme_from_row(rows[0]) if rows else None

    async def update_meme_readings(
        self,
        *,
        guild_id: int,
        changes: dict[int, tuple[str | None, int]],
    ) -> int:
        updated = 0
        for meme_id, (reading, updated_by) in changes.items():
            if await self.update_meme_reading(
                guild_id=guild_id,
                meme_id=meme_id,
                reading=reading,
                updated_by=updated_by,
            ):
                updated += 1
        return updated

    async def increment_trigger_count(self, *, guild_id: int, meme_id: int) -> None:
        await self._request_json(
            "POST",
            "/rest/v1/rpc/increment_meme_trigger_count",
            json_body={
                "p_guild_id": guild_id,
                "p_meme_id": meme_id,
            },
        )

    async def to_discord_file(self, meme: Meme) -> discord.File:
        data = await self._request_bytes(
            "GET",
            f"/storage/v1/object/{self.bucket}/{quote(meme.image_path, safe='/')}",
        )
        filename = Path(meme.image_path).name
        return discord.File(io.BytesIO(data), filename=filename)

    async def read_meme_image(self, meme: Meme) -> ImagePayload:
        data = await self._request_bytes(
            "GET",
            f"/storage/v1/object/{self.bucket}/{quote(meme.image_path, safe='/')}",
        )
        filename = Path(meme.image_path).name
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return ImagePayload(data=data, content_type=content_type, filename=filename)


def create_backend(settings: Settings) -> MemeBackend:
    if settings.backend == "local":
        return LocalMemeBackend(settings)
    if settings.backend == "supabase":
        return SupabaseMemeBackend(settings)
    raise SystemExit("BACKEND は local または supabase を指定してください。")

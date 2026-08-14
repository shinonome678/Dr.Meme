from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Protocol


SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
CONTENT_TYPE_TO_EXTENSION = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


class AttachmentLike(Protocol):
    filename: str
    content_type: str | None

    async def save(self, fp: str | Path, *, seek_begin: bool = True, use_cached: bool = False) -> int:
        ...


class UnsupportedImageError(Exception):
    """対応していない添付ファイルです。"""


class ImageDownloadError(Exception):
    """画像の保存に失敗しました。"""


class ImageStorage:
    def __init__(self, *, data_dir: Path, images_dir: Path) -> None:
        self.data_dir = data_dir
        self.images_dir = images_dir

    def initialize(self) -> None:
        self.images_dir.mkdir(parents=True, exist_ok=True)

    def extension_for_attachment(self, attachment: AttachmentLike) -> str:
        suffix = Path(attachment.filename).suffix.lower()
        if suffix in SUPPORTED_IMAGE_EXTENSIONS:
            return suffix

        content_type = (attachment.content_type or "").lower()
        if content_type in CONTENT_TYPE_TO_EXTENSION:
            return CONTENT_TYPE_TO_EXTENSION[content_type]

        raise UnsupportedImageError

    def is_supported_attachment(self, attachment: AttachmentLike) -> bool:
        try:
            self.extension_for_attachment(attachment)
        except UnsupportedImageError:
            return False
        return True

    def first_supported_attachment(self, attachments: list[AttachmentLike]) -> AttachmentLike | None:
        for attachment in attachments:
            if self.is_supported_attachment(attachment):
                return attachment
        return None

    async def save_attachment(self, attachment: AttachmentLike) -> str:
        extension = self.extension_for_attachment(attachment)
        self.initialize()

        filename = f"{uuid.uuid4().hex}{extension}"
        destination = self.images_dir / filename
        try:
            await attachment.save(destination, use_cached=False)
        except Exception as exc:
            logging.exception("Failed to save attachment: %s", attachment.filename)
            raise ImageDownloadError from exc

        relative_path = destination.relative_to(self.data_dir)
        return relative_path.as_posix()

    def path_for(self, relative_path: str) -> Path:
        return (self.data_dir / relative_path).resolve()

    def delete_image(self, relative_path: str) -> bool:
        try:
            target = self.path_for(relative_path)
            target.relative_to(self.images_dir.resolve())
        except ValueError:
            logging.warning("Refused to delete image outside images dir: %s", relative_path)
            return False

        if not target.exists():
            return False

        try:
            target.unlink()
        except OSError:
            logging.exception("Failed to delete image: %s", target)
            return False
        return True

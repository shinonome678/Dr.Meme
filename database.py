from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


VALID_MATCH_TYPES = {"partial", "exact"}


class MemeDatabaseError(Exception):
    """DB操作に失敗したときの基底例外です。"""


class DuplicateMemeError(MemeDatabaseError):
    """同じguild/keyword/match_typeのミームが既に存在します。"""


@dataclass(frozen=True)
class Meme:
    id: int
    guild_id: int
    keyword: str
    match_type: str
    image_path: str
    created_by: int
    created_at: str
    enabled: bool
    trigger_count: int


def _row_to_meme(row: sqlite3.Row) -> Meme:
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
    )


class MemeDatabase:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    keyword TEXT NOT NULL,
                    match_type TEXT NOT NULL CHECK(match_type IN ('partial', 'exact')),
                    image_path TEXT NOT NULL,
                    created_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    enabled INTEGER NOT NULL DEFAULT 1,
                    trigger_count INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(guild_id, keyword, match_type)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memes_guild_enabled
                ON memes(guild_id, enabled)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memes_guild_keyword
                ON memes(guild_id, keyword)
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def add_meme(
        self,
        *,
        guild_id: int,
        keyword: str,
        match_type: str,
        image_path: str,
        created_by: int,
    ) -> Meme:
        if match_type not in VALID_MATCH_TYPES:
            raise ValueError(f"Unknown match_type: {match_type}")

        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO memes (
                        guild_id,
                        keyword,
                        match_type,
                        image_path,
                        created_by
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (guild_id, keyword, match_type, image_path, created_by),
                )
                meme_id = int(cursor.lastrowid)
                row = conn.execute(
                    "SELECT * FROM memes WHERE id = ?",
                    (meme_id,),
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise DuplicateMemeError from exc

        if row is None:
            raise MemeDatabaseError("登録したミームを読み戻せませんでした。")
        return _row_to_meme(row)

    def duplicate_exists(self, *, guild_id: int, keyword: str, match_type: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM memes
                WHERE guild_id = ? AND keyword = ? AND match_type = ?
                LIMIT 1
                """,
                (guild_id, keyword, match_type),
            ).fetchone()
        return row is not None

    def get_meme(self, *, guild_id: int, meme_id: int) -> Meme | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memes WHERE guild_id = ? AND id = ?",
                (guild_id, meme_id),
            ).fetchone()
        return _row_to_meme(row) if row else None

    def list_memes(self, *, guild_id: int, limit: int, offset: int = 0) -> list[Meme]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM memes
                WHERE guild_id = ?
                ORDER BY id ASC
                LIMIT ? OFFSET ?
                """,
                (guild_id, limit, offset),
            ).fetchall()
        return [_row_to_meme(row) for row in rows]

    def count_memes(self, *, guild_id: int) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM memes WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
        return int(row["total"]) if row else 0

    def list_enabled_memes(self, *, guild_id: int) -> list[Meme]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM memes
                WHERE guild_id = ? AND enabled = 1
                ORDER BY id ASC
                """,
                (guild_id,),
            ).fetchall()
        return [_row_to_meme(row) for row in rows]

    def update_meme(
        self,
        *,
        guild_id: int,
        meme_id: int,
        keyword: str | None = None,
        match_type: str | None = None,
    ) -> Meme | None:
        current = self.get_meme(guild_id=guild_id, meme_id=meme_id)
        if current is None:
            return None

        next_keyword = keyword if keyword is not None else current.keyword
        next_match_type = match_type if match_type is not None else current.match_type
        if next_match_type not in VALID_MATCH_TYPES:
            raise ValueError(f"Unknown match_type: {next_match_type}")

        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE memes
                    SET keyword = ?, match_type = ?
                    WHERE guild_id = ? AND id = ?
                    """,
                    (next_keyword, next_match_type, guild_id, meme_id),
                )
                row = conn.execute(
                    "SELECT * FROM memes WHERE guild_id = ? AND id = ?",
                    (guild_id, meme_id),
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise DuplicateMemeError from exc

        return _row_to_meme(row) if row else None

    def set_enabled(
        self,
        *,
        guild_id: int,
        meme_id: int,
        enabled: bool,
    ) -> Meme | None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE memes
                SET enabled = ?
                WHERE guild_id = ? AND id = ?
                """,
                (1 if enabled else 0, guild_id, meme_id),
            )
            row = conn.execute(
                "SELECT * FROM memes WHERE guild_id = ? AND id = ?",
                (guild_id, meme_id),
            ).fetchone()
        return _row_to_meme(row) if row else None

    def delete_meme(self, *, guild_id: int, meme_id: int) -> Meme | None:
        meme = self.get_meme(guild_id=guild_id, meme_id=meme_id)
        if meme is None:
            return None
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM memes WHERE guild_id = ? AND id = ?",
                (guild_id, meme_id),
            )
        return meme

    def increment_trigger_count(self, *, guild_id: int, meme_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE memes
                SET trigger_count = trigger_count + 1
                WHERE guild_id = ? AND id = ?
                """,
                (guild_id, meme_id),
            )

    def close(self) -> None:
        # 接続は操作ごとに閉じるため、Bot終了時に必要な処理はありません。
        return None


def validate_match_type(match_type: str) -> str:
    normalized = match_type.strip().lower()
    if normalized in {"partial", "部分一致"}:
        return "partial"
    if normalized in {"exact", "完全一致"}:
        return "exact"
    raise ValueError("判定方法は partial / exact のどちらかを指定してください。")


def match_type_label(match_type: str) -> str:
    return "部分一致" if match_type == "partial" else "完全一致"


def ids(memes: Iterable[Meme]) -> list[int]:
    return [meme.id for meme in memes]

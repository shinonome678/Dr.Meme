from __future__ import annotations

import hmac
import secrets


def new_game_id() -> str:
    return secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:16]


def new_player_token() -> str:
    return secrets.token_urlsafe(32)


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))

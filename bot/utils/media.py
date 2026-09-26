"""Подписанные ссылки на фото, загруженные в бота (для Telegraph-страниц)."""
from __future__ import annotations

import hashlib
import hmac


def sign_file_id(bot_token: str, file_id: str) -> str:
    return hmac.new(bot_token.encode(), ("media:" + file_id).encode(), hashlib.sha256).hexdigest()[:24]


def media_path(bot_token: str, file_id: str) -> str:
    return f"/media/{sign_file_id(bot_token, file_id)}/{file_id}.jpg"


def verify(bot_token: str, signature: str, file_id: str) -> bool:
    return hmac.compare_digest(sign_file_id(bot_token, file_id), signature)

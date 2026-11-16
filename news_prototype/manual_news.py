"""Validate a manually supplied story for a future private bot handler.

No bot routing, persistence, fetching, TTS or publishing is enabled here. A URL
is retained as an untrusted reference and is never fetched by this function.
"""
from __future__ import annotations
from datetime import datetime, timezone
from news_prototype.news_sources import safe_link


def make_candidate(*, user_id: int, admin_id: int, chat_type: str, message_id: int,
                   text: str, accent: str = '', url: str = '', now: datetime | None = None) -> dict:
    if type(user_id) is not int or type(admin_id) is not int or user_id <= 0 or user_id != admin_id or chat_type != 'private':
        raise PermissionError('Добавление доступно владельцу в личном чате')
    if type(message_id) is not int or message_id <= 0:
        raise ValueError('Неверный идентификатор сообщения')
    if not isinstance(text, str) or not isinstance(accent, str) or not isinstance(url, str):
        raise ValueError('Неверные данные новости')
    text, accent, url = text.strip(), accent.strip(), url.strip()
    if len(text) > 8000 or len(accent) > 1500 or len(url) > 2048:
        raise ValueError('Новость или акцент слишком длинные')
    if not text and not url:
        raise ValueError('Нужен текст новости или ссылка')
    link = safe_link(url) if url else None
    if url and link is None:
        raise ValueError('Неверная ссылка на источник')
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise ValueError('Время должно включать часовой пояс')
    return {'id': f'manual:{user_id}:{message_id}', 'source_id': 'manual', 'owner_id': user_id,
            'text': text, 'accent': accent, 'url': link,
            'collected_at': timestamp.astimezone(timezone.utc).isoformat(),
            'status': 'candidate', 'verification': 'unverified'}

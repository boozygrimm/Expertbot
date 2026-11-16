"""Single, manually reviewed trial admitted by the owner through the bot.

Uses isolated Mongo collections, not the ordinary news scheduler. The owner
chooses the text; no model, source fetch, channel publication or group send is
started here. A persistent slot and content-derived ID prevent double renders.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import re

from news_prototype.article_fetch import validate_url, ArticleError
from news_prototype.news_queue import enqueue_script
from news_prototype.story_audio import validate_script

OTHER_FLAGS = ('NEWS_INTAKE_ENABLED', 'NEWS_COLLECTION_ENABLED', 'NEWS_EDITOR_ENABLED',
               'NEWS_RENDER_QUEUE_ENABLED', 'NEWS_REVIEW_ENABLED')


def enabled() -> bool:
    return (os.getenv('NEWS_OWNER_TRIAL_ENABLED', '').strip() == '1'
            and all(os.getenv(name, '').strip() != '1' for name in OTHER_FLAGS))


def parse_message(message: str) -> tuple[str, dict]:
    if not isinstance(message, str) or len(message) > 4096:
        raise ValueError('Неверное сообщение')
    lines = message.splitlines()
    if not lines or not re.fullmatch(r'/news_trial(?:@[A-Za-z0-9_]+)?\s*', lines[0], re.I):
        raise ValueError('Нужна команда /news_trial')
    url = ticker = None
    turns = []
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        name, sep, text = line.partition(':')
        text = text.strip()
        if not sep or not text:
            raise ValueError('Каждая строка должна начинаться с роли или поля')
        if name.casefold() == 'источник' and url is None:
            try:
                url = validate_url(text)
            except ArticleError:
                raise ValueError('Нужен HTTPS-источник') from None
        elif name.casefold() == 'строка' and ticker is None:
            ticker = text
        elif name in ('Бендер', 'Киберсенсей'):
            turns.append({'speaker': name, 'text': text})
        else:
            raise ValueError('Неизвестная или повторная строка')
    if not url or not ticker or {t['speaker'] for t in turns} != {'Бендер', 'Киберсенсей'}:
        raise ValueError('Нужны источник, строка и оба ведущих')
    return url, validate_script({'turns': turns, 'ticker': ticker})


async def admit(db, owner_id: int, url: str, script: dict) -> str:
    if type(owner_id) is not int or owner_id <= 0:
        raise ValueError('Неверный владелец')
    try:
        url = validate_url(url)
    except ArticleError:
        raise ValueError('Нужен HTTPS-источник') from None
    script = validate_script(script)
    if {turn['speaker'] for turn in script['turns']} != {'Бендер', 'Киберсенсей'} or not script['ticker']:
        raise ValueError('Нужны оба ведущих и строка')
    canonical = json.dumps({'owner_id': owner_id, 'url': url, 'script': script},
                           ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    digest = hashlib.sha256(canonical).hexdigest()
    candidate_id = 'owner-trial:' + digest
    await db.news_owner_trial_gate.update_one({'_id': 'slot'},
        {'$setOnInsert': {'candidate_id': candidate_id, 'created_at': datetime.now(timezone.utc)}}, upsert=True)
    gate = await db.news_owner_trial_gate.find_one({'_id': 'slot'})
    if not gate or gate.get('candidate_id') != candidate_id:
        raise ValueError('Слот уже занят другой пробой')
    doc = {'owner_id': owner_id, 'source_id': 'owner_trial', 'url': url,
           'title': 'Пробный сюжет по сценарию владельца', 'verification': 'needs_review', 'script': script}
    await db.news_owner_trial_candidates.update_one({'_id': candidate_id}, {'$setOnInsert': doc}, upsert=True)
    stored = await db.news_owner_trial_candidates.find_one({'_id': candidate_id})
    if not stored or any(stored.get(k) != v for k, v in doc.items()):
        raise ValueError('Пробная карточка не совпадает')
    created = await enqueue_script(db.news_owner_trial_jobs, candidate_id, script)
    return 'queued' if created else 'already_queued'


async def dispatch_once(queue, jobs, client) -> None:
    """Shared media queue idle hook; no trial submission while users are waiting."""
    if not enabled() or await queue.head():
        return
    pending = await jobs.find_one({'stage': {'$in': ['queued', 'submitting', 'running']}})
    if pending and await client.supports_news():
        from news_prototype.news_queue import run_idle_once
        await run_idle_once(queue, jobs, client)


async def status(db) -> str:
    gate = await db.news_owner_trial_gate.find_one({'_id': 'slot'})
    if not gate:
        return 'not_queued'
    job = await db.news_owner_trial_jobs.find_one({'candidate_id': gate.get('candidate_id')})
    return job.get('stage', 'unknown') if job else 'admission_incomplete'

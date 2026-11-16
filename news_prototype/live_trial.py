"""Explicit, phased single-news trial; no scheduler, channel or group posting.

Preparation uses a new local private folder. Submission is driven manually and
uses the *same Mongo queue lease and worker slot* as ordinary media jobs. The
trial's news records use separate per-trial collections. Nothing is enabled by
importing this module. All network operations require an explicit command.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import uuid

from news_prototype import article_fetch, editor_client, news_queue, story_editor
from news_prototype.news_worker_client import NewsWorkerClient, valid_id

DB_NAME = 'beleyakov_db'
QUEUE_NAME = 'bender_video_queue'


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json(path: Path, data: dict) -> None:
    temp = path.with_name(path.name + '.tmp')
    encoded = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(encoded)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
        _sync_dir(path.parent)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def load_prepared(folder: Path) -> dict:
    folder = Path(folder)
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError('Папка пробы недоступна')
    path = folder / 'prepared.json'
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 100_000:
        raise ValueError('Проба не подготовлена')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or set(data) != {'trial_id', 'candidate', 'draft'}:
        raise ValueError('Повреждена проба')
    valid_id(data['trial_id'])
    state_file = folder / 'state.json'
    if state_file.is_symlink() or not state_file.is_file() or state_file.stat().st_size > 4096:
        raise ValueError('Состояние подготовки недоступно')
    state = json.loads(state_file.read_text(encoding='utf-8'))
    if state != {'stage': 'prepared', 'trial_id': data['trial_id']}:
        raise ValueError('Подготовка не подтверждена')
    candidate, draft = data['candidate'], data['draft']
    if not isinstance(candidate, dict) or not isinstance(draft, dict):
        raise ValueError('Повреждена проба')
    story_editor.prompt_for(candidate)
    if draft.get('decision') != 'draft' or draft.get('verification') != 'needs_review':
        raise ValueError('Сценарий не готов')
    from news_prototype.story_audio import validate_script
    validate_script(draft.get('script'))
    return data


def _collections(db, trial_id: str):
    valid_id(trial_id)
    suffix = trial_id.replace('-', '')
    return db['news_trial_candidates_' + suffix], db['news_trial_jobs_' + suffix]


async def prepare(folder: Path, url: str, accent: str, key: str, model: str,
                  *, text: str = '', fetch=article_fetch.fetch_article, draft=editor_client.draft) -> str:
    if not isinstance(accent, str) or len(accent) > 1500 or not isinstance(text, str) or len(text) > 8000:
        raise ValueError('Слишком длинный акцент или текст')
    url = article_fetch.validate_url(url)
    if not key:
        raise ValueError('Не найден ключ редактора')
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,100}', model or ''):
        raise ValueError('Не задана модель редактора')
    folder = Path(folder)
    folder.mkdir(mode=0o700, parents=False, exist_ok=False)
    _sync_dir(folder.parent)
    trial_id = str(uuid.uuid4())
    state = folder / 'state.json'
    _write_json(state, {'stage': 'fetching', 'trial_id': trial_id})
    candidate = {'source_id': 'manual', 'url': url, 'text': text.strip(),
                 'accent': accent.strip(), 'verification': 'unverified'}
    try:
        try:
            story_editor.prompt_for(candidate)
        except ValueError:
            article = await fetch(url)
            candidate.update({k: article[k] for k in ('title', 'summary', 'resolved_url') if k in article})
            story_editor.prompt_for(candidate)
    except Exception:
        _write_json(state, {'stage': 'needs_source', 'trial_id': trial_id})
        return 'needs_source'
    # Commit the exact source before contacting the model; never silently retry.
    _write_json(folder / 'source.json', candidate)
    _write_json(state, {'stage': 'editing', 'trial_id': trial_id})
    try:
        result = await draft(candidate, key, model)
        if result.get('decision') == 'skip':
            _write_json(state, {'stage': 'skipped', 'trial_id': trial_id})
            return 'skipped'
        if result.get('decision') != 'draft' or result.get('verification') != 'needs_review':
            raise ValueError('Неверный сценарий')
        from news_prototype.story_audio import validate_script
        validate_script(result['script'])
        _write_json(folder / 'prepared.json', {'trial_id': trial_id, 'candidate': candidate, 'draft': result})
        _write_json(state, {'stage': 'prepared', 'trial_id': trial_id})
        return 'prepared'
    except Exception:
        _write_json(state, {'stage': 'editor_failed', 'trial_id': trial_id})
        return 'editor_failed'


async def enqueue(db, data: dict) -> str:
    candidates, jobs = _collections(db, data['trial_id'])
    candidate_id = 'trial:' + data['trial_id']
    document = dict(data['candidate'], draft=data['draft'], verification='needs_review')
    await candidates.update_one({'_id': candidate_id}, {'$setOnInsert': document}, upsert=True)
    existing = await candidates.find_one({'_id': candidate_id})
    if not existing or any(existing.get(k) != v for k, v in document.items()):
        raise ValueError('Сохранённая карточка не совпадает')
    created = await news_queue.enqueue_script(jobs, candidate_id, data['draft']['script'])
    return 'queued' if created else 'already_queued'


async def drive_once(db, data: dict, client) -> str:
    """At most one queue step; never submit directly or start a second worker."""
    from bender_queue import VideoQueue
    _, jobs = _collections(db, data['trial_id'])
    shared = db[QUEUE_NAME]
    # Do not create a new queue in the wrong DB: the operator must point to the
    # actual queue used by the bot. Merely supplying any Mongo URI is not enough.
    doc = await shared.find_one({'_id': 'bender-video'})
    if not doc or not isinstance(doc.get('items'), list) or 'lease_until' not in doc:
        raise ValueError('Общая очередь не найдена')
    if not await client.supports_news():
        return 'worker_not_ready'
    queue = VideoQueue(shared)
    if not await queue.acquire():
        return 'queue_in_use'
    heartbeat = asyncio.create_task(queue.heartbeat())
    try:
        await news_queue.run_idle_once(queue, jobs, client)
        job = await jobs.find_one({'candidate_id': 'trial:' + data['trial_id']})
        return job.get('stage', 'not_queued') if job else 'not_queued'
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        await queue.release()


async def deliver_once(db, data: dict, client, bot, admin_id: int) -> str:
    from news_prototype import review_delivery
    candidates, jobs = _collections(db, data['trial_id'])
    return await review_delivery.run_once(jobs, candidates, client, bot, admin_id)


def _editor_key() -> str:
    # Uses existing keys only; never accept secrets as CLI arguments.
    for name in ('GEMINI_KEYS', 'GEMINI_API_KEYS', 'GEMINI_API_KEY'):
        value = os.getenv(name, '').strip()
        if value:
            return value.split(',')[0].strip()
    from news_prototype.run_bender_trial import load_keys
    keys = load_keys()
    return keys[0] if keys else ''


def _database():
    if os.getenv('NEWS_TRIAL_SHARED_QUEUE_CONFIRMED', '') != '1':
        raise ValueError('Не подтверждена общая очередь рабочего бота')
    uri = os.getenv('MONGO_URI', '').strip()
    if not uri:
        raise ValueError('Не настроен доступ к общей очереди')
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=10000)
    return client, client[DB_NAME]


def _worker():
    return NewsWorkerClient(os.getenv('BENDER_WORKER_URL', ''),
                            os.getenv('BENDER_WORKER_KEY', '') or os.getenv('VIDEO_WORKER_KEY', ''))


async def _main(args) -> str:
    if args.command == 'prepare':
        from models import GEMINI_CHAT_MODEL
        text = ''
        if args.text_file:
            path = Path(args.text_file)
            if path.is_symlink() or path.stat().st_size > 32_000:
                raise ValueError('Текст недоступен или слишком большой')
            text = path.read_text(encoding='utf-8')
        return await prepare(args.folder, args.url, args.accent, _editor_key(), GEMINI_CHAT_MODEL, text=text)
    data = load_prepared(args.folder) if args.command != 'check' else None
    connection, db = _database()
    try:
        if args.command == 'check':
            shared = await db[QUEUE_NAME].find_one({'_id': 'bender-video'})
            if not shared or not isinstance(shared.get('items'), list) or 'lease_until' not in shared:
                return 'shared_queue_not_found'
            return 'ready' if await _worker().supports_news() else 'worker_not_ready'
        if args.command == 'enqueue':
            return await enqueue(db, data)
        if args.command == 'step':
            return await drive_once(db, data, _worker())
        if args.command == 'status':
            _, jobs = _collections(db, data['trial_id'])
            job = await jobs.find_one({'candidate_id': 'trial:' + data['trial_id']})
            return job.get('stage', 'not_queued') if job else 'not_queued'
        if args.command == 'deliver':
            from aiogram import Bot
            from config import ADMIN_ID, TELEGRAM_TOKEN
            from news_prototype.send_press_trial import load_token
            # No implicit fallback owner: a copied installation may have a
            # different configured administrator. Confirm explicitly in env.
            configured_owner = os.getenv('ADMIN_ID', '').strip()
            if not configured_owner.isascii() or not configured_owner.isdigit() or int(configured_owner) != ADMIN_ID or ADMIN_ID <= 0:
                raise ValueError('Владелец не подтверждён в настройках')
            token = TELEGRAM_TOKEN or load_token()
            if not token:
                raise ValueError('Не найден токен бота')
            bot = Bot(token=token)
            try:
                return await deliver_once(db, data, _worker(), bot, ADMIN_ID)
            finally:
                await bot.session.close()
        raise ValueError('Неизвестная команда')
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prepare_parser = sub.add_parser('prepare', help='Один запрос источника и редактора; без рендера')
    prepare_parser.add_argument('--folder', type=Path, required=True)
    prepare_parser.add_argument('--url', required=True, help='HTTPS-ссылка на новость или публичный пост')
    prepare_parser.add_argument('--accent', default='')
    prepare_parser.add_argument('--text-file', help='Локальный текст, если страница недоступна')
    sub.add_parser('check', help='Проверить очередь и объявленную поддержку воркера без задач')
    for command in ('enqueue', 'step', 'status', 'deliver'):
        item = sub.add_parser(command)
        item.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(_main(args))
    except Exception as exc:
        # Provider and DB exception messages may contain credentials or input.
        print('Операция не подтверждена; проверьте состояние перед повтором. Тип:', type(exc).__name__)
        raise SystemExit(1) from None
    print('Состояние:', result)


if __name__ == '__main__':
    main()

"""Opt-in owner-only private news intake and digest requests.

Handlers only save requests; optional background stages do the work. No channel
publication is performed here. Deployment is a separate decision.
"""
from __future__ import annotations

import logging
import os
import re
from aiogram import Router, types
from aiogram.filters import Command
from config import ADMIN_ID
from database import db
from news_prototype.manual_news import make_candidate

router = Router()
logger = logging.getLogger(__name__)
ACCENT_LINE = re.compile(r'(?im)^[ \t]*акцент[ \t]*:[ \t]*(.*)$')
SOURCE_LINE = re.compile(r'(?im)^[ \t]*источник[ \t]*:[ \t]*(https?://\S+)[ \t]*$')
URL_LINE = re.compile(r'^https?://\S+$', re.I)
HELP = ('Чтобы сохранить новость, напиши в личке:\n'
        '/news текст новости или открытая HTTPS-ссылка\nАкцент: на что обратить внимание\n\n'
        'Можно ответить командой /news на пересланное текстовое сообщение; '
        'после команды указать акцент. Ссылка на отдельный публичный пост Telegram '
        'тоже подходит. /news_list — последние сохранённые. '
        'Подготовка зависит от включённых этапов; публикации нет.')


def parse_input(command_text: str, reply_text: str | None) -> tuple[str, str, str]:
    match_command = re.match(r'^/\S+(?:\s+([\s\S]*))?$', command_text.strip())
    body = (match_command.group(1) or '').strip() if match_command else ''
    if reply_text is not None:
        news = reply_text.strip()
        accent = body
        if accent.lower().startswith('акцент:'):
            accent = accent.split(':', 1)[1].strip()
    else:
        match = ACCENT_LINE.search(body)
        accent = match.group(1).strip() if match else ''
        news = (body[:match.start()] + body[match.end():]).strip() if match else body
    source = ''
    source_match = SOURCE_LINE.search(news)
    if source_match:
        source = source_match.group(1)
        news = (news[:source_match.start()] + news[source_match.end():]).strip()
    elif URL_LINE.fullmatch(news):
        source, news = news, ''
    return news, accent, source


@router.message(Command('news'))
async def add_news(message: types.Message) -> None:
    user = message.from_user
    if not user or user.id != ADMIN_ID or message.chat.type != 'private':
        return
    reply = message.reply_to_message
    reply_text = (reply.text or reply.caption) if reply else None
    if reply is not None and not reply_text:
        await message.answer('В ответе нужен текст или подпись к сообщению. ' + HELP)
        return
    text, accent, url = parse_input(message.text or '', reply_text)
    if not text and not url:
        await message.answer(HELP)
        return
    try:
        candidate = make_candidate(user_id=user.id, admin_id=ADMIN_ID, chat_type=message.chat.type,
                                   message_id=message.message_id, text=text, accent=accent, url=url)
    except ValueError:
        await message.answer('Не получилось сохранить: проверь текст, акцент и ссылку. ' + HELP)
        return
    key = candidate.pop('id')
    try:
        result = await db.db.news_candidates.update_one({'_id': key}, {'$setOnInsert': candidate}, upsert=True)
    except Exception as exc:
        logger.warning('Не удалось подтвердить сохранение новости: %s', type(exc).__name__)
        await message.answer('Не удалось подтвердить сохранение. Проверь /news_list перед повтором.')
        return
    if result.upserted_id is None:
        await message.answer('Эта новость уже сохранена. Состояние можно проверить в /news_list.')
    else:
        await message.answer('Новость и акцент сохранены как непроверенный материал. '
                             'Дальнейшая подготовка зависит от включённых этапов. Публикации нет.')


@router.message(Command('news_list'))
async def list_news(message: types.Message) -> None:
    user = message.from_user
    if not user or user.id != ADMIN_ID or message.chat.type != 'private':
        return
    try:
        items = await db.db.news_candidates.find({'source_id': 'manual', 'owner_id': user.id}).sort(
            'collected_at', -1).limit(5).to_list(length=5)
    except Exception as exc:
        logger.warning('Не удалось получить список новостей: %s', type(exc).__name__)
        await message.answer('Не удалось загрузить список новостей.')
        return
    if not items:
        await message.answer('Сохранённых ручных новостей пока нет.')
        return
    lines = ['Последние ручные новости (непроверенные):']
    for item in items:
        text = ' '.join((item.get('text') or item.get('url') or '').split())[:180]
        accent = ' '.join((item.get('accent') or '').split())[:100]
        status = str(item.get('status') or 'candidate')
        label = {'candidate': 'ожидает обработки', 'editing': 'готовится сценарий',
                 'needs_source': 'не удалось получить достаточно текста: пришли текст новости отдельно',
                 'skipped': 'редактор пропустил новость', 'editor_failed': 'не удалось подготовить сценарий',
                 'draft_ready': 'сценарий подготовлен', 'render_queued': 'передана на подготовку видео'}.get(status, status)
        lines.append(f'• {text}\n  Состояние: {label}' + (f'\n  Акцент: {accent}' if accent else ''))
    await message.answer('\n\n'.join(lines), parse_mode=None, disable_web_page_preview=True)


@router.message(Command('news_ready'))
async def list_ready_stories(message: types.Message) -> None:
    user = message.from_user
    if not user or user.id != ADMIN_ID or message.chat.type != 'private':
        return
    from news_prototype.news_queue import READY_STORY_STAGES
    parts = (message.text or '').split()
    if len(parts) > 2 or (len(parts) == 2 and (not parts[1].isdigit() or len(parts[1]) > 6 or int(parts[1]) < 1)):
        await message.answer('Используй /news_ready или /news_ready 2 для следующей страницы.')
        return
    page = int(parts[1]) if len(parts) == 2 else 1
    try:
        items = await db.db.news_render_jobs.find({'stage': {'$in': list(READY_STORY_STAGES)},
                                                   'kind': {'$ne': 'digest'}}).sort(
            [('created_at', -1), ('_id', -1)]).skip((page - 1) * 10).limit(10).to_list(length=10)
        lines = [f'Готовые сюжеты, страница {page} (всё ещё требуют проверки):']
        for job in items:
            candidate = await db.db.news_candidates.find_one({'_id': job.get('candidate_id')})
            candidate = candidate or {}
            title = ' '.join(str(candidate.get('title') or candidate.get('text') or candidate.get('url') or 'Новость').split())[:100]
            lines.append(f'{title}\nКод: {job["request_id"][:8]}\nID: {job["request_id"]}')
    except Exception as exc:
        logger.warning('Не удалось получить готовые сюжеты: %s', type(exc).__name__)
        await message.answer('Не удалось получить готовые сюжеты.')
        return
    if not items:
        await message.answer('На этой странице готовых сюжетов нет.')
        return
    lines.append('Укажи коды в нужном порядке: /news_digest КОД1 КОД2 ...\nСледующая страница: /news_ready ' + str(page + 1))
    await message.answer('\n\n'.join(lines), parse_mode=None)


@router.message(Command('news_digest'))
async def create_digest(message: types.Message) -> None:
    user = message.from_user
    if not user or user.id != ADMIN_ID or message.chat.type != 'private':
        return
    if os.getenv('NEWS_RENDER_QUEUE_ENABLED', '').strip() != '1':
        await message.answer('Очередь подготовки новостей выключена; дайджест не поставлен в очередь.')
        return
    parts = (message.text or '').split()
    try:
        from news_prototype.news_queue import enqueue_digest, resolve_story_codes
        inputs = await resolve_story_codes(db.db.news_render_jobs, parts[1:])
        created = await enqueue_digest(db.db.news_render_jobs, user.id, message.message_id, inputs)
    except ValueError:
        await message.answer('Нужны как минимум два разных кода готовых сюжетов из /news_ready. '
                             'Если код неоднозначен, нужен полный ID.')
        return
    except Exception as exc:
        logger.warning('Не удалось подтвердить запрос дайджеста: %s', type(exc).__name__)
        await message.answer('Не удалось подтвердить запись. Не повторяй вслепую: проверь /news_digest_status.')
        return
    await message.answer(('Дайджест поставлен в очередь в указанном порядке. ' if created else 'Этот запрос уже сохранён. ')
                         + 'Он не будет опубликован. Доставка владельцу зависит от настроек.')


@router.message(Command('news_digest_status'))
async def list_digests(message: types.Message) -> None:
    user = message.from_user
    if not user or user.id != ADMIN_ID or message.chat.type != 'private':
        return
    try:
        items = await db.db.news_render_jobs.find({'kind': 'digest'}).sort(
            'created_at', -1).limit(10).to_list(length=10)
    except Exception as exc:
        logger.warning('Не удалось получить состояние дайджестов: %s', type(exc).__name__)
        await message.answer('Не удалось получить состояние дайджестов.')
        return
    if not items:
        await message.answer('Запросов на дайджест пока нет.')
        return
    await message.answer('Последние запросы дайджеста:\n\n' + '\n\n'.join(
        f"{item['request_id']}\nСостояние: {item.get('stage', 'unknown')}"
        for item in items), parse_mode=None)

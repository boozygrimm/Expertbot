"""Owner-only, opt-in draft/review workflow with explicit render approval.

No polling, publication, audio, rendering or retries happen here. Durable states
are written before an external fetch/editor call or a render admission. A lost
response is never interpreted as permission to repeat an external operation.
"""
from __future__ import annotations

import asyncio

from datetime import datetime, timezone
import hashlib
import json
import re

from news_prototype import article_fetch, owner_trial, story_editor
from news_prototype.story_audio import validate_script

MAX_MESSAGE = 3900  # Includes the editable command; below Telegram's limit.
REVISION = re.compile(r'^[0-9a-f]{64}$')


def _url(value: str) -> str:
    try:
        result = article_fetch.validate_url(value)
        from news_prototype.telegram_post import widget_for
        widget_for(result)
        return result
    except article_fetch.ArticleError:
        raise ValueError('Нужна ссылка на доступный HTTPS-источник') from None


def _key(owner_id: int, draft_id: int) -> str:
    # MongoDB decodes integer values beyond int32 as bson.int64.Int64, an int
    # subclass. Batch draft IDs intentionally use that range. Accept integer
    # subclasses while still excluding bool and non-integral values.
    if (not isinstance(owner_id, int) or isinstance(owner_id, bool) or owner_id <= 0
            or not isinstance(draft_id, int) or isinstance(draft_id, bool) or draft_id <= 0):
        raise ValueError('Неверный идентификатор')
    return f'owner-draft:{owner_id}:{draft_id}'


def revision(url: str, script: dict) -> str:
    payload = json.dumps({'url': url, 'script': script}, ensure_ascii=False,
                         sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def prepare_input(value: str) -> tuple[str, str]:
    """Parse /news_prepare URL followed by an optional single accent line."""
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError('Неверный запрос')
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if not lines:
        raise ValueError('Нужна ссылка')
    parts = lines[0].split(maxsplit=1)
    if len(parts) != 2 or not re.fullmatch(r'/news_prepare(?:@[A-Za-z0-9_]+)?', parts[0], re.I):
        raise ValueError('Нужна ссылка после команды')
    # Explicitly validate Telegram paths before the network request.
    url = _url(parts[1])
    accent = ''
    if len(lines) > 2:
        raise ValueError('Укажи не более одного акцента')
    if len(lines) == 2:
        label, sep, accent = lines[1].partition(':')
        if not sep or label.strip().casefold() != 'акцент':
            raise ValueError('Нужна строка Акцент:')
        accent = accent.strip()
    if len(accent) > 1500:
        raise ValueError('Акцент слишком длинный')
    return url, accent


def prepare_many_input(value: str) -> tuple[list[str], str]:
    """Parse two to four source URLs and an optional shared accent."""
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError('Неверный запрос')
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if not lines or not re.fullmatch(r'/news_prepare_many(?:@[A-Za-z0-9_]+)?', lines[0], re.I):
        raise ValueError('Нужна команда и ссылки')
    accent = ''
    if len(lines) > 1 and lines[-1].casefold().startswith('акцент:'):
        accent = lines.pop().partition(':')[2].strip()
    if len(accent) > 1500 or not 3 <= len(lines) <= 5:
        raise ValueError('Нужно от двух до четырёх ссылок')
    urls = [_url(line) for line in lines[1:]]
    if len(set(urls)) != len(urls):
        raise ValueError('Ссылки не должны повторяться')
    return urls, accent


def batch_draft_ids(message_id: int, count: int) -> list[int]:
    # Telegram message IDs are signed 32-bit. Reserve a disjoint numeric range
    # for children so these can still be addressed by existing /news_edit.
    if type(message_id) is not int or not 0 < message_id < 2**31 or not 2 <= count <= 4:
        raise ValueError('Неверный номер сообщения')
    return [100_000_000_000 + message_id * 10 + i for i in range(1, count + 1)]


def editable_message(draft_id: int, url: str, script: dict) -> str:
    script = validate_script(script)
    if not script['ticker'] or {turn['speaker'] for turn in script['turns']} != {'Бендер', 'Киберсенсей'}:
        raise ValueError('Нужны оба ведущих и строка')
    rev = revision(url, script)
    lines = [f'/news_edit {draft_id} {rev}', f'Источник: {url}', f'Строка: {script["ticker"]}']
    for turn in script['turns']:
        if len(turn['text'].splitlines()) != 1:
            raise ValueError('Реплика должна быть одной строкой')
        lines.append(f'{turn["speaker"]}: {turn["text"]}')
    if len(script['ticker'].splitlines()) != 1:
        raise ValueError('Бегущая строка должна быть одной строкой')
    message = '\n'.join(lines)
    if len(message) > MAX_MESSAGE or len(message.encode('utf-16-le')) // 2 > MAX_MESSAGE:
        raise ValueError('Сценарий слишком длинный для одного сообщения')
    return message


def parse_edit(text: str) -> tuple[int, str, str, dict]:
    if not isinstance(text, str) or len(text) > 4096:
        raise ValueError('Неверное сообщение')
    lines = text.splitlines()
    if not lines:
        raise ValueError('Нет текста')
    parts = lines[0].split()
    if len(parts) != 3 or not re.fullmatch(r'/news_edit(?:@[A-Za-z0-9_]+)?', parts[0], re.I):
        raise ValueError('Нужна команда редактирования')
    if not parts[1].isdigit() or len(parts[1]) > 15 or int(parts[1]) <= 0 or not REVISION.fullmatch(parts[2]):
        raise ValueError('Неверный идентификатор или версия')
    draft_id = int(parts[1])
    url, script = owner_trial.parse_message('/news_trial\n' + '\n'.join(lines[1:]))
    editable_message(draft_id, url, script)
    return draft_id, parts[2], url, script


async def create(db, owner_id: int, draft_id: int, url: str, accent: str) -> bool:
    key = _key(owner_id, draft_id)
    url = _url(url)
    if not isinstance(accent, str) or len(accent) > 1500:
        raise ValueError('Неверный акцент')
    doc = {'owner_id': owner_id, 'draft_id': draft_id, 'url': url, 'accent': accent.strip(),
           'stage': 'new', 'verification': 'unverified', 'created_at': datetime.now(timezone.utc)}
    result = await db.news_owner_drafts.update_one({'_id': key}, {'$setOnInsert': doc}, upsert=True)
    if result.upserted_id is not None:
        return True
    existing = await db.news_owner_drafts.find_one({'_id': key})
    if not existing or any(existing.get(k) != doc[k] for k in ('owner_id', 'draft_id', 'url', 'accent')):
        raise ValueError('Запрос уже использован для другой новости')
    return False


async def get(db, owner_id: int, draft_id: int) -> dict | None:
    return await db.news_owner_drafts.find_one({'_id': _key(owner_id, draft_id), 'owner_id': owner_id})


async def _finish_editing(db, item: dict, draft, image_b64: str | None = None,
                          image_regions: list | None = None) -> str:
    key = item['_id']
    try:
        result = await draft(item)
        if not isinstance(result, dict) or result.get('decision') not in ('draft', 'skip'):
            raise ValueError('Неверный ответ редактора')
        if result['decision'] == 'skip':
            await db.news_owner_drafts.update_one({'_id': key, 'stage': 'editing'}, {'$set': {'stage': 'skipped'}})
            return 'skipped'
        if result.get('verification') != 'needs_review':
            raise ValueError('Редактор не отметил необходимость проверки')
        script = validate_script(result.get('script'))
        # The model never controls media; it comes only from the verified post.
        script.pop('image_b64', None)
        script.pop('image_regions', None)
        if image_b64:
            script['image_b64'] = image_b64
            if image_regions:
                script['image_regions'] = image_regions
        script = validate_script(script)
        editable_message(item['draft_id'], item['url'], script)
        rev = revision(item['url'], script)
        saved = await db.news_owner_drafts.update_one({'_id': key, 'stage': 'editing'},
            {'$set': {'stage': 'draft_ready', 'script': script, 'revision': rev,
                      'evidence': result.get('evidence', []), 'verification': 'needs_review'}})
        return 'draft_ready' if saved.modified_count else 'changed'
    except Exception:
        # Do not include provider errors: they can contain keys or input.
        await db.news_owner_drafts.update_one({'_id': key, 'stage': 'editing'}, {'$set': {'stage': 'editor_failed'}})
        return 'editor_failed'


async def process_new(db, owner_id: int, draft_id: int, fetch, draft, fetch_image=None,
                      analyze_image=None) -> str:
    key = _key(owner_id, draft_id)
    claimed = await db.news_owner_drafts.update_one({'_id': key, 'owner_id': owner_id, 'stage': 'new'},
                                                     {'$set': {'stage': 'fetching'}})
    if not claimed.modified_count:
        return 'already_started'
    item = await get(db, owner_id, draft_id)
    try:
        article = await fetch(item['url'])
        if not isinstance(article, dict) or not isinstance(article.get('summary'), str):
            raise ValueError('Не найден текст')
        additions = {k: article[k] for k in ('title', 'summary', 'resolved_url') if k in article}
        item = item | additions
    except Exception:
        await db.news_owner_drafts.update_one({'_id': key, 'stage': 'fetching'}, {'$set': {'stage': 'needs_source'}})
        return 'needs_source'
    image_b64 = None
    media = article.get('media')
    if fetch_image and isinstance(media, dict) and media.get('kind') in ('photo', 'video_thumbnail'):
        try:
            # Revalidate even when the fetch callback is injected.
            from news_prototype.telegram_post import media_url
            image_b64 = await fetch_image(media_url(media.get('url')))
            from news_prototype.news_image_codec import decode_thumbnail
            decode_thumbnail(image_b64)
            additions['image_kind'] = media['kind']
        except Exception:
            image_b64 = None  # Missing or unsafe media must never prevent the story.
    regions = None
    if image_b64:
        additions['image_analysis'] = 'unavailable'
        if analyze_image:
            try:
                from news_prototype.image_vision import TIMEOUT_SECONDS
                from news_prototype.image_focus import validate_regions
                analysis = await asyncio.wait_for(analyze_image(image_b64), timeout=TIMEOUT_SECONDS)
                description = analysis.get('description') if isinstance(analysis, dict) else None
                if not isinstance(description, str) or not description.strip() or len(description) > 1800:
                    raise ValueError('Неверное описание')
                description = description.strip()
                additions['image_description'] = description
                additions['image_analysis'] = 'analyzed'
                item = item | {'image_description': description}
                try:
                    if analysis.get('regions'):
                        regions = validate_regions(analysis['regions'])
                except ValueError:
                    regions = None
            except Exception:
                # A timeout or bad vision response must not block text-only editing.
                pass
    try:
        story_editor.prompt_for(item)
    except ValueError:
        # /news_source currently starts a text-only draft; do not retain an
        # image-derived description without the corresponding review image.
        for field in ('image_description', 'image_analysis', 'image_kind'):
            additions.pop(field, None)
        await db.news_owner_drafts.update_one({'_id': key, 'stage': 'fetching'},
                                               {'$set': dict(additions, stage='needs_source')})
        return 'needs_source'
    claimed = await db.news_owner_drafts.update_one({'_id': key, 'stage': 'fetching'},
                                                     {'$set': dict(additions, stage='editing')})
    if not claimed.modified_count:
        return 'changed'
    return await _finish_editing(db, item, draft, image_b64, regions)


async def provide_source(db, owner_id: int, draft_id: int, text: str, draft) -> str:
    if not isinstance(text, str) or not text.strip() or len(text) > 8000:
        raise ValueError('Нужен текст источника до 8000 символов')
    key = _key(owner_id, draft_id)
    item = await get(db, owner_id, draft_id)
    if not item or item.get('stage') != 'needs_source':
        return 'not_waiting'
    item = item | {'text': text.strip()}
    story_editor.prompt_for(item)
    changed = await db.news_owner_drafts.update_one({'_id': key, 'owner_id': owner_id, 'stage': 'needs_source'},
                                                     {'$set': {'text': text.strip(), 'stage': 'editing'}})
    if not changed.modified_count:
        return 'changed'
    return await _finish_editing(db, item, draft)


async def edit(db, owner_id: int, draft_id: int, expected: str, url: str, script: dict) -> str:
    if not REVISION.fullmatch(expected or ''):
        raise ValueError('Неверная версия')
    url = _url(url)
    script = validate_script(script)
    editable_message(draft_id, url, script)
    current = await get(db, owner_id, draft_id)
    if not current or current.get('url') != url:
        raise ValueError('Источник нельзя менять в готовом черновике')
    script.pop('image_b64', None)
    script.pop('image_regions', None)
    if 'image_b64' in current.get('script', {}):
        script['image_b64'] = current['script']['image_b64']
        if 'image_regions' in current['script']:
            script['image_regions'] = current['script']['image_regions']
    new_rev = revision(url, script)
    if new_rev == expected and current.get('stage') == 'draft_ready' and current.get('revision') == expected:
        return 'unchanged'
    changed = await db.news_owner_drafts.update_one(
        {'_id': _key(owner_id, draft_id), 'owner_id': owner_id, 'stage': 'draft_ready', 'revision': expected},
        {'$set': {'script': script, 'revision': new_rev, 'verification': 'needs_review', 'edited_by_owner': True}})
    return 'saved' if changed.modified_count else 'stale'


async def remove_focus(db, owner_id: int, draft_id: int, expected: str) -> str:
    if not REVISION.fullmatch(expected or ''):
        raise ValueError('Неверная версия')
    item = await get(db, owner_id, draft_id)
    if not item or item.get('stage') != 'draft_ready' or item.get('revision') != expected:
        return 'stale'
    script = dict(item['script'])
    if 'image_regions' not in script:
        return 'unchanged'
    script.pop('image_regions')
    changed = await db.news_owner_drafts.update_one(
        {'_id': _key(owner_id, draft_id), 'owner_id': owner_id, 'stage': 'draft_ready', 'revision': expected},
        {'$set': {'script': script, 'revision': revision(item['url'], script), 'verification': 'needs_review'}})
    return 'saved' if changed.modified_count else 'stale'


async def remove_image(db, owner_id: int, draft_id: int, expected: str) -> str:
    if not REVISION.fullmatch(expected or ''):
        raise ValueError('Неверная версия')
    item = await get(db, owner_id, draft_id)
    if not item or item.get('stage') != 'draft_ready' or item.get('revision') != expected:
        return 'stale'
    script = dict(item['script'])
    if 'image_b64' not in script:
        return 'unchanged'
    script.pop('image_b64')
    script.pop('image_regions', None)
    changed = await db.news_owner_drafts.update_one(
        {'_id': _key(owner_id, draft_id), 'owner_id': owner_id, 'stage': 'draft_ready', 'revision': expected},
        {'$set': {'script': script, 'revision': revision(item['url'], script), 'verification': 'needs_review'},
         '$unset': {'image_kind': ''}})
    return 'saved' if changed.modified_count else 'stale'


async def approve(db, owner_id: int, draft_id: int, expected: str) -> str:
    if not REVISION.fullmatch(expected or ''):
        raise ValueError('Неверная версия')
    key = _key(owner_id, draft_id)
    item = await get(db, owner_id, draft_id)
    if not item or item.get('revision') != expected:
        return 'stale'
    if item.get('stage') in ('queued', 'already_queued'):
        return 'already_queued'
    if item.get('stage') != 'draft_ready':
        return 'not_ready'
    # Previous delivered trials are reopened only after an explicit approval of
    # this new revision; uncertain/in-flight trials are never unlocked here.
    stage, finished_id = await owner_trial.status_info(db)
    if stage not in ('not_queued', 'review_sent') or (stage == 'review_sent' and not finished_id):
        return 'trial_busy'
    claimed = await db.news_owner_drafts.update_one({'_id': key, 'owner_id': owner_id,
        'stage': 'draft_ready', 'revision': expected}, {'$set': {'stage': 'approving'}})
    if not claimed.modified_count:
        return 'changed'
    try:
        if finished_id:
            opened = await owner_trial.open_next(db, owner_id, finished_id)
            if opened not in ('opened', 'already_open'):
                await db.news_owner_drafts.update_one({'_id': key, 'stage': 'approving'},
                                                       {'$set': {'stage': 'approval_uncertain'}})
                return 'approval_uncertain'
        result = await owner_trial.admit(db, owner_id, item['url'], item['script'])
        final = 'queued' if result == 'queued' else 'already_queued'
        await db.news_owner_drafts.update_one({'_id': key, 'stage': 'approving'}, {'$set': {'stage': final}})
        return final
    except Exception:
        # Could have committed the render admission despite a lost response.
        # Never automatically replay; inspect the existing trial status.
        await db.news_owner_drafts.update_one({'_id': key, 'stage': 'approving'},
                                               {'$set': {'stage': 'approval_uncertain'}})
        return 'approval_uncertain'

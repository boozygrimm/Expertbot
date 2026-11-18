"""Collect selected sources for a single bulletin, without per-story editors.

Claim the batch once before fetching. Missing text blocks the model call and can
be supplied explicitly; images are optional, validated and never substituted.
All snapshots remain in the separate bulletin collection, not in the old drafts.
"""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from news_prototype import article_fetch, bulletin_editor, owner_bulletin
from news_prototype.news_image_codec import decode_thumbnail
from news_prototype.telegram_post import media_url, widget_for


async def create(db, owner_id: int, number: int, choices: list[dict]) -> bool:
    key = owner_bulletin._key(owner_id, number)
    if not isinstance(choices, list) or not 2 <= len(choices) <= 20:
        raise ValueError('Нужно выбрать от двух до двадцати новостей')
    clean = []
    for index, choice in enumerate(choices, 1):
        if not isinstance(choice, dict) or set(choice) != {'url', 'title'}:
            raise ValueError('Неверная новость')
        try:
            url = article_fetch.validate_url(choice['url'])
            widget_for(url)  # reject unsupported/private Telegram paths
        except article_fetch.ArticleError:
            raise ValueError('Недопустимый адрес источника') from None
        title = choice['title']
        if not isinstance(title, str) or not title.strip() or len(title) > 300:
            raise ValueError('Неверный заголовок')
        clean.append({'draft_id': index, 'url': url, 'title': title.strip()})
    if len({x['url'] for x in clean}) != len(clean):
        raise ValueError('Ссылки не должны повторяться')
    result = await db.news_owner_bulletins.update_one({'_id': key}, {'$setOnInsert': {
        'owner_id': owner_id, 'bulletin_id': int(number), 'mode': 'direct', 'choices': clean,
        'stage': 'collect_new', 'created_at': datetime.now(timezone.utc)}}, upsert=True)
    if result.upserted_id is not None:
        return True
    item = await owner_bulletin.get(db, owner_id, number)
    if not item or item.get('mode') != 'direct' or item.get('choices') != clean:
        raise ValueError('Номер уже используется')
    return False


def _source_text(value):
    if not isinstance(value, str) or not 80 <= len(value.strip()) <= 8000:
        raise ValueError('Нужен полный текст источника от 80 до 8000 символов')
    return value.strip()


def parse_urls(text: str) -> list[str]:
    """Accept individual public HTTPS article/post links, one per line."""
    if not isinstance(text, str) or len(text) > 20000:
        raise ValueError('Нужны ссылки')
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not 1 <= len(lines) <= 20:
        raise ValueError('Нужны от одной до двадцати ссылок')
    try:
        urls = [article_fetch.validate_url(line) for line in lines]
        for url in urls:
            widget_for(url)
    except article_fetch.ArticleError:
        raise ValueError('Нужны публичные HTTPS-ссылки на статьи или посты') from None
    if len(set(urls)) != len(urls):
        raise ValueError('Ссылки повторяются')
    return urls


def _stage(stories):
    if any(not story.get('summary') for story in stories):
        return 'source_missing'
    return 'new' if len(stories) >= bulletin_editor.MIN_STORIES else 'source_too_few'


async def _collect_choices(choices, fetch, fetch_image):
    semaphore = asyncio.Semaphore(4)
    async def collect_one(choice):
        story = dict(choice, summary='')
        async with semaphore:
            try:
                article = await fetch(choice['url'])
                summary = _source_text(article.get('summary'))
                title = article.get('title')
                if isinstance(title, str) and title.strip() and len(title) <= 300:
                    story['title'] = title.strip()
                story['summary'] = summary
                from news_prototype.telegram_post import safe_channel_name
                name = safe_channel_name(article.get('telegram_channel_name'))
                if name and widget_for(choice['url']) is not None:
                    story['telegram_channel_name'] = name
                video_status = article.get('telegram_video_status')
                if (isinstance(video_status, str) and video_status in ('available', 'none', 'ambiguous', 'too_big')
                        and widget_for(choice['url']) is not None):
                    # A discovery hint only: not a downloaded/approved video.
                    # This field participates in source_revision and never
                    # contains signed CDN URLs or video bytes.
                    story['telegram_video_status'] = video_status
            except Exception:
                return story
            media = article.get('media')
            if isinstance(media, dict) and media.get('kind') in ('photo', 'video_thumbnail', 'article_image'):
                try:
                    if media['kind'] == 'article_image':
                        image = await fetch_image(article_fetch.validate_url(media.get('url')), article=True)
                    else:
                        image = await fetch_image(media_url(media.get('url')))
                    decode_thumbnail(image)
                    story['image_b64'] = image
                    story['image_kind'] = media['kind']
                except Exception:
                    pass  # The renderer uses a joke card, never unrelated media.
        return story

    return await asyncio.gather(*(collect_one(choice) for choice in choices))


async def collect(db, owner_id: int, number: int, fetch, fetch_image) -> str:
    key = owner_bulletin._key(owner_id, number)
    item = await owner_bulletin.get(db, owner_id, number)
    if not item or item.get('mode') != 'direct' or item.get('stage') != 'collect_new':
        return 'not_new'
    claim = await db.news_owner_bulletins.update_one({'_id': key, 'owner_id': owner_id, 'stage': 'collect_new'},
        {'$set': {'stage': 'fetching_sources'}})
    if not claim.modified_count:
        return 'not_new'
    stories = await _collect_choices(item['choices'], fetch, fetch_image)
    missing = [s['draft_id'] for s in stories if not s['summary']]
    if not missing:
        bulletin_editor.material_for(stories)
    result = await db.news_owner_bulletins.update_one({'_id': key, 'owner_id': owner_id, 'stage': 'fetching_sources'},
        {'$set': {'stage': 'source_missing' if missing else 'new', 'stories': stories,
                  'source_revision': owner_bulletin.revision(stories, []), 'missing_ids': missing}})
    return ('source_missing' if missing else 'new') if result.modified_count else 'changed'


async def provide_source(db, owner_id: int, number: int, section_id: int, expected: str, text: str) -> str:
    text = _source_text(text)
    item = await owner_bulletin.get(db, owner_id, number)
    if (not item or item.get('mode') != 'direct' or item.get('stage') != 'source_missing'
            or item.get('source_revision') != expected
            or owner_bulletin.revision(item['stories'], []) != expected):
        return 'stale'
    stories = copy.deepcopy(item['stories'])
    story = next((s for s in stories if s['draft_id'] == section_id), None)
    if not story or section_id not in item['missing_ids'] or story.get('summary'):
        return 'stale'
    story['summary'] = text
    missing = [s['draft_id'] for s in stories if not s['summary']]
    stage = _stage(stories)
    if stage == 'new':
        bulletin_editor.material_for(stories)
    result = await db.news_owner_bulletins.update_one(
        {'_id': owner_bulletin._key(owner_id, number), 'owner_id': owner_id,
         'stage': 'source_missing', 'source_revision': expected},
        {'$set': {'stories': stories, 'source_revision': owner_bulletin.revision(stories, []),
                  'stage': stage, 'missing_ids': missing}})
    return stage if result.modified_count else 'stale'


async def remove_missing(db, owner_id: int, number: int, section_id: int, expected: str) -> str:
    """Remove only a missing story from the exact owner-reviewed source snapshot."""
    item = await owner_bulletin.get(db, owner_id, number)
    if (not item or item.get('mode') != 'direct' or item.get('stage') != 'source_missing'
            or item.get('source_revision') != expected
            or owner_bulletin.revision(item['stories'], []) != expected
            or section_id not in item.get('missing_ids', [])):
        return 'stale'
    if (len(item['stories']) != len(item['choices']) or any(
            s['draft_id'] != c['draft_id'] or s['url'] != c['url']
            for s, c in zip(item['stories'], item['choices']))):
        return 'stale'
    stories = [s for s in item['stories'] if s['draft_id'] != section_id]
    choices = [c for c in item['choices'] if c['draft_id'] != section_id]
    if len(stories) < 1 or len(stories) != len(choices) or len(stories) != len(item['stories']) - 1:
        return 'too_few'
    missing = [s['draft_id'] for s in stories if not s.get('summary')]
    stage = _stage(stories)
    if stage == 'new':
        bulletin_editor.material_for(stories)
    result = await db.news_owner_bulletins.update_one(
        {'_id': owner_bulletin._key(owner_id, number), 'owner_id': owner_id,
         'stage': 'source_missing', 'source_revision': expected},
        {'$set': {'choices': choices, 'stories': stories, 'source_revision': owner_bulletin.revision(stories, []),
                  'stage': stage, 'missing_ids': missing,
                  'next_source_id': max(item.get('next_source_id', 1), max(s['draft_id'] for s in item['stories']) + 1)}})
    return stage if result.modified_count else 'stale'


async def append_sources(db, owner_id: int, number: int, expected: str, urls: list[str], fetch, fetch_image) -> str:
    """Read only newly added links; preserve existing snapshots and require CAS."""
    urls = parse_urls('\n'.join(urls))
    item = await owner_bulletin.get(db, owner_id, number)
    stages = ('new', 'source_missing', 'source_too_few')
    if (not item or item.get('mode') != 'direct' or item.get('stage') not in stages
            or item.get('source_revision') != expected
            or owner_bulletin.revision(item['stories'], []) != expected):
        return 'stale'
    stories, choices = item['stories'], item['choices']
    if (len(stories) != len(choices) or any(s['draft_id'] != c['draft_id'] or s['url'] != c['url']
                                              for s, c in zip(stories, choices))):
        return 'stale'
    if len(stories) + len(urls) > bulletin_editor.MAX_STORIES:
        raise ValueError('Не более двадцати новостей')
    if set(urls) & {choice['url'] for choice in choices}:
        raise ValueError('Ссылка уже добавлена')
    first = max(item.get('next_source_id', 1), max((s['draft_id'] for s in stories), default=0) + 1)
    additions = [{'draft_id': first + i, 'url': url, 'title': url[:300]} for i, url in enumerate(urls)]
    claim = await db.news_owner_bulletins.update_one(
        {'_id': owner_bulletin._key(owner_id, number), 'owner_id': owner_id,
         'stage': item['stage'], 'source_revision': expected},
        {'$set': {'stage': 'fetching_sources'}})
    if not claim.modified_count:
        return 'stale'
    found = await _collect_choices(additions, fetch, fetch_image)
    stories = stories + found
    choices = choices + additions
    missing = [s['draft_id'] for s in stories if not s.get('summary')]
    stage = _stage(stories)
    if stage == 'new':
        bulletin_editor.material_for(stories)
    result = await db.news_owner_bulletins.update_one(
        {'_id': owner_bulletin._key(owner_id, number), 'owner_id': owner_id,
         'stage': 'fetching_sources', 'source_revision': expected},
        {'$set': {'choices': choices, 'stories': stories, 'source_revision': owner_bulletin.revision(stories, []),
                  'stage': stage, 'missing_ids': missing, 'next_source_id': first + len(additions)}})
    return stage if result.modified_count else 'stale'

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


async def collect(db, owner_id: int, number: int, fetch, fetch_image) -> str:
    key = owner_bulletin._key(owner_id, number)
    item = await owner_bulletin.get(db, owner_id, number)
    if not item or item.get('mode') != 'direct' or item.get('stage') != 'collect_new':
        return 'not_new'
    claim = await db.news_owner_bulletins.update_one({'_id': key, 'owner_id': owner_id, 'stage': 'collect_new'},
        {'$set': {'stage': 'fetching_sources'}})
    if not claim.modified_count:
        return 'not_new'
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
                video_status = article.get('telegram_video_status')
                if (isinstance(video_status, str) and video_status in ('available', 'none', 'ambiguous')
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
                    pass  # neutral screen rather than unrelated or unverified media
        return story

    stories = await asyncio.gather(*(collect_one(choice) for choice in item['choices']))
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
    if not missing:
        bulletin_editor.material_for(stories)
    result = await db.news_owner_bulletins.update_one(
        {'_id': owner_bulletin._key(owner_id, number), 'owner_id': owner_id,
         'stage': 'source_missing', 'source_revision': expected},
        {'$set': {'stories': stories, 'source_revision': owner_bulletin.revision(stories, []),
                  'stage': 'source_missing' if missing else 'new', 'missing_ids': missing}})
    return ('source_missing' if missing else 'new') if result.modified_count else 'stale'

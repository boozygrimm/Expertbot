"""Owner-only daily news collection and one private briefing at 03:00 Moscow.

The persistent per-day sending marker avoids automatically repeating an
ambiguous Telegram send. No script generation, video rendering or publication.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import os

from news_prototype import owner_discovery, owner_trial

MSK = timezone(timedelta(hours=3))
KEEP_DAYS = 7
BATCH_BASE = 5_000_000_000_000


async def enabled(db) -> bool:
    """Telegram setting takes precedence; retain the old flag as a default."""
    settings = await db.news_owner_daily_state.find_one({'_id': 'settings'})
    if settings and isinstance(settings.get('enabled'), bool):
        return settings['enabled']
    # Preserve the old opt-in rules until the owner explicitly changes the setting.
    return (os.getenv('NEWS_OWNER_DAILY_ENABLED', '').strip() == '1'
            and os.getenv('NEWS_OWNER_DRAFT_ENABLED', '').strip() == '1'
            and owner_trial.enabled())


async def set_enabled(db, value: bool) -> None:
    if type(value) is not bool:
        raise ValueError('Неверное состояние сбора')
    await db.news_owner_daily_state.update_one({'_id': 'settings'},
                                               {'$set': {'enabled': value}}, upsert=True)


def now_utc(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Нужно время с часовым поясом')
    return now.astimezone(timezone.utc)


def date_key(now: datetime) -> str:
    return now_utc(now).astimezone(MSK).date().isoformat()


def batch_id(day: str) -> int:
    return BATCH_BASE + int(day.replace('-', ''))


def normalize_items(items: list[dict]) -> list[dict]:
    result, seen = [], set()
    for item in items:
        try:
            url = owner_discovery.canonical_url(item['url'])
            published = owner_discovery.news_sources.parse_date(item['published_at'])
            source, title = item['source'], item['title']
            if (not published or not isinstance(title, str) or not title.strip()
                    or source not in {name for name, _ in owner_discovery.SOURCES.values()}
                    or len(url) > 500 or url in seen):
                continue
            result.append({'url': url, 'source': source, 'title': title[:180],
                           'published_at': published.isoformat()})
            seen.add(url)
        except (ValueError, KeyError, TypeError, owner_discovery.article_fetch.ArticleError):
            continue
    return result


async def poll(db, *, now: datetime | None = None, fetch=None) -> tuple[int, list[str]]:
    now = now_utc(now)
    fetch = fetch or owner_discovery.collect
    items, failed = await fetch(now=now, per_source_limit=100, max_results=500)
    inserted = 0
    for item in normalize_items(items):
        key = hashlib.sha256(item['url'].encode('utf-8')).hexdigest()
        result = await db.news_owner_daily_items.update_one({'_id': key},
            {'$setOnInsert': {**item, 'first_seen_at': now}}, upsert=True)
        inserted += int(result.upserted_id is not None)
    await db.news_owner_daily_items.delete_many({'published_at': {'$lt': (now - timedelta(days=KEEP_DAYS)).isoformat()}})
    await db.news_owner_daily_state.update_one({'_id': 'last_poll'},
        {'$set': {'at': now, 'failed': failed, 'inserted': inserted}}, upsert=True)
    return inserted, failed


async def initialize(db, owner_id: int, now: datetime) -> str:
    local = now_utc(now).astimezone(MSK)
    # Enabling after 03:10 should not immediately surprise the owner with a
    # daytime briefing; subsequent restarts can catch up on the same day.
    start = local.date() if (local.hour < 3 or (local.hour == 3 and local.minute <= 10)) else local.date() + timedelta(days=1)
    key = f'owner:{owner_id}'
    await db.news_owner_daily_state.update_one({'_id': key},
        {'$setOnInsert': {'first_day': start.isoformat()}}, upsert=True)
    state = await db.news_owner_daily_state.find_one({'_id': key})
    return state['first_day']


def select_fair(items: list[dict]) -> list[dict]:
    """Take one per source in rounds, so a busy feed does not hide others."""
    buckets = {name: [] for name, _ in owner_discovery.SOURCES.values()}
    for item in items:
        if item.get('source') in buckets:
            buckets[item['source']].append(item)
    for bucket in buckets.values():
        bucket.sort(key=lambda item: item['published_at'], reverse=True)
    output = []
    while any(buckets.values()):
        for bucket in buckets.values():
            if bucket:
                output.append(bucket.pop(0))
    return output


async def recent_items(db, end: datetime) -> list[dict]:
    end = now_utc(end)
    start_time = (end - timedelta(hours=24)).isoformat()
    items = await db.news_owner_daily_items.find({'published_at': {'$gte': start_time, '$lte': end.isoformat()}},
        {'_id': 0, 'first_seen_at': 0}).sort('published_at', -1).limit(250).to_list(length=250)
    return select_fair(items)


def format_briefing(day: str, owner_batch: int, items: list[dict], failed: list[str], *, daily: bool = True) -> tuple[str, list[dict]]:
    label = f'{day} · 03:00 МСК' if daily else 'сейчас'
    prefix = f'Собранные новости за последние сутки · {label}\nЗаголовки могут быть переведены автоматически. Проверь источник.\n'
    if failed:
        prefix += 'Недоступны при последней проверке: ' + ', '.join(failed) + '.\n'
    used = []
    for item in items:
        if len(used) >= owner_discovery.MAX_RESULTS:
            break
        title = item.get('translated_title', item['title'])
        title = ' '.join(title.split())[:150]
        line = f'{len(used) + 1}. {title}\n{item["source"]} · {item["published_at"][:16].replace("T", " ")} UTC\n{item["url"]}\n'
        footer = f'\nВыбрать новости: /news_pick {owner_batch} 1 2\nСценарии и рендер не запущены.'
        if len((prefix + line + footer).encode('utf-16-le')) // 2 > 3800:
            break
        prefix += line
        used.append(item)
    if not used:
        prefix += 'Свежих новостей не найдено.\n'
    if len(used) < len(items):
        prefix += 'Показана часть новостей; другие варианты: /news_discover.\n'
    if len(used) >= 2:
        prefix += f'\nВыбрать от 2 до 4 новостей: /news_pick {owner_batch} 1 2\n'
    prefix += 'Сценарии и рендер не запущены.'
    return prefix, used


async def preview(db, owner_id: int, owner_batch: int, key: str, model: str, *, now: datetime | None = None,
                  translate=None) -> str:
    now = now_utc(now)
    snapshot = await owner_discovery.get_snapshot(db, owner_id, owner_batch)
    if not snapshot:
        items = await recent_items(db, now)
        state = await db.news_owner_daily_state.find_one({'_id': 'last_poll'}) or {}
        failed = state.get('failed', [])
        translate = translate or owner_discovery.translate_titles
        candidates = items[:owner_discovery.MAX_RESULTS]
        titles = await translate(candidates, key, model) if candidates else None
        if titles and len(titles) == len(candidates):
            for item, title in zip(candidates, titles):
                item['translated_title'] = title
        _, selected = format_briefing(date_key(now), owner_batch, items, failed, daily=False)
        snapshot = await owner_discovery.save_snapshot(db, owner_id, owner_batch, selected, failed)
        omitted = len(selected) < len(items)
    else:
        omitted = False
    text, _ = format_briefing(date_key(now), owner_batch, snapshot['items'], snapshot['failed'], daily=False)
    if omitted:
        text = text.replace('Сценарии и рендер не запущены.',
            'Показана часть новостей; другие варианты: /news_discover.\nСценарии и рендер не запущены.')
    return text


async def send_due(db, bot, owner_id: int, key: str, model: str, *, now: datetime | None = None,
                   fetch=None, translate=None, check_enabled: bool = False) -> str:
    """Called under a cross-process lease. Resumes preparation, never uncertain sends."""
    if type(owner_id) is not int or owner_id <= 0:
        raise ValueError('Неверный владелец')
    now = now_utc(now)
    local = now.astimezone(MSK)
    start = await initialize(db, owner_id, now)
    day = date_key(now)
    if day < start or local.hour < 3:
        return 'not_due'
    ident = f'{owner_id}:{day}'
    await db.news_owner_daily_deliveries.update_one({'_id': ident},
        {'$setOnInsert': {'owner_id': owner_id, 'day': day, 'stage': 'preparing', 'created_at': now}}, upsert=True)
    record = await db.news_owner_daily_deliveries.find_one({'_id': ident})
    if record.get('stage') in ('sent', 'sending', 'uncertain'):
        return record['stage']
    if record.get('stage') == 'preparing':
        # Refresh once at the deadline, in addition to the regular 30-minute polls.
        _, failed = await poll(db, now=now, fetch=fetch)
        window_end = datetime.combine(local.date(), datetime.min.time(), MSK) + timedelta(hours=3)
        end = window_end.astimezone(timezone.utc)
        items = await recent_items(db, end)
        translate = translate or owner_discovery.translate_titles
        candidates = items[:owner_discovery.MAX_RESULTS]
        translated = await translate(candidates, key, model) if candidates else None
        if translated and len(translated) == len(candidates):
            for item, title in zip(candidates, translated):
                item['translated_title'] = title
        ident_batch = batch_id(day)
        _, selected = format_briefing(day, ident_batch, items, failed)
        # Number-to-URL mappings must exist before a message can be sent.
        snapshot = await owner_discovery.save_snapshot(db, owner_id, ident_batch, selected, failed)
        text, selected_again = format_briefing(day, ident_batch, snapshot['items'], snapshot['failed'])
        if len(selected_again) != len(snapshot['items']):
            raise ValueError('Подборка не помещается')
        if len(selected) < len(items):
            # The snapshot contains only displayed entries; keep the omission notice.
            text = text.replace('Сценарии и рендер не запущены.',
                'Показана часть новостей; другие варианты: /news_discover.\nСценарии и рендер не запущены.')
        await db.news_owner_daily_deliveries.update_one({'_id': ident, 'stage': 'preparing'},
            {'$set': {'stage': 'ready', 'text': text, 'batch_id': ident_batch}})
        record = await db.news_owner_daily_deliveries.find_one({'_id': ident})
    if record.get('stage') != 'ready':
        return record.get('stage', 'unknown')
    # The owner may have switched collection off while sources were fetched.
    if check_enabled and not await enabled(db):
        return 'disabled'
    claim = await db.news_owner_daily_deliveries.update_one({'_id': ident, 'stage': 'ready'},
        {'$set': {'stage': 'sending', 'attempted_at': now}})
    if not claim.modified_count:
        return 'changed'
    try:
        msg = await bot.send_message(owner_id, record['text'], parse_mode=None, disable_web_page_preview=True)
    except Exception:
        await db.news_owner_daily_deliveries.update_one({'_id': ident, 'stage': 'sending'},
                                                        {'$set': {'stage': 'uncertain'}})
        return 'uncertain'
    await db.news_owner_daily_deliveries.update_one({'_id': ident, 'stage': 'sending'},
        {'$set': {'stage': 'sent', 'message_id': msg.message_id, 'sent_at': now}})
    return 'sent'

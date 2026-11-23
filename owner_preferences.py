"""Global owner controls stored in Mongo, with legacy environment defaults.

Reading a setting never changes it. Opening the owner's panel copies the old
settings once, so removing the old environment flags afterwards keeps the same
behaviour. Credentials and connection addresses remain in the environment.
"""
from __future__ import annotations

import os
from config import ADMIN_ID

SETTING = 'owner_control_panel'


def legacy_defaults() -> dict:
    return {
        'image_provider': 'cloudflare' if os.getenv('IMAGE_PROVIDER_MODE', '').strip().lower() == 'cloudflare' else 'legacy',
        'search_images': os.getenv('SEARCH_IMAGES', '').strip().lower() == 'enabled',
    }


async def get(settings) -> dict:
    values = legacy_defaults()
    doc = await settings.find_one({'setting': SETTING, 'chat_id': ADMIN_ID}) or {}
    provider = doc.get('image_provider')
    images = doc.get('search_images')
    if provider in ('legacy', 'cloudflare'):
        values['image_provider'] = provider
    if type(images) is bool:
        values['search_images'] = images
    return values


async def current() -> dict:
    from database import db
    return await get(db.settings)


async def migrate(settings) -> None:
    await settings.update_one({'setting': SETTING, 'chat_id': ADMIN_ID},
        {'$setOnInsert': legacy_defaults()}, upsert=True)


async def set_image_provider(settings, provider: str) -> None:
    if provider not in ('legacy', 'cloudflare'):
        raise ValueError('Неизвестный сервис изображений')
    # Preserve the other legacy value even if this is the first operation.
    await migrate(settings)
    await settings.update_one({'setting': SETTING, 'chat_id': ADMIN_ID},
                              {'$set': {'image_provider': provider}})


async def set_search_images(settings, value: bool) -> None:
    if type(value) is not bool:
        raise ValueError('Неверная настройка поиска')
    await migrate(settings)
    await settings.update_one({'setting': SETTING, 'chat_id': ADMIN_ID},
                              {'$set': {'search_images': value}})


# Admission metadata only. No Raid save, class level or reward is stored here.
# A deterministic Mongo _id deduplicates requests without a new startup index.
# chat_id is also required by the existing unique (setting, chat_id) index;
# missing it would make the second tester collide with the first on null.
RAID_TESTER_SETTING = 'raid_tester_access_v1'
RAID_TESTER_STATUSES = ('pending', 'allowed', 'denied', 'revoked')


def raid_tester_key(user_id: int) -> str:
    if type(user_id) is not int or not 0 < user_id < 2 ** 52 or user_id == ADMIN_ID:
        raise ValueError('Неверный ID тестера')
    return f'{RAID_TESTER_SETTING}:{ADMIN_ID}:{user_id}'


async def get_raid_tester(settings, user_id: int) -> dict | None:
    doc = await settings.find_one({'_id': raid_tester_key(user_id), 'setting': RAID_TESTER_SETTING,
                                   'owner_id': ADMIN_ID, 'user_id': user_id, 'chat_id': user_id})
    if doc is not None and (doc.get('status') not in RAID_TESTER_STATUSES
                            or type(doc.get('revision')) is not int or not 0 <= doc['revision'] < 10 ** 9):
        raise ValueError('Несовместимая запись допуска')
    return doc


async def request_raid_tester(settings, user_id: int, name: str) -> tuple[dict, bool]:
    from datetime import datetime, timezone
    from pymongo.errors import DuplicateKeyError
    import unicodedata
    clean_name = ''.join(c for c in str(name) if not unicodedata.category(c).startswith('C'))[:80].strip()
    doc = {'_id': raid_tester_key(user_id), 'setting': RAID_TESTER_SETTING, 'owner_id': ADMIN_ID,
           'user_id': user_id, 'chat_id': user_id, 'name': clean_name or 'Тестер', 'status': 'pending', 'revision': 0,
           'requested_at': datetime.now(timezone.utc)}
    try:
        result = await settings.update_one({'_id': doc['_id']}, {'$setOnInsert': doc}, upsert=True)
        created = result.upserted_id is not None
    except DuplicateKeyError:
        created = False  # A concurrent first request inserted the same unique ID.
    saved = await get_raid_tester(settings, user_id)
    if saved is None:
        raise ValueError('Заявка не сохранена')
    return saved, created


async def change_raid_tester(settings, user_id: int, revision: int, status: str) -> bool:
    from datetime import datetime, timezone
    if type(revision) is not int or not 0 <= revision < 10 ** 9 - 1:
        return False
    previous = {'allowed': ['pending', 'denied', 'revoked'], 'denied': ['pending'], 'revoked': ['allowed']}
    if status not in previous:
        return False
    result = await settings.update_one(
        {'_id': raid_tester_key(user_id), 'setting': RAID_TESTER_SETTING, 'owner_id': ADMIN_ID,
         'user_id': user_id, 'chat_id': user_id, 'revision': revision, 'status': {'$in': previous[status]}},
        {'$set': {'status': status, 'decided_at': datetime.now(timezone.utc)}, '$inc': {'revision': 1}})
    return result.modified_count == 1


async def list_raid_testers(settings, status: str, page: int) -> tuple[list, bool]:
    if status not in ('pending', 'allowed', 'all') or type(page) is not int or not 0 <= page <= 100000:
        raise ValueError('Неверная страница тестеров')
    query = {'setting': RAID_TESTER_SETTING, 'owner_id': ADMIN_ID}
    if status != 'all':
        query['status'] = status
    rows = await settings.find(query).sort([('requested_at', -1), ('_id', 1)]).skip(page * 8).limit(9).to_list(length=9)
    return rows[:8], len(rows) > 8


# Notification preferences are independent of admission and never contain saves.
RAID_ANNOUNCEMENT_SETTING = 'raid_update_announcement_v1'


def raid_tester_updates_enabled(doc: dict) -> bool:
    # Existing testers are subscribed until they opt out. Invalid values fail
    # closed; changing admission must never reset an explicit refusal.
    return doc.get('update_notifications', True) is True


async def set_raid_tester_updates(settings, user_id: int, enabled: bool) -> bool:
    if type(enabled) is not bool:
        raise ValueError('Неверная настройка уведомлений')
    doc = await get_raid_tester(settings, user_id)
    if doc is None:
        return False
    result = await settings.update_one(
        {'_id': raid_tester_key(user_id), 'setting': RAID_TESTER_SETTING,
         'owner_id': ADMIN_ID, 'user_id': user_id, 'chat_id': user_id},
        {'$set': {'update_notifications': enabled}})
    # Idempotent explicit on/off buttons, not a stale-message toggle. Do not
    # increment the admission revision or create records for unknown senders.
    return result.matched_count == 1


async def raid_update_recipients(settings) -> list[int]:
    query = {'setting': RAID_TESTER_SETTING, 'owner_id': ADMIN_ID, 'status': 'allowed',
             '$or': [{'update_notifications': True}, {'update_notifications': {'$exists': False}}]}
    recipients = []
    async for doc in settings.find(query).sort('user_id', 1):
        uid = doc.get('user_id')
        if (type(uid) is int and 0 < uid < 2 ** 52 and uid != ADMIN_ID
                and doc.get('_id') == raid_tester_key(uid) and doc.get('chat_id') == uid
                and type(doc.get('revision')) is int and 0 <= doc['revision'] < 10 ** 9
                and raid_tester_updates_enabled(doc)):
            recipients.append(uid)
    return recipients


async def get_raid_announcement(settings) -> dict | None:
    return await settings.find_one({'_id': f'{RAID_ANNOUNCEMENT_SETTING}:{ADMIN_ID}',
                                    'setting': RAID_ANNOUNCEMENT_SETTING, 'chat_id': ADMIN_ID,
                                    'owner_id': ADMIN_ID})


async def claim_raid_announcement(settings, token: str, build: str, total: int) -> bool:
    """Persist an at-most-once send claim BEFORE the first Telegram request.

    Only the latest claim/report is retained. Drafts live in the owner's FSM;
    callbacks also have to match that current draft, so an old button cannot
    become valid again after a restart or a newer announcement.
    """
    from datetime import datetime, timezone
    from pymongo.errors import DuplicateKeyError
    import re
    if (not re.fullmatch(r'[a-f0-9]{16}', token) or not re.fullmatch(r'[a-f0-9]{12}', build)
            or type(total) is not int or total < 1):
        raise ValueError('Неверный черновик объявления')
    key = f'{RAID_ANNOUNCEMENT_SETTING}:{ADMIN_ID}'
    try:
        result = await settings.update_one({'_id': key, 'setting': RAID_ANNOUNCEMENT_SETTING,
                                             'chat_id': ADMIN_ID, 'owner_id': ADMIN_ID, 'token': {'$ne': token}},
            {'$setOnInsert': {'setting': RAID_ANNOUNCEMENT_SETTING, 'chat_id': ADMIN_ID, 'owner_id': ADMIN_ID},
             '$set': {'token': token, 'build': build, 'status': 'sending', 'total': total,
                      'sent': 0, 'unconfirmed': 0, 'skipped': 0, 'remaining': total,
                      'started_at': datetime.now(timezone.utc)},
             '$unset': {'finished_at': ''}}, upsert=True)
        return result.modified_count == 1 or result.upserted_id is not None
    except DuplicateKeyError:
        return False  # This exact draft was already claimed, including pre-crash.


async def finish_raid_announcement(settings, token: str, *, sent: int, unconfirmed: int,
                                   skipped: int, remaining: int) -> None:
    from datetime import datetime, timezone
    await settings.update_one(
        {'_id': f'{RAID_ANNOUNCEMENT_SETTING}:{ADMIN_ID}', 'setting': RAID_ANNOUNCEMENT_SETTING,
         'owner_id': ADMIN_ID, 'chat_id': ADMIN_ID, 'token': token},
        {'$set': {'status': 'complete' if remaining == 0 else 'interrupted',
                  'sent': sent, 'unconfirmed': unconfirmed, 'skipped': skipped, 'remaining': remaining,
                  'finished_at': datetime.now(timezone.utc)}})

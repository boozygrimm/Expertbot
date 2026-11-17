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

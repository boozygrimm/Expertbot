"""Persistent, ordered owner selection from the general news store.

Selecting/removing never changes news or performs network/model work.
"""
from pymongo.errors import DuplicateKeyError
from news_prototype import article_fetch
from news_prototype.telegram_post import widget_for

LIMIT = 20


async def ids(db, owner_id):
    row = await db.news_general_bulletin_baskets.find_one({'_id': owner_id}) or {}
    return row.get('ids', [])


async def change(db, owner_id, ident, *, add):
    coll = db.news_general_bulletin_baskets
    try:
        await coll.update_one({'_id': owner_id}, {'$setOnInsert': {'ids': []}}, upsert=True)
    except DuplicateKeyError:
        pass  # Another concurrent click created the basket.
    for _ in range(10):
        old = (await coll.find_one({'_id': owner_id}))['ids']
        if add and ident in old:
            return 'duplicate'
        if not add and ident not in old:
            return 'missing'
        if add and len(old) >= LIMIT:
            return 'full'
        new = old + [ident] if add else [i for i in old if i != ident]
        result = await coll.update_one({'_id': owner_id, 'ids': old}, {'$set': {'ids': new}})
        if result.modified_count:
            return 'saved'
    return 'busy'


def choice(item):
    if not item or item.get('hidden') is True:
        raise ValueError('Новость недоступна')
    try:
        url = article_fetch.validate_url(item.get('url'))
        widget_for(url)
    except article_fetch.ArticleError:
        raise ValueError('Ссылка недоступна') from None
    title = ' '.join(str(item.get('title') or '').split())[:180] or 'Новость по ссылке'
    return {'url': url, 'title': title}


async def choices(db, owner_id):
    selected = await ids(db, owner_id)
    if not 2 <= len(selected) <= LIMIT:
        raise ValueError('Нужно от двух до двадцати новостей')
    docs = await db.news_general_candidates.find({'_id': {'$in': selected}}).to_list(length=LIMIT)
    by_id = {doc['_id']: doc for doc in docs}
    result = [choice(by_id.get(ident)) for ident in selected]
    if len({item['url'] for item in result}) != len(result):
        raise ValueError('Повторяются ссылки')
    return result

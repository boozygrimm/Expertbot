from collections import Counter
from datetime import datetime, timezone
import os

import pytest
from mongomock_motor import AsyncMongoMockClient

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
from news_prototype import general_auto, general_editorial, general_queue

NOW = datetime(2026, 1, 2, 8, tzinfo=timezone.utc)


@pytest.mark.parametrize('title,topic', [
    ('Новая нейросеть создаёт изображения', 'ai'),
    ('Компания представила процессор', 'technology'),
    ('Вышла новая видеоигра', 'games'),
    ('Режиссёр рассказал о новом фильме', 'cinema'),
    ('В музее открылась выставка', 'culture'),
    ('Физики провели эксперимент', 'science'),
    ('Клинические испытания вакцины завершены', 'health'),
    ('NASA отправит марсоход', 'space'),
    ('Учёные изучают климат океана', 'nature'),
    ('Животные вернулись в заповедник', 'animals'),
    ('Курс биткоина вырос', 'crypto'),
    ('Центробанк изменил процентную ставку', 'business'),
    ('Завод выпустил электромобиль', 'industry_transport'),
    ('Парламент объявил о выборах', 'politics_society'),
    ('Расследование завершено', 'law_investigations'),
    ('Футбол: стартовал чемпионат', 'sport'),
    ('Туризм и путешествия по стране', 'travel_life'),
    ('Необычная находка озадачила жителей', 'odd'),
    ('Археологи начали раскопки', 'history'),
    ('A new video game is coming', 'games'),
    ('New vaccine enters trial', 'health'),
])
def test_headline_topics_cover_broad_options(title, topic):
    result = general_editorial.assess(title, 'general')
    assert topic in result['topics']
    assert result['basis'] == 'headline'


def test_fallback_multitopic_and_ad_markers():
    assert general_editorial.assess('Событие произошло вчера', 'space')['topics'] == ['space']
    assert general_editorial.assess('Событие произошло вчера', 'unknown')['topics'] == ['general']
    multi = general_editorial.assess('Вакцина для животных', 'general')
    assert set(multi['topics']) == {'health', 'animals'}
    assert general_editorial.choose_topic(multi, Counter({'health': 3})) == 'animals'
    assert general_editorial.assess('Реклама: подписка на журнал')['defer_reason'] == 'advertising_marker'
    assert general_editorial.assess('Подарок подписчикам #реклама')['defer_reason'] == 'advertising_marker'
    assert general_editorial.assess('Исследователи изучили влияние рекламы на детей')['defer_reason'] is None
    assert 'animals' not in general_editorial.assess('Китай объявил о новых планах')['topics']


@pytest.mark.asyncio
async def test_topic_balance_uses_headline_not_just_publisher():
    db = AsyncMongoMockClient().test
    ai_key = await general_queue.add_news(db, 'https://example.com/ai', now=NOW)
    sport_key = await general_queue.add_news(db, 'https://example.com/sport', now=NOW)
    await db.news_general_candidates.update_one({'_id': ai_key}, {'$set': {
        'title': 'Нейросеть получила новые возможности', 'source_id': 'tass', 'topic_hint': 'general'}})
    await db.news_general_candidates.update_one({'_id': sport_key}, {'$set': {
        'title': 'Футбол: стартовал чемпионат страны', 'source_id': 'tass', 'topic_hint': 'general'}})
    await db.news_general_attempts.insert_one({'_id': 'previous', 'at': NOW, 'source_id': 'tass', 'topic_hint': 'ai'})
    chosen = await general_auto.pick(db, NOW)
    assert chosen['_id'] == sport_key
    assert chosen['topic_hint'] == 'sport' and chosen['topic_basis'] == 'headline'


@pytest.mark.asyncio
async def test_ads_and_similar_stories_are_deferred_but_manually_selectable():
    db = AsyncMongoMockClient().test
    first = await general_queue.add_news(db, 'https://example.com/first', now=NOW)
    duplicate = await general_queue.add_news(db, 'https://example.com/duplicate', now=NOW)
    advert = await general_queue.add_news(db, 'https://example.com/advert', now=NOW)
    await db.news_general_candidates.update_one({'_id': first}, {'$set': {
        'title': 'Учёные обнаружили новый редкий минерал на Луне', 'stage': 'draft_ready'}})
    await db.news_general_candidates.update_one({'_id': duplicate}, {'$set': {
        'title': 'Учёные обнаружили новые редкие минералы на Луне'}})
    await db.news_general_candidates.update_one({'_id': advert}, {'$set': {'title': 'Реклама: наш новый проект'}})
    assert await general_auto.pick(db, NOW) is None
    duplicate_item = await db.news_general_candidates.find_one({'_id': duplicate})
    advert_item = await db.news_general_candidates.find_one({'_id': advert})
    assert await general_auto.defer_reason(db, duplicate_item, NOW) == 'similar_headline'
    assert await general_auto.defer_reason(db, advert_item, NOW) == 'advertising_marker'
    assert await general_queue.reserve(db, duplicate, 123) is not None
    assert await general_queue.reserve(db, advert, 123) is not None
    assert await db.news_general_candidates.count_documents({}) == 3


def test_similarity_conservatively_respects_numbers_and_negations():
    original = 'Учёные обнаружили новый редкий минерал на Луне'
    assert general_auto.similar_title(original, 'Учёные обнаружили новые редкие минералы на Луне')
    assert not general_auto.similar_title(original, 'Учёные не обнаружили новые редкие минералы на Луне')
    assert not general_auto.similar_title('Учёные обнаружили 10 новых редких минералов на Луне',
                                           'Учёные обнаружили 20 новых редких минералов на Луне')
    assert not general_auto.similar_title('Новости сегодня', 'Новости сегодня')


@pytest.mark.parametrize('title', [
    'Путин встретился с лидерами', 'В Кремле обсудили встречу',
    'Трамп ответил журналистам', 'Зеленский выступил с заявлением',
    'МИД сделал заявление', 'В НАТО ответили', 'Госдума рассмотрит предложение',
])
def test_obvious_political_headlines_get_preliminary_label(title):
    assert 'politics_society' in general_editorial.assess(title)['topics']


@pytest.mark.parametrize('title,topic', [
    ('YouTube обновил приложение', 'internet'),
    ('Интернет и социальные сети изменились', 'internet'),
    ('Комик рассказал шутку', 'humor'),
    ('Мем стал популярным', 'humor'),
])
def test_additional_categories(title, topic):
    assert topic in general_editorial.assess(title)['topics']

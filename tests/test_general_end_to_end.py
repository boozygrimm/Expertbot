"""Simulated complete owner flow. No live providers, Telegram or network."""
import json
import os
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from mongomock_motor import AsyncMongoMockClient

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
from handlers import general_news_panel, news_owner_drafts, owner_news_actions
from news_prototype import (article_fetch, general_auto, general_discovery, general_queue,
                            general_sources, owner_drafts, owner_trial, story_editor, story_review)

TEXT = ('В публикации сообщается о проверяемом событии. Источник приводит подробности и отмечает, '
        'что некоторые результаты остаются предварительными и требуют дальнейшей проверки.')


@pytest.mark.asyncio
async def test_full_flow_collection_diversity_draft_review_and_panel():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    db = AsyncMongoMockClient().test
    for source in general_sources.builtin_sources():
        await general_sources.remove_source(db, source['id'])
    await general_sources.add_source(db, 'telegram', 'Sport News', '@sport_wire', 'general')
    await general_sources.add_source(db, 'rss', 'Научная лента', 'https://example.com/rss', 'general')
    date = now - timedelta(minutes=10)
    html = f'''<div class="tgme_widget_message" data-post="sport_wire/100">
      <div class="tgme_widget_message_text js-message_text">Футбол: команда победила в финале чемпионата</div>
      <time datetime="{date.isoformat()}"></time></div>'''.encode()
    rss = f'''<rss><channel><item><title>NASA выложило изображения поверхности Марса</title>
      <link>https://news.example.com/science?utm_source=rss</link>
      <pubDate>{format_datetime(date, usegmt=True)}</pubDate></item></channel></rss>'''.encode()
    async def fetch_feed(session, url):
        if url == 'https://t.me/s/sport_wire':
            return html
        if url == 'https://example.com/rss':
            return rss
        raise AssertionError('unexpected network')
    async def fetch_article(url):
        if 't.me/' in url:
            return {'title': 'Матч', 'summary': TEXT, 'telegram_channel_name': 'Sport News',
                    'resolved_url': 'https://t.me/sport_wire/100?embed=1'}
        return {'title': 'Изображения Марса', 'summary': TEXT, 'resolved_url': 'https://wire.example.org/article'}
    async def edit(candidate):
        output = {'decision': 'draft', 'turns': [
            {'speaker': 'Бендер', 'text': 'Источник рассказал о событии.', 'kind': 'fact',
             'evidence': 'В публикации сообщается о проверяемом событии'},
            {'speaker': 'Киберсенсей', 'text': 'Интересно, что будет дальше.', 'kind': 'comment', 'evidence': ''}],
            'ticker': 'ПОДРОБНОСТИ СОБЫТИЯ'}
        return story_editor.parse_draft(json.dumps(output, ensure_ascii=False), candidate)
    review_request = AsyncMock(return_value=json.dumps({'status': 'issues', 'issues': [
        {'location': 'Строка', 'reason': 'Сверить формулировку', 'quote': ''}]}, ensure_ascii=False))
    with patch.object(general_auto, 'keys_ready', return_value=True), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)), \
         patch.object(general_discovery, 'owner_discovery') as discovery_owner:
        # Delegate parsers to the real module; replace only the network fetch.
        from news_prototype import owner_discovery
        discovery_owner._fetch = AsyncMock(side_effect=fetch_feed)
        discovery_owner.parse_channel = owner_discovery.parse_channel
        discovery_owner._clean_feed = owner_discovery._clean_feed
        with patch.object(article_fetch, 'fetch_article', AsyncMock(side_effect=fetch_article)), \
             patch.object(news_owner_drafts, 'make_draft', AsyncMock(side_effect=edit)), \
             patch.object(story_review, 'request_groq', review_request):
            assert not await general_auto.enabled(db)
            await general_auto.set_enabled(db, True)
            assert await general_auto.collect(db) == 'collected'
            assert await db.news_general_candidates.count_documents({}) == 2
            assert await general_auto.tick(db, 123, now=now) == 'draft_ready'
            assert await general_auto.tick(db, 123, now=now + timedelta(minutes=15)) == 'draft_ready'
            assert await general_auto.usage(db, now) == 2
    docs = await db.news_owner_drafts.find({'owner_id': 123}).to_list(length=10)
    assert len(docs) == 2 and review_request.await_count == 2
    scripts = [doc['script']['turns'][0]['text'] for doc in docs]
    assert any('телеграм-канале «Sport News»' in text for text in scripts)
    assert any('сайте «wire.example.org»' in text for text in scripts)
    assert all(doc['independent_review']['status'] == 'issues' for doc in docs)
    assert all(doc['verification'] == 'needs_review' for doc in docs)
    attempts = await db.news_general_attempts.find({}).to_list(length=10)
    assert {entry['topic_hint'] for entry in attempts} == {'space', 'sport'}
    # Ready list and compact card work with existing buttons and no render.
    message = SimpleNamespace(message_id=55, chat=SimpleNamespace(id=123, type='private'),
        from_user=SimpleNamespace(id=123), panel_mode=True, edit_text=AsyncMock(), answer=AsyncMock())
    fake_db = SimpleNamespace(db=db)
    with patch.object(general_news_panel, 'db', fake_db), patch.object(owner_news_actions, 'db', fake_db):
        await general_news_panel.news_list(message, 123, 0, 'ready')
        assert 'Готовые сценарии · 2' in message.edit_text.await_args.args[0]
        await news_owner_drafts.show(message, docs[0]['draft_id'], item=docs[0], panel_mode=True)
        assert 'Сверить формулировку' in message.answer.await_args.args[0]
    assert await db.news_owner_trial_jobs.count_documents({}) == 0


@pytest.mark.asyncio
async def test_source_prefix_does_not_truncate_long_first_turn_and_aligns_evidence():
    db = AsyncMongoMockClient().test
    key = await general_queue.add_news(db, 'https://example.com/article')
    fetch = AsyncMock(return_value={'title': 'Статья', 'summary': TEXT,
                                    'resolved_url': 'https://source.example.org/story'})
    draft = AsyncMock(return_value={'decision': 'draft', 'verification': 'needs_review',
        'script': {'ticker': 'Новость', 'turns': [{'speaker': 'Бендер', 'text': 'А' * 470},
                                                  {'speaker': 'Киберсенсей', 'text': 'Принято.'}]},
        'evidence': [{'kind': 'fact', 'quote': 'текст'}, {'kind': 'comment', 'quote': ''}]})
    assert await general_queue.prepare_reserved(db, key, 123, fetch, draft) == 'draft_ready'
    item = await owner_drafts.get(db, 123, general_queue.draft_id_for(key))
    assert item['script']['turns'][0]['text'] == 'На сайте «source.example.org» поделились новостью.'
    assert item['script']['turns'][1]['text'] == 'А' * 470
    assert len(item['evidence']) == 3 and item['evidence'][0]['quote'] == ''


@pytest.mark.asyncio
async def test_telegram_missing_display_name_uses_verified_post_username():
    db = AsyncMongoMockClient().test
    key = await general_queue.add_news(db, 'https://t.me/Good_Channel/42')
    fetch = AsyncMock(return_value={'title': 'Пост', 'summary': TEXT})
    draft = AsyncMock(return_value={'decision': 'draft', 'verification': 'needs_review',
        'script': {'ticker': 'Новость', 'turns': [{'speaker': 'Бендер', 'text': 'Событие.'},
                                                  {'speaker': 'Киберсенсей', 'text': 'Понятно.'}]}, 'evidence': []})
    assert await general_queue.prepare_reserved(db, key, 123, fetch, draft) == 'draft_ready'
    item = await owner_drafts.get(db, 123, general_queue.draft_id_for(key))
    assert 'телеграм-канале «Good_Channel»' in item['script']['turns'][0]['text']


@pytest.mark.asyncio
async def test_one_button_real_queue_editor_review_and_category_selection():
    db = AsyncMongoMockClient().test
    game = await general_queue.add_news(db, 'https://example.com/game')
    politics = await general_queue.add_news(db, 'https://example.com/politics')
    await db.news_general_candidates.update_one({'_id': game}, {'$set': {'title': 'Видеоигра вышла'}})
    await db.news_general_candidates.update_one({'_id': politics}, {'$set': {'title': 'Путин выступил'}})
    await general_auto.set_category_enabled(db, 'politics_society', False)
    fetch = AsyncMock(return_value={'title': 'Игра', 'summary': TEXT, 'resolved_url': 'https://example.com/game'})
    edit = AsyncMock(return_value={'decision': 'draft', 'verification': 'needs_review',
        'script': {'ticker': 'Игра', 'turns': [{'speaker': 'Бендер', 'text': 'Новость.'},
                                               {'speaker': 'Киберсенсей', 'text': 'Принято.'}]}, 'evidence': []})
    review_request = AsyncMock(return_value='{"status":"issues","issues":[{"location":"Строка","reason":"Сверить","quote":""}]}')
    with patch.object(general_auto, 'keys_ready', return_value=True), \
         patch.object(owner_trial, 'drafts_enabled', AsyncMock(return_value=True)), \
         patch.object(article_fetch, 'fetch_article', fetch), \
         patch.object(news_owner_drafts, 'make_draft', edit), \
         patch.object(story_review, 'request_groq', review_request):
        result = await general_auto.run_once(db, 123, poll=AsyncMock(return_value=(0, [])))
    assert result['status'] == 'no_candidates' and result['ready'] == 1
    draft = await owner_drafts.get(db, 123, general_queue.draft_id_for(game))
    assert draft['independent_review']['status'] == 'issues'
    assert draft['verification'] == 'needs_review'
    assert 'example.com' in draft['script']['turns'][0]['text']
    assert (await db.news_general_candidates.find_one({'_id': politics}))['stage'] == 'collected'
    assert not await general_auto.enabled(db)
    assert await db.news_owner_trial_jobs.count_documents({}) == 0
    fetch.assert_awaited_once(); edit.assert_awaited_once(); review_request.assert_awaited_once()

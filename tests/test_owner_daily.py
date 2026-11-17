import asyncio
from datetime import datetime, timedelta, timezone
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
from mongomock_motor import AsyncMongoMockClient
from news_prototype import owner_daily, owner_discovery, owner_trial, news_scheduler

DAY = datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc)  # 03:00 Moscow
ITEMS = [
    {'source': 'Crypto Sensei', 'title': 'Новости ИИ', 'url': 'https://t.me/SenseiDurova/42', 'published_at': (DAY-timedelta(hours=1)).isoformat()},
    {'source': 'OpenAI', 'title': 'A new AI model', 'url': 'https://example.com/story?utm_source=rss&amp;utm_medium=x', 'published_at': (DAY-timedelta(hours=2)).isoformat()},
]


class DailyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        self.bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=101)))
        self.fetch = AsyncMock(return_value=(ITEMS, ['Decrypt']))
        self.translate = AsyncMock(return_value=['Новости ИИ', 'Новая модель ИИ'])

    async def test_enabled_only_with_separate_flag_and_existing_owner_mode(self):
        flags = {'NEWS_OWNER_DAILY_ENABLED':'1', 'NEWS_OWNER_DRAFT_ENABLED':'1', 'NEWS_OWNER_TRIAL_ENABLED':'1',
                 **{name:'0' for name in owner_trial.OTHER_FLAGS}}
        with patch.dict(os.environ, flags, clear=False):
            self.assertTrue(owner_daily.enabled())
            with patch.dict(os.environ, {'NEWS_OWNER_DAILY_ENABLED':'0'}): self.assertFalse(owner_daily.enabled())
            with patch.dict(os.environ, {'NEWS_COLLECTION_ENABLED':'1'}): self.assertFalse(owner_daily.enabled())

    async def test_poll_deduplicates_tracking_urls_and_keeps_status(self):
        self.assertEqual(await owner_daily.poll(self.db, now=DAY, fetch=self.fetch), (2, ['Decrypt']))
        self.assertEqual(await owner_daily.poll(self.db, now=DAY, fetch=self.fetch), (0, ['Decrypt']))
        self.assertEqual(await self.db.news_owner_daily_items.count_documents({}), 2)
        item = await self.db.news_owner_daily_items.find_one({'source':'OpenAI'})
        self.assertEqual(item['url'], 'https://example.com/story')
        self.assertEqual((await self.db.news_owner_daily_state.find_one({'_id':'last_poll'}))['failed'], ['Decrypt'])
        self.assertEqual(self.fetch.await_args.kwargs['per_source_limit'], 100)

    async def test_first_start_at_afternoon_waits_until_next_three(self):
        afternoon = DAY + timedelta(hours=12)
        result = await owner_daily.send_due(self.db, self.bot, 123, 'key', 'model', now=afternoon,
                                            fetch=self.fetch, translate=self.translate)
        self.assertEqual(result, 'not_due'); self.bot.send_message.assert_not_awaited()
        tomorrow = DAY + timedelta(days=1)
        new_items = [item | {'published_at': (tomorrow - timedelta(hours=1)).isoformat()} for item in ITEMS]
        self.fetch.return_value = (new_items, [])
        result = await owner_daily.send_due(self.db, self.bot, 123, 'key', 'model', now=tomorrow,
                                            fetch=self.fetch, translate=self.translate)
        self.assertEqual(result, 'sent'); self.bot.send_message.assert_awaited_once()

    async def test_moscow_time_delivery_snapshot_and_exactly_one_send(self):
        before = DAY - timedelta(minutes=1)
        self.assertEqual(await owner_daily.send_due(self.db, self.bot, 123, 'key', 'model', now=before,
                                                   fetch=self.fetch, translate=self.translate), 'not_due')
        self.assertEqual(await owner_daily.send_due(self.db, self.bot, 123, 'key', 'model', now=DAY,
                                                   fetch=self.fetch, translate=self.translate), 'sent')
        self.assertEqual(await owner_daily.send_due(self.db, self.bot, 123, 'key', 'model', now=DAY+timedelta(minutes=3),
                                                   fetch=self.fetch, translate=self.translate), 'sent')
        self.bot.send_message.assert_awaited_once()
        args = self.bot.send_message.await_args
        self.assertEqual(args.args[0], 123)
        self.assertIn('03:00 МСК', args.args[1]); self.assertIn('Недоступны', args.args[1])
        self.assertIn('Новая модель ИИ', args.args[1])
        batch = owner_daily.batch_id('2026-01-02')
        self.assertIn(f'/news_pick {batch} 1 2', args.args[1])
        snapshot = await owner_discovery.get_snapshot(self.db, 123, batch)
        self.assertEqual(discovery_urls(snapshot), ['https://t.me/SenseiDurova/42', 'https://example.com/story'])
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
        self.assertEqual(await self.db.news_owner_drafts.count_documents({}), 0)

    async def test_late_restart_only_sends_current_day_and_uses_three_am_window(self):
        await owner_daily.initialize(self.db, 123, DAY-timedelta(minutes=1))
        late = DAY + timedelta(hours=8)
        future = {'source':'Decrypt', 'title':'После трёх', 'url':'https://decrypt.co/later',
                  'published_at':(DAY+timedelta(minutes=5)).isoformat()}
        self.fetch.return_value = (ITEMS + [future], [])
        self.assertEqual(await owner_daily.send_due(self.db, self.bot, 123, 'key', 'model', now=late,
                                                   fetch=self.fetch, translate=self.translate), 'sent')
        text = self.bot.send_message.await_args.args[1]
        self.assertNotIn('После трёх', text)
        self.assertEqual(await self.db.news_owner_daily_deliveries.count_documents({}), 1)

    async def test_uncertain_send_does_not_repeat(self):
        await owner_daily.initialize(self.db, 123, DAY-timedelta(minutes=1))
        self.bot.send_message.side_effect = TimeoutError('ambiguous')
        self.assertEqual(await owner_daily.send_due(self.db, self.bot, 123, 'key', 'model', now=DAY,
                                                   fetch=self.fetch, translate=self.translate), 'uncertain')
        self.assertEqual(await owner_daily.send_due(self.db, self.bot, 123, 'key', 'model', now=DAY+timedelta(minutes=1),
                                                   fetch=self.fetch, translate=self.translate), 'uncertain')
        self.bot.send_message.assert_awaited_once()

    async def test_multiple_instances_with_lease_cannot_send_twice(self):
        await owner_daily.initialize(self.db, 123, DAY-timedelta(minutes=1))
        started, release = asyncio.Event(), asyncio.Event()
        async def send(*args, **kwargs):
            started.set(); await release.wait(); return SimpleNamespace(message_id=101)
        self.bot.send_message.side_effect = send
        async def work():
            return await owner_daily.send_due(self.db, self.bot, 123, 'key', 'model', now=DAY,
                                               fetch=self.fetch, translate=self.translate)
        first = asyncio.create_task(news_scheduler._locked(self.db.news_owner_daily_locks, 'delivery', work))
        await asyncio.wait_for(started.wait(), 1)
        await news_scheduler._locked(self.db.news_owner_daily_locks, 'delivery', work)
        release.set(); await first
        self.bot.send_message.assert_awaited_once()

    async def test_preview_uses_stored_items_and_does_not_send(self):
        await owner_daily.poll(self.db, now=DAY, fetch=self.fetch)
        text = await owner_daily.preview(self.db, 123, 52, 'key', 'model', now=DAY, translate=self.translate)
        self.assertIn('/news_pick 52 1 2', text)
        self.assertEqual(len((await owner_discovery.get_snapshot(self.db, 123, 52))['items']), 2)
        self.bot.send_message.assert_not_awaited()

    async def test_missing_translation_or_no_news_still_delivers(self):
        self.fetch.return_value = ([], ['OpenAI'])
        await owner_daily.initialize(self.db, 123, DAY-timedelta(minutes=1))
        self.assertEqual(await owner_daily.send_due(self.db, self.bot, 123, '', 'model', now=DAY,
                                                   fetch=self.fetch, translate=self.translate), 'sent')
        text = self.bot.send_message.await_args.args[1]
        self.assertIn('Свежих новостей не найдено', text)
        self.assertIn('OpenAI', text)
        self.assertNotIn('/news_pick', text)
        self.translate.assert_not_awaited()

    async def test_removes_only_expired_items(self):
        old = ITEMS[0] | {'url':'https://t.me/SenseiDurova/1', 'published_at':(DAY-timedelta(days=8)).isoformat()}
        self.fetch.return_value = (ITEMS + [old], [])
        await owner_daily.poll(self.db, now=DAY, fetch=self.fetch)
        self.assertEqual(await self.db.news_owner_daily_items.count_documents({}), 2)


def discovery_urls(snapshot):
    return [item['url'] for item in snapshot['items']]


class DailyIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def flags(self):
        return {'NEWS_OWNER_DAILY_ENABLED': '1', 'NEWS_OWNER_DRAFT_ENABLED': '1',
                'NEWS_OWNER_TRIAL_ENABLED': '1', **{name: '0' for name in owner_trial.OTHER_FLAGS}}

    async def test_scheduler_is_opt_in_and_registers_immediate_collection(self):
        import app_setup
        with patch.dict(os.environ, self.flags(), clear=False):
            scheduler = app_setup.create_scheduler()
            jobs = {job.func.__name__: job for job in scheduler.get_jobs()}
            self.assertIn('collect_owner_daily_news', jobs)
            self.assertIn('deliver_owner_daily_news', jobs)
            self.assertEqual(jobs['collect_owner_daily_news'].trigger.interval, timedelta(minutes=30))
            from apscheduler.triggers.cron import CronTrigger
            self.assertIsInstance(jobs['deliver_owner_daily_news'].trigger, CronTrigger)
            self.assertIsNotNone(jobs['collect_owner_daily_news'].next_run_time)
            self.assertIsNotNone(jobs['deliver_owner_daily_news'].next_run_time)
        with patch.dict(os.environ, {**self.flags(), 'NEWS_OWNER_DAILY_ENABLED': '0'}, clear=False):
            names = {job.func.__name__ for job in app_setup.create_scheduler().get_jobs()}
            self.assertNotIn('collect_owner_daily_news', names)
            self.assertNotIn('deliver_owner_daily_news', names)

    async def test_handlers_private_owner_only_and_preview_is_pickable(self):
        from handlers import news_owner_drafts as handlers
        db = AsyncMongoMockClient().test
        owner = handlers.ADMIN_ID
        message = SimpleNamespace(from_user=SimpleNamespace(id=owner), chat=SimpleNamespace(type='private'),
                                  message_id=55, answer=AsyncMock())
        await owner_daily.poll(db, now=datetime.now(timezone.utc), fetch=AsyncMock(return_value=(
            [x | {'published_at': datetime.now(timezone.utc).isoformat()} for x in ITEMS], [])))
        with patch.dict(os.environ, self.flags(), clear=False), patch.object(handlers, 'db', SimpleNamespace(db=db)):
            with patch.object(owner_discovery, 'translate_titles', AsyncMock(return_value=None)):
                await handlers.collected(message)
            self.assertIn('/news_pick 55 1 2', message.answer.await_args.args[0])
            snapshot = await owner_discovery.get_snapshot(db, owner, 55)
            self.assertEqual(len(owner_discovery.choose(snapshot, [1, 2])), 2)
            await handlers.daily_status(message)
            self.assertIn('Собрано за сутки: 2', message.answer.await_args.args[0])
            for bad in (SimpleNamespace(id=owner + 1), None):
                stranger = SimpleNamespace(from_user=bad, chat=SimpleNamespace(type='private'), answer=AsyncMock())
                await handlers.collected(stranger)
                await handlers.daily_status(stranger)
                stranger.answer.assert_not_awaited()
            message.chat.type = 'group'
            message.answer.reset_mock()
            await handlers.collected(message)
            await handlers.daily_status(message)
            message.answer.assert_not_awaited()

    async def test_briefing_limits_and_marks_omissions(self):
        items = [{'source': 'OpenAI', 'title': '📰' * 150, 'url': f'https://example.com/{i}?x=' + 'x' * 400,
                  'published_at': DAY.isoformat()} for i in range(30)]
        text, selected = owner_daily.format_briefing('2026-01-02', owner_daily.batch_id('2026-01-02'), items, [])
        self.assertLessEqual(len(text.encode('utf-16-le')) // 2, 4096)
        self.assertLess(len(selected), 30)
        self.assertIn('Показана часть', text)


if __name__ == '__main__': unittest.main()

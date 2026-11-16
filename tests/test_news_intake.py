import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')
try:
    from handlers import news_intake
except ImportError:
    news_intake = None


@unittest.skipIf(news_intake is None, 'aiogram and motor are optional for isolated tests')
class NewsIntakeTests(unittest.IsolatedAsyncioTestCase):
    def msg(self, text='/news https://example.com/a\nАкцент: важность', user=22, chat='private', reply=None):
        return SimpleNamespace(from_user=SimpleNamespace(id=user), chat=SimpleNamespace(type=chat),
                               message_id=45, text=text, reply_to_message=reply, answer=AsyncMock())

    def test_parse_text_link_and_accent(self):
        self.assertEqual(news_intake.parse_input('/news https://example.com/a\nАкцент: про курс', None),
                         ('', 'про курс', 'https://example.com/a'))
        self.assertEqual(news_intake.parse_input('/news Текст\nИсточник: https://example.com/a\nАкцент: важность', None),
                         ('Текст', 'важность', 'https://example.com/a'))
        self.assertEqual(news_intake.parse_input('/news важен срок', 'Пересланная новость'),
                         ('Пересланная новость', 'важен срок', ''))
        self.assertEqual(news_intake.parse_input('/news\nТекст с пробелом\nАкцент: сроки', None),
                         ('Текст с пробелом', 'сроки', ''))

    async def test_only_owner_in_private_saves_idempotently(self):
        collection = SimpleNamespace(update_one=AsyncMock(return_value=SimpleNamespace(upserted_id='id')))
        fake_db = SimpleNamespace(db=SimpleNamespace(news_candidates=collection))
        with patch.object(news_intake, 'db', fake_db), patch.object(news_intake, 'ADMIN_ID', 22):
            for message in (self.msg(user=23), self.msg(chat='group')):
                await news_intake.add_news(message)
                message.answer.assert_not_awaited()
            collection.update_one.assert_not_awaited()
            message = self.msg()
            await news_intake.add_news(message)
            query, update = collection.update_one.await_args.args
            self.assertEqual(query, {'_id': 'manual:22:45'})
            candidate = update['$setOnInsert']
            self.assertEqual(candidate['url'], 'https://example.com/a')
            self.assertEqual(candidate['accent'], 'важность')
            self.assertEqual(candidate['verification'], 'unverified')
            self.assertTrue(collection.update_one.await_args.kwargs['upsert'])
            self.assertIn('Публикации нет', message.answer.await_args.args[0])

    async def test_unknown_save_result_no_automatic_retry(self):
        collection = SimpleNamespace(update_one=AsyncMock(side_effect=RuntimeError('secret')))
        fake_db = SimpleNamespace(db=SimpleNamespace(news_candidates=collection))
        with patch.object(news_intake, 'db', fake_db), patch.object(news_intake, 'ADMIN_ID', 22):
            message = self.msg()
            await news_intake.add_news(message)
            self.assertEqual(collection.update_one.await_count, 1)
            self.assertIn('/news_list', message.answer.await_args.args[0])
            self.assertNotIn('secret', message.answer.await_args.args[0])

    async def test_digest_requires_owner_private_and_enabled_queue(self):
        with patch.object(news_intake, 'ADMIN_ID', 22), patch.dict(os.environ, {'NEWS_RENDER_QUEUE_ENABLED':'0'}):
            for message in (self.msg('/news_digest a b', user=23), self.msg('/news_digest a b', chat='group')):
                await news_intake.create_digest(message)
                message.answer.assert_not_awaited()
            message = self.msg('/news_digest a b')
            await news_intake.create_digest(message)
            self.assertIn('выключена', message.answer.await_args.args[0])

    async def test_digest_submission_order_and_ambiguous_database_result(self):
        with patch.object(news_intake, 'ADMIN_ID', 22), patch.dict(os.environ, {'NEWS_RENDER_QUEUE_ENABLED':'1'}), \
             patch.object(news_intake, 'db', SimpleNamespace(db=SimpleNamespace(news_render_jobs='jobs'))), \
             patch('news_prototype.news_queue.enqueue_digest', new_callable=AsyncMock) as enqueue, \
             patch('news_prototype.news_queue.resolve_story_codes', new_callable=AsyncMock, return_value=['first','second']):
            enqueue.return_value = True
            message = self.msg('/news_digest first second')
            await news_intake.create_digest(message)
            enqueue.assert_awaited_once_with('jobs',22,45,['first','second'])
            self.assertIn('не будет опубликован',message.answer.await_args.args[0])
            enqueue.side_effect = RuntimeError('secret')
            message = self.msg('/news_digest first second')
            await news_intake.create_digest(message)
            self.assertIn('Не повторяй',message.answer.await_args.args[0])
            self.assertNotIn('secret',message.answer.await_args.args[0])

    async def test_list_is_owner_only(self):
        cursor = MagicMock(); cursor.sort.return_value = cursor; cursor.limit.return_value = cursor
        cursor.to_list = AsyncMock(return_value=[{'text': 'История', 'accent': 'Детали'}])
        collection = SimpleNamespace(find=MagicMock(return_value=cursor))
        with patch.object(news_intake, 'db', SimpleNamespace(db=SimpleNamespace(news_candidates=collection))), \
             patch.object(news_intake, 'ADMIN_ID', 22):
            await news_intake.list_news(self.msg(user=23))
            collection.find.assert_not_called()
            message = self.msg()
            await news_intake.list_news(message)
            collection.find.assert_called_once_with({'source_id': 'manual', 'owner_id': 22})
            self.assertIn('Детали', message.answer.await_args.args[0])
            self.assertIsNone(message.answer.await_args.kwargs['parse_mode'])


if __name__ == '__main__': unittest.main()

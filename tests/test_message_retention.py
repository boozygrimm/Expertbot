import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from database import Database, MESSAGE_RETENTION_SECONDS


class MessageRetentionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = Database.__new__(Database)
        self.db.messages = SimpleNamespace(index_information=AsyncMock(return_value={}), create_index=AsyncMock())
        self.db.db = SimpleNamespace(command=AsyncMock())

    async def test_creates_ttl_for_fresh_collection(self):
        await self.db.ensure_message_retention()
        self.db.messages.create_index.assert_awaited_once_with('createdAt', expireAfterSeconds=180 * 86400)
        self.db.db.command.assert_not_awaited()

    async def test_extends_existing_ttl_by_name_without_dropping_index(self):
        self.db.messages.index_information.return_value = {
            'existing_ttl': {'key': [('createdAt', 1)], 'expireAfterSeconds': 604800},
        }
        await self.db.ensure_message_retention()
        self.db.db.command.assert_awaited_once_with({'collMod': 'messages', 'index': {
            'name': 'existing_ttl', 'expireAfterSeconds': MESSAGE_RETENTION_SECONDS,
        }})
        self.db.messages.create_index.assert_not_awaited()

    async def test_same_ttl_is_noop(self):
        self.db.messages.index_information.return_value = {
            'createdAt_1': {'key': [('createdAt', 1)], 'expireAfterSeconds': MESSAGE_RETENTION_SECONDS},
        }
        await self.db.ensure_message_retention()
        self.db.db.command.assert_not_awaited()
        self.db.messages.create_index.assert_not_awaited()

    async def test_does_not_shorten_or_convert_unknown_indexes(self):
        cases = [
            {'one': {'key': [('createdAt', 1)], 'expireAfterSeconds': MESSAGE_RETENTION_SECONDS + 1}},
            {'one': {'key': [('createdAt', 1)]}},
            {'one': {'key': [('createdAt', 1)], 'expireAfterSeconds': 604800, 'sparse': True}},
            {'one': {'key': [('createdAt', 1)], 'expireAfterSeconds': 604800, 'partialFilterExpression': {'x': 1}}},
            {'one': {'key': [('createdAt', 1)], 'expireAfterSeconds': 604800},
             'two': {'key': [('createdAt', 1)], 'expireAfterSeconds': 604800}},
            {'one': {'key': [('createdAt', 1)], 'expireAfterSeconds': 'invalid'}},
        ]
        for indexes in cases:
            with self.subTest(indexes=indexes), self.assertLogs('database', level='WARNING'):
                self.db.messages.index_information.return_value = indexes
                await self.db.ensure_message_retention()
                self.db.messages.create_index.assert_not_awaited()
                self.db.db.command.assert_not_awaited()

    async def test_other_ttl_warns_even_if_createdat_migrates(self):
        self.db.messages.index_information.return_value = {
            'createdAt_1': {'key': [('createdAt', 1)], 'expireAfterSeconds': 604800},
            'other_1': {'key': [('other', 1)], 'expireAfterSeconds': 3600},
        }
        with self.assertLogs('database', level='WARNING') as logs:
            await self.db.ensure_message_retention()
        self.assertIn('Other message TTL indexes exist', logs.output[0])
        self.db.db.command.assert_awaited_once()

    async def test_permissions_or_connection_error_does_not_break_startup_or_log_error_contents(self):
        for location in ('index_information', 'command'):
            with self.subTest(location=location):
                self.setUp()
                if location == 'index_information':
                    self.db.messages.index_information.side_effect = OSError('secret')
                else:
                    self.db.messages.index_information.return_value = {
                        'createdAt_1': {'key': [('createdAt', 1)], 'expireAfterSeconds': 604800}}
                    self.db.db.command.side_effect = OSError('secret')
                with self.assertLogs('database', level='WARNING') as logs:
                    await self.db.ensure_message_retention()
                self.assertIn('previous TTL may remain active', logs.output[0])
                self.assertNotIn('secret', str(logs.output))

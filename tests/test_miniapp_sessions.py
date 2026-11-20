import asyncio
import unittest
from unittest.mock import patch
from urllib.parse import parse_qsl, urlencode

from aiohttp.test_utils import TestClient, TestServer
from miniapp_backend.app import create_session_app
from miniapp_backend.sessions import CapacityError, ReplayError, SessionStore
from miniapp_backend.telegram_stand import configuration_from_env, create_telegram_stand
from tests.test_miniapp_backend import FakePlayers, TOKEN, signed


class SessionStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_expiry_revocation_and_replay_with_normalized_query(self):
        now = [100.0]
        store = SessionStore(TOKEN, {42}, ttl=30, clock=lambda: now[0])
        data = signed()
        token, ttl = await store.exchange(data)
        self.assertEqual(ttl, 30)
        self.assertEqual(await store.resolve(token), 42)
        rearranged = urlencode(list(reversed(parse_qsl(data))))
        with self.assertRaises(ReplayError):
            await store.exchange(rearranged)
        await store.revoke(token)
        self.assertIsNone(await store.resolve(token))
        with self.assertRaises(ReplayError):
            await store.exchange(data)
        token, _ = await store.exchange(signed(extra={'query_id': 'a-different-session'}))
        now[0] += 31
        self.assertIsNone(await store.resolve(token))

    async def test_concurrent_replay_and_capacity_fail_closed(self):
        store = SessionStore(TOKEN, {42}, max_entries=1)
        data = signed()
        outcomes = await asyncio.gather(store.exchange(data), store.exchange(data), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, ReplayError) for result in outcomes), 1)
        with self.assertRaises(CapacityError):
            await store.exchange(signed(extra={'query_id': 'another'}))


class TelegramApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.players = FakePlayers({42: {'inventory': {'w': 7}, 'buildings': {'lab': 1}}})
        self.client = TestClient(TestServer(create_session_app(token=TOKEN, allowed_users={42, 44}, players=self.players)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def exchange(self, data):
        return await self.client.post('/api/session', json={'initData': data})

    async def test_session_reads_own_profile_and_can_be_revoked(self):
        response = await self.exchange(signed())
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        data = await response.json()
        self.assertEqual(data['expires_in'], 600)
        access_token = data['access_token']
        headers = {'Authorization': 'Bearer ' + access_token}
        result = await self.client.get('/api/alchemy/me?user_id=44', headers=headers)
        self.assertEqual(result.status, 200)
        self.assertEqual((await result.json())['profile']['inventory'][0]['quantity'], 7)
        self.assertEqual(self.players.calls[0][0], {'user_id': 42})
        self.assertEqual((await self.client.delete('/api/session', headers=headers)).status, 204)
        self.assertEqual((await self.client.get('/api/alchemy/me', headers=headers)).status, 401)

    async def test_replay_forbidden_bad_signatures_and_direct_initdata(self):
        data = signed()
        self.assertEqual((await self.exchange(data)).status, 200)
        self.assertEqual((await self.exchange(data)).status, 409)
        self.assertEqual((await self.exchange(signed(user_id=43))).status, 403)
        self.assertEqual((await self.exchange(signed(token='wrong'))).status, 401)
        self.assertEqual((await self.client.get('/api/alchemy/me', headers={'Authorization': 'tma ' + data})).status, 401)
        self.assertEqual(self.players.calls, [])

    async def test_body_and_routes_are_fail_closed(self):
        self.assertEqual((await self.client.post('/api/session', json={'initData': signed(), 'user_id': 42})).status, 400)
        self.assertEqual((await self.client.post('/api/session', data='bad')).status, 400)
        self.assertEqual((await self.client.get('/api/dev/session')).status, 404)
        self.assertEqual((await self.client.post('/api/dev/craft', json={})).status, 404)
        self.assertEqual((await self.client.post('/api/alchemy/me', json={})).status, 405)
        self.assertEqual((await self.client.get('/api/alchemy/me', headers={'Authorization': 'Bearer invalid'})).status, 401)
        self.assertEqual(self.players.calls, [])

    async def test_missing_profile(self):
        session = await (await self.exchange(signed(user_id=44))).json()
        response = await self.client.get('/api/alchemy/me', headers={'Authorization': 'Bearer ' + session['access_token']})
        self.assertEqual(response.status, 404)


class MockMongoEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def test_signed_login_and_projected_profile_leave_mock_database_unchanged(self):
        try:
            from mongomock_motor import AsyncMongoMockClient
        except ImportError:
            self.skipTest('mongomock-motor is not installed')
        collection = AsyncMongoMockClient().lunapark_test_demo.alchemy_players
        await collection.insert_one({'user_id': 42, 'inventory': {'w': 5}, 'discovered': ['steam'],
                                     'active_expeditions': [{'status': 'searching', 'planned_events': ['hidden']}],
                                     'private_data': 'not exposed'})
        before = await collection.find_one({'user_id': 42})
        client = TestClient(TestServer(create_telegram_stand(token=TOKEN, allowed_users={42}, players=collection)))
        await client.start_server()
        try:
            response = await client.post('/api/session', json={'initData': signed()})
            token = (await response.json())['access_token']
            response = await client.get('/api/alchemy/me', headers={'Authorization': 'Bearer ' + token})
            self.assertEqual(response.status, 200)
            payload = await response.json()
            self.assertEqual(payload['profile']['inventory'][0]['quantity'], 5)
            self.assertEqual(payload['profile']['expeditions'], {'searching': 1})
            self.assertNotIn('private_data', str(payload))
            self.assertNotIn('hidden', str(payload))
            self.assertEqual(before, await collection.find_one({'user_id': 42}))
        finally:
            await client.close()


class TelegramStandTests(unittest.IsolatedAsyncioTestCase):
    async def test_serves_telegram_sdk_and_no_sandbox_routes(self):
        client = TestClient(TestServer(create_telegram_stand(token=TOKEN, allowed_users={42}, players=FakePlayers())))
        await client.start_server()
        try:
            response = await client.get('/')
            html = await response.text()
            self.assertIn('https://telegram.org/js/telegram-web-app.js', html)
            self.assertIn('frame-ancestors', response.headers['Content-Security-Policy'])
            self.assertIn("'telegram'", await (await client.get('/config.js')).text())
            self.assertEqual((await client.get('/api/dev/session')).status, 404)
            self.assertEqual((await client.get('/.env')).status, 404)
            self.assertEqual((await client.get('/app.js')).status, 200)
        finally:
            await client.close()

    def test_environment_requires_explicit_test_configuration(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaises(ValueError):
                configuration_from_env()
        fields = {'LUNAPARK_STAND_CONFIRM': 'isolated_test_only', 'LUNAPARK_TEST_BOT_TOKEN': TOKEN,
                  'LUNAPARK_TEST_MONGO_URI': 'mongodb://localhost', 'LUNAPARK_TEST_DB': 'lunapark_test_demo',
                  'LUNAPARK_TEST_USERS': '42, 44'}
        with patch.dict('os.environ', fields, clear=True):
            self.assertEqual(configuration_from_env()[2:], ('lunapark_test_demo', {42, 44}))
        with patch.dict('os.environ', fields | {'LUNAPARK_TEST_DB': 'beleyakov_db'}, clear=True):
            with self.assertRaises(ValueError):
                configuration_from_env()
        with patch.dict('os.environ', fields | {'LUNAPARK_TEST_USERS': '42,no'}, clear=True):
            with self.assertRaises(ValueError):
                configuration_from_env()

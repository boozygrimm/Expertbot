import hashlib
import hmac
import json
import time
import unittest
from urllib.parse import urlencode

from aiohttp.test_utils import TestClient, TestServer

from miniapp_backend.app import create_app
from miniapp_backend.auth import InvalidInitData, verify_init_data
from miniapp_backend.profile import PROJECTION, build_profile

TOKEN = '123456:TEST-TOKEN'  # Deliberately fake; never use a real bot token in tests.


def signed(user_id=42, *, date=None, token=TOKEN, extra=None):
    fields = {'auth_date': str(int(time.time()) if date is None else date),
              'user': json.dumps({'id': user_id, 'first_name': 'Алекс'}, ensure_ascii=False, separators=(',', ':'))}
    fields.update(extra or {})
    data = '\n'.join(f'{key}={value}' for key, value in sorted(fields.items()))
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    fields['hash'] = hmac.new(secret, data.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class AuthenticationTests(unittest.TestCase):
    def test_independent_openssl_vector(self):
        fields = {'auth_date': '1700000000', 'query_id': 'query123',
                  'user': '{"id":42,"first_name":"Alex"}',
                  'hash': '3d8df3ee7a5c944bdc8c3dc6ba05047378ab232599212e2a5ee928ec8cea7e7b'}
        self.assertEqual(verify_init_data(urlencode(fields), TOKEN, now=1700000000), 42)

    def test_signed_data_and_signature_field(self):
        self.assertEqual(verify_init_data(signed(extra={'signature': 'abc-DEF_123'}), TOKEN), 42)

    def test_invalid_inputs_are_rejected(self):
        now = int(time.time())
        samples = [
            '', 'x=y', signed(token='wrong'), signed(date=now-901), signed(date=now+31),
            signed(user_id=True), signed(user_id=0), signed(user_id='42'), signed(user_id=-1),
            signed() + '&user=%7B%22id%22%3A43%7D', signed() + '&hash=' + '0'*64,
            signed(extra={'query_id': 'x\ny'}), 'x=' + 'a'*9000,
        ]
        for sample in samples:
            with self.subTest(sample=sample[:50]):
                with self.assertRaises(InvalidInitData):
                    verify_init_data(sample, TOKEN, now=now)
        with self.assertRaises(InvalidInitData):
            verify_init_data(signed(), '')


class FakePlayers:
    def __init__(self, profiles=None, fail=False):
        self.profiles = profiles or {}
        self.calls = []
        self.fail = fail

    async def find_one(self, query, projection):
        self.calls.append((query, projection))
        if self.fail:
            raise RuntimeError('fake secret must not appear')
        return self.profiles.get(query['user_id'])


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.players = FakePlayers({42: {
            'user_id': 42, 'user_name': 'Private name', 'secret': 'internal secret',
            'inventory': {'w': 4, 'f': 3, 'base_crystal': 1, 'empty': 0},
            'buildings': {'lab': 2, 'storage': 1}, 'discovered': ['steam'],
            'active_expeditions': [{'status': 'searching', 'planned_events': ['secret surprise']}],
        }})
        self.client = TestClient(TestServer(create_app(token=TOKEN, allowed_users={42, 44}, players=self.players)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def get(self, raw=None, suffix=''):
        headers = {} if raw is None else {'Authorization': 'tma ' + raw}
        return await self.client.get('/api/alchemy/me' + suffix, headers=headers)

    async def test_reads_only_verified_user_and_projects_safe_fields(self):
        response = await self.get(signed(), '?user_id=999')
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        data = await response.json()
        self.assertEqual(self.players.calls, [({'user_id': 42}, PROJECTION)])
        self.assertEqual(data['profile']['buildings']['lab'], 2)
        self.assertEqual(data['profile']['discovered_count'], 1)
        self.assertEqual(data['profile']['expeditions'], {'searching': 1})
        self.assertEqual({item['id'] for item in data['profile']['inventory']}, {'w', 'f'})
        self.assertNotIn('secret', str(data))
        self.assertNotIn('Private name', str(data))
        self.assertNotIn('user_id', str(data))

    async def test_unauthorized_and_forbidden_never_query_database(self):
        for auth, status in [(None, 401), (signed(token='bad'), 401), (signed(user_id=43), 403)]:
            response = await self.get(auth)
            self.assertEqual(response.status, status)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.players.calls, [])

    async def test_missing_profile_and_db_failure_do_not_create_profile_or_leak(self):
        self.assertEqual((await self.get(signed(user_id=44))).status, 404)
        self.players.fail = True
        response = await self.get(signed())
        self.assertEqual(response.status, 503)
        self.assertNotIn('secret', await response.text())

    async def test_no_write_method(self):
        response = await self.client.post('/api/alchemy/me', headers={'Authorization': 'tma ' + signed()})
        self.assertEqual(response.status, 405)
        self.assertEqual(self.players.calls, [])
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    async def test_no_cors(self):
        response = await self.client.get('/api/alchemy/me', headers={'Origin': 'https://other.invalid', 'Authorization': 'tma ' + signed()})
        self.assertNotIn('Access-Control-Allow-Origin', response.headers)


class MongoProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_nested_projection_on_mock_motor(self):
        try:
            from mongomock_motor import AsyncMongoMockClient
        except ImportError:
            self.skipTest('mongomock-motor is not installed')
        collection = AsyncMongoMockClient().test.alchemy_players
        await collection.insert_one({'user_id': 42, 'inventory': {'w': 4},
                                     'active_expeditions': [{'status': 'searching', 'planned_events': ['hidden']}]})
        player = await collection.find_one({'user_id': 42}, PROJECTION)
        self.assertEqual(player['active_expeditions'], [{'status': 'searching'}])
        self.assertEqual(build_profile(player)['expeditions'], {'searching': 1})


class ProfileTests(unittest.TestCase):
    def test_missing_and_malformed_values(self):
        profile = build_profile({'inventory': {'w': -1, 'a': '4', 'e': True, 'f': 2},
                                 'buildings': None, 'discovered': None,
                                 'active_expeditions': [None, {'status': {'bad': 'type'}}, {'status': 'arrived'}]})
        self.assertEqual([item['id'] for item in profile['inventory']], ['f'])
        self.assertEqual(profile['buildings']['lab'], 0)
        self.assertEqual(profile['discovered_count'], 0)
        self.assertEqual(profile['expeditions'], {'unknown': 1, 'arrived': 1})

    def test_known_recipes_match_inventory_and_discoveries_without_leaking_unknown(self):
        profile = build_profile({'inventory': {'steam': 0, 'w': 1}, 'discovered': ['lava']})
        results = {recipe['result'] for recipe in profile['recipes']}
        self.assertIn('☁️ Пар', results)
        self.assertIn('🌋 Лава', results)
        self.assertNotIn('🧬 Жизнь', results)

    def test_statuses_fighters_and_stations_are_sanitized(self):
        profile = build_profile({
            'production_slots': [{'type': 'mine', 'worker': 'spirit_e', 'stopped': False, 'total_mined': 4, 'secret': 'no'}],
            'fighters': [{'unit_id': 'fire_swordsman', 'custom_name': 'Искра', 'status': 'available', 'tier': 1, 'xp': 2, 'stamina': 3, 'history': ['private']}],
            'active_expeditions': [{'status': 'returning', 'planned_events': ['secret']}, {'status': 'other'}],
        })
        self.assertEqual(profile['production'][0]['name'], '⛏ Шахта')
        self.assertEqual(profile['fighters'][0]['name'], 'Искра')
        self.assertEqual(profile['fighters'][0]['unit_id'], 'fire_swordsman')
        unknown = build_profile({'fighters': [{'unit_id': 'private-unexpected-value'}]})
        self.assertIsNone(unknown['fighters'][0]['unit_id'])
        self.assertEqual(profile['expeditions'], {'returning': 1, 'unknown': 1})
        self.assertNotIn('secret', str(profile))
        self.assertNotIn('history', str(profile))

    def test_configuration_fails_closed(self):
        for token, users in [('', {42}), (TOKEN, set()), (TOKEN, {0}), (TOKEN, {True})]:
            with self.assertRaises(ValueError):
                create_app(token=token, allowed_users=users, players=FakePlayers())


if __name__ == '__main__':
    unittest.main()

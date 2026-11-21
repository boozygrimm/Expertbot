import asyncio
import unittest
from uuid import uuid4

from aiohttp.test_utils import TestClient, TestServer
from handlers.alc_recipes import ELEMENTS, RECIPES, is_battle_unit
from miniapp_backend.auth import InvalidInitData, verify_init_data
from miniapp_backend.sandbox import SANDBOX_TOKEN, SandboxPlayers, create_sandbox


class SandboxCraftTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = TestClient(TestServer(create_sandbox()))
        await self.client.start_server()
        self.init_data = (await (await self.client.get('/api/dev/session')).json())['initData']
        self.headers = {'Authorization': 'tma ' + self.init_data}

    async def asyncTearDown(self):
        await self.client.close()

    async def profile(self):
        response = await self.client.get('/api/alchemy/me', headers=self.headers)
        self.assertEqual(response.status, 200)
        return (await response.json())['profile']

    async def craft(self, ingredients, request_id=None, headers=None):
        return await self.client.post('/api/dev/craft', json={'ingredients': ingredients, 'request_id': request_id or str(uuid4())}, headers=self.headers if headers is None else headers)

    async def test_signed_sandbox_profile_and_idempotent_craft(self):
        before = await self.profile()
        before_items = {item['id']: item['quantity'] for item in before['inventory']}
        key = str(uuid4())
        first = await self.craft(['w', 'f'], key)
        self.assertEqual(first.status, 200)
        data = await first.json()
        self.assertEqual(data['outcome'], 'crafted')
        self.assertEqual(data['result'], '☁️ Пар')
        second = await self.craft(['w', 'f'], key)
        self.assertEqual(await second.json(), data)
        after_items = {item['id']: item['quantity'] for item in (await self.profile())['inventory']}
        self.assertEqual(after_items['w'], before_items['w'] - 1)
        self.assertEqual(after_items['f'], before_items['f'] - 1)
        self.assertEqual(after_items['steam'], before_items['steam'] + 1)
        self.assertEqual((await self.craft(['e', 'f'], key)).status, 409)
        with self.assertRaises(InvalidInitData):
            verify_init_data(self.init_data, '123456:REAL-IS-NOT-THIS-TOKEN')

    async def test_bad_requests_and_unauthorized_do_not_write(self):
        before = await self.profile()
        self.assertEqual((await self.craft(['w', 'f'], headers={})).status, 401)
        self.assertEqual((await self.craft(['base_crystal', 'w'])).status, 400)
        self.assertEqual((await self.craft(['$inc', 'w'])).status, 400)
        self.assertEqual((await self.craft(['w', 'w'], request_id='not-an-id')).status, 400)
        self.assertEqual(before, await self.profile())

    async def test_unknown_recipe_keeps_ingredients(self):
        pair = next((a, b) for a in ['w', 'f', 'e', 'a', 'stone', 'moss']
                    for b in ['w', 'f', 'e', 'a', 'stone', 'moss'] if tuple(sorted((a, b))) not in RECIPES)
        before = await self.profile()
        result = await self.craft(list(pair))
        self.assertEqual(result.status, 200)
        self.assertEqual((await result.json())['outcome'], 'no_recipe')
        self.assertEqual(before['inventory'], (await self.profile())['inventory'])

    async def test_sandbox_is_isolated_and_not_a_path_to_other_files(self):
        for path in ['/api/alchemy/me?user_id=1', '/README.md', '/assets/../../config.py']:
            if path.startswith('/api/'):
                response = await self.client.get(path, headers=self.headers)
                self.assertEqual(response.status, 200)
            else:
                self.assertEqual((await self.client.get(path)).status, 404)
        self.assertEqual((await self.client.post('/api/alchemy/me', json={})).status, 405)


class SandboxConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_crafts_cannot_spend_the_same_resource_twice(self):
        store = SandboxPlayers()
        store.player['inventory'] = {'w': 1, 'f': 1}
        left, right = await asyncio.gather(store.craft(['w', 'f'], str(uuid4())), store.craft(['w', 'f'], str(uuid4())))
        self.assertEqual(sorted([left[1], right[1]]), [200, 409])
        self.assertEqual(store.player['inventory']['w'], 0)
        self.assertEqual(store.player['inventory']['f'], 0)
        self.assertEqual(store.player['inventory']['steam'], 1)

    async def test_duplicate_simultaneous_request_executes_only_once(self):
        store = SandboxPlayers()
        store.player['inventory'] = {'w': 2, 'f': 2}
        key = str(uuid4())
        left, right = await asyncio.gather(store.craft(['w', 'f'], key), store.craft(['w', 'f'], key))
        self.assertEqual(left, right)
        self.assertEqual(store.player['inventory']['w'], 1)
        self.assertEqual(store.player['inventory']['f'], 1)
        self.assertEqual(store.player['inventory']['steam'], 1)

    async def test_same_item_and_battle_recipe(self):
        store = SandboxPlayers()
        pair = next(pair for pair, result in RECIPES.items() if pair[0] == pair[1] and not is_battle_unit(result))
        store.player['inventory'] = {pair[0]: 1}
        response, status = await store.craft(list(pair), str(uuid4()))
        self.assertEqual(status, 409)
        self.assertEqual(response['error'], 'insufficient_resources')
        pair = next(pair for pair, result in RECIPES.items() if is_battle_unit(result))
        store.player['inventory'] = {item: 2 for item in pair}
        before = dict(store.player['inventory'])
        response, status = await store.craft(list(pair), str(uuid4()))
        self.assertEqual((status, response['error']), (409, 'fighter_recipe'))
        self.assertEqual(store.player['inventory'], before)

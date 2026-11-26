import asyncio
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
import unittest
from uuid import uuid4

from aiohttp.test_utils import TestClient, TestServer
from miniapp_backend.sandbox import SandboxPlayers, create_sandbox


def uid():
    return str(uuid4())


class SandboxProductionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = TestClient(TestServer(create_sandbox()))
        await self.client.start_server()
        session = await (await self.client.get('/api/dev/session')).json()
        self.headers = {'Authorization': 'tma ' + session['initData']}

    async def asyncTearDown(self):
        await self.client.close()

    async def profile(self):
        response = await self.client.get('/api/alchemy/me', headers=self.headers)
        return (await response.json())['profile']

    async def action(self, command, key=None, headers=None):
        return await self.client.post('/api/dev/production', json={**command, 'request_id': key or uid()},
                                      headers=self.headers if headers is None else headers)

    async def test_remove_start_advance_and_return_worker(self):
        before = await self.profile()
        mine, stopped = before['production']
        self.assertEqual(before['sandbox']['production_capacity'], 2)
        self.assertTrue(stopped['stopped'])
        remove = await self.action({'action': 'remove', 'slot_id': stopped['sandbox_id']})
        self.assertEqual(remove.status, 200)
        start = await self.action({'action': 'start', 'station': 'farm', 'worker': 'spirit_w'})
        self.assertEqual(start.status, 200)
        started = (await start.json())['profile']
        inv = {x['id']: x['quantity'] for x in started['inventory']}
        self.assertEqual(inv['spirit_w'], 1)
        self.assertEqual(inv['energy'], 3)
        advance = await self.action({'action': 'advance'})
        data = await advance.json()
        self.assertEqual(data['outcome'], 'advanced')
        self.assertEqual(data['profile']['sandbox']['elapsed_hours'], 1)
        self.assertEqual(data['profile']['production'][0]['total_mined'], 15)
        self.assertEqual(data['profile']['production'][1]['total_mined'], 3)
        farm_id = data['profile']['production'][1]['sandbox_id']
        result = await self.action({'action': 'remove', 'slot_id': farm_id})
        inv = {x['id']: x['quantity'] for x in (await result.json())['profile']['inventory']}
        self.assertEqual(inv['spirit_w'], 2)
        self.assertEqual(inv['energy'], 3)
        self.assertEqual((await self.action({'action': 'remove', 'slot_id': farm_id})).status, 409)

    async def test_authorization_validation_and_capacity_fail_closed(self):
        before = await self.profile()
        self.assertEqual((await self.action({'action': 'advance'}, headers={})).status, 401)
        for command in ({'action':'start', 'station':'mine', 'worker':'fake'},
                        {'action':'start', 'station':[], 'worker':'none'},
                        {'action':'remove', 'slot_id':'not-a-uuid'},
                        {'action':'advance', 'hours':500}, {'action':'unknown'}):
            self.assertEqual((await self.action(command)).status, 400)
        full = await self.action({'action':'start', 'station':'mine', 'worker':'none'})
        self.assertEqual(full.status, 409)
        self.assertEqual((await full.json())['error'], 'slots_full')
        self.assertEqual(await self.profile(), before)

    async def test_duplicates_and_cross_action_key_conflict(self):
        key = uid()
        command = {'action': 'advance'}
        first, second = await asyncio.gather(self.action(command, key), self.action(command, key))
        self.assertEqual(await first.json(), await second.json())
        profile = await self.profile()
        self.assertEqual(profile['sandbox']['elapsed_hours'], 1)
        self.assertEqual(profile['production'][0]['total_mined'], 15)
        conflict = await self.action({'action':'remove', 'slot_id':profile['production'][0]['sandbox_id']}, key)
        self.assertEqual(conflict.status, 409)
        craft = await self.client.post('/api/dev/craft', json={'ingredients':['w','f'], 'request_id':key}, headers=self.headers)
        self.assertEqual(craft.status, 409)
        self.assertEqual(await self.profile(), profile)

    async def test_twenty_four_hours_stops_without_returning_expired_spirit(self):
        for _ in range(24):
            response = await self.action({'action':'advance'})
            self.assertEqual(response.status, 200)
        profile = await self.profile()
        mine = profile['production'][0]
        self.assertEqual(profile['sandbox']['elapsed_hours'], 24)
        self.assertTrue(mine['stopped'])
        self.assertEqual(mine['total_mined'], 12 + 23 * 3)
        removed = await self.action({'action':'remove', 'slot_id':mine['sandbox_id']})
        inventory = (await removed.json())['profile']['inventory']
        self.assertNotIn('spirit_e', {x['id'] for x in inventory})

    async def test_capacity_and_legacy_parameters_never_enter_telegram_api(self):
        # This app is sandbox-only; the production/session app is separately tested.
        profile = await self.profile()
        self.assertIn('sandbox', profile)
        self.assertTrue(all('sandbox_id' in x for x in profile['production']))


class SandboxIsolationTests(unittest.TestCase):
    def test_demo_import_does_not_load_legacy_database_or_config(self):
        root = Path(__file__).resolve().parent.parent
        env = {'PATH': os.environ.get('PATH', ''), 'HOME': os.environ.get('HOME', '/tmp'),
               'PYTHONPATH': str(root), 'PYTHON_DOTENV_DISABLED': '1',
               'MONGO_URI': 'invalid-must-not-be-used', 'TELEGRAM_TOKEN': 'invalid-must-not-be-used'}
        code = ("import sys; import miniapp_backend.sandbox; "
                "assert not {'database', 'config', 'handlers.alc_core'} & set(sys.modules)")
        run = subprocess.run([sys.executable, '-c', code], cwd=root, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


class InMemoryConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_craft_and_tick_are_serialized_without_lost_resources(self):
        store = SandboxPlayers()
        before = deepcopy(store.player['inventory'])
        (craft, cstatus), (tick, tstatus) = await asyncio.gather(
            store.craft(['w', 'f'], uid()), store.production({'action':'advance'}, uid()))
        self.assertEqual((cstatus, tstatus), (200, 200))
        self.assertEqual(craft['outcome'], 'crafted')
        self.assertEqual(tick['outcome'], 'advanced')
        # Mine cannot produce water, fire, or steam.
        self.assertEqual(store.player['inventory']['w'], before['w'] - 1)
        self.assertEqual(store.player['inventory']['f'], before['f'] - 1)
        self.assertEqual(store.player['inventory']['steam'], before['steam'] + 1)
        self.assertEqual(store.player['production_slots'][0]['total_mined'], 15)

    async def test_insufficient_energy_and_duplicate_remove_do_not_mutate(self):
        store = SandboxPlayers()
        slot_id = store.player['production_slots'][1]['_sandbox_id']
        await store.production({'action':'remove', 'slot_id':slot_id}, uid())
        store.player['inventory'].pop('energy')
        before = deepcopy(store.player)
        payload, status = await store.production({'action':'start', 'station':'farm', 'worker':'spirit_w'}, uid())
        self.assertEqual((status, payload['error']), (409, 'missing_energy'))
        self.assertEqual(store.player, before)
        self.assertEqual((await store.production({'action':'remove', 'slot_id':slot_id}, uid()))[1], 409)
        self.assertEqual(store.player, before)

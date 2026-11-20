"""Offline CAS/production tests. Launched in a fake, scrubbed environment."""
import asyncio
from copy import deepcopy
import unittest
from unittest.mock import patch
from uuid import uuid4

import mongomock
from miniapp_backend.isolated_store import IsolatedProfileStore, OperationKeyConflict, OutcomeUnknown
from miniapp_backend.isolated_production import production_operation_id, run_isolated_production
from miniapp_backend import isolated_production
from tests.test_miniapp_isolated_store import AsyncMockCollection


def uid():
    return str(uuid4())


class ConflictOnce(AsyncMockCollection):
    def __init__(self, collection):
        super().__init__(collection)
        self.conflict = True

    async def update_one(self, query, update):
        if self.conflict:
            self.conflict = False
            # Simulate another compliant writer committing between read and CAS.
            self.raw.update_one({'user_id': 42}, {'$inc': {'_alc_rev': 1, 'inventory.w': 1}})
        return await super().update_one(query, update)


class ProductionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.raw = mongomock.MongoClient().lunapark_test_unit.alchemy_players
        self.player = {'user_id': 42, 'inventory': {'w': 20, 'f': 20}, 'discovered': [],
                       'buildings': {}, 'production_slots': [{'type': 'mine', 'assigned_at': 99900,
                                                               'worker': 'spirit_e', 'total_mined': 0}],
                       'private': 'keep'}
        self.raw.insert_one(deepcopy(self.player))
        self.collection = AsyncMockCollection(self.raw)
        self.store = IsolatedProfileStore(self.collection, isolated_test_only=True)
        self.run_id = uid()

    async def tick(self, store=None, **kwargs):
        return await run_isolated_production(store or self.store, user_id=42,
                                             run_id=kwargs.get('run_id', self.run_id),
                                             tick_time=kwargs.get('tick_time', 100000))

    async def test_parallel_tick_and_crafts_preserve_both(self):
        expected_raw = mongomock.MongoClient().lunapark_test_unit.alchemy_players
        expected_raw.insert_one(deepcopy(self.player))
        expected_store = IsolatedProfileStore(AsyncMockCollection(expected_raw), isolated_test_only=True)
        craft_ids = [uid() for _ in range(4)]
        await self.tick(expected_store)
        for key in craft_ids:
            await expected_store.craft(42, key, ['w', 'f'])
        results = await asyncio.gather(self.tick(), *(self.store.craft(42, key, ['w', 'f']) for key in craft_ids))
        self.assertEqual(results[0], {'outcome': 'produced'})
        doc = self.raw.find_one({'user_id': 42})
        expected = expected_raw.find_one({'user_id': 42})
        for field in ('inventory', 'discovered', 'last_craft', 'production_slots'):
            self.assertEqual(doc[field], expected[field])
        self.assertEqual(doc['_alc_rev'], 5)
        self.assertEqual(len(doc['_alc_ops']), 5)
        self.assertEqual(doc['private'], 'keep')

    async def test_conflict_restarts_random_stream_with_fresh_state(self):
        collection = ConflictOnce(self.raw)
        store = IsolatedProfileStore(collection, isolated_test_only=True)
        seen = []
        original = isolated_production.calculate_production_loot
        def record(player, now, *, rng):
            seen.append((player['inventory']['w'], rng.getstate()))
            return original(player, now, rng=rng)
        with patch.object(isolated_production, 'calculate_production_loot', side_effect=record):
            await self.tick(store)
        self.assertEqual(len(seen), 2)
        self.assertEqual([x[0] for x in seen], [20, 21])
        self.assertEqual(seen[0][1], seen[1][1])
        doc = self.raw.find_one({'user_id': 42})
        self.assertEqual(doc['inventory']['w'], 21)
        self.assertEqual(doc['_alc_rev'], 2)
        self.assertEqual(doc['production_slots'][0]['total_mined'], 3)

    async def test_parallel_duplicates_and_recreation_do_not_repeat(self):
        result = await asyncio.gather(*(self.tick() for _ in range(8)))
        self.assertTrue(all(x == result[0] for x in result))
        fresh = IsolatedProfileStore(AsyncMockCollection(self.raw), isolated_test_only=True)
        self.assertEqual(await self.tick(fresh), result[0])
        doc = self.raw.find_one({'user_id': 42})
        self.assertEqual(doc['_alc_rev'], 1)
        self.assertEqual(doc['production_slots'][0]['total_mined'], 3)
        with self.assertRaises(OperationKeyConflict):
            await self.tick(fresh, tick_time=100001)

    async def test_unknown_outcome_and_same_run_retry(self):
        self.collection.after_commit_error = True
        with self.assertRaises(OutcomeUnknown):
            await self.tick()
        self.assertEqual(await self.tick(), {'outcome': 'produced'})
        self.assertEqual(self.raw.find_one({'user_id': 42})['production_slots'][0]['total_mined'], 3)
        self.assertEqual(len(self.raw.find_one({'user_id': 42})['_alc_ops']), 1)

    async def test_noop_is_recorded_and_not_reinterpreted_later(self):
        self.raw.update_one({'user_id': 42}, {'$set': {'production_slots.0.stopped': True}})
        self.assertEqual(await self.tick(), {'outcome': 'unchanged'})
        self.raw.update_one({'user_id': 42}, {'$set': {'production_slots.0.stopped': False}, '$inc': {'_alc_rev': 1}})
        self.assertEqual(await self.tick(), {'outcome': 'unchanged'})
        self.assertNotIn('mined_items', self.raw.find_one({'user_id': 42})['production_slots'][0])

    async def test_identity_and_validation(self):
        self.assertEqual(production_operation_id(self.run_id, 42), production_operation_id(self.run_id, 42))
        self.assertNotEqual(production_operation_id(self.run_id, 42), production_operation_id(self.run_id, 43))
        with self.assertRaises(ValueError):
            await self.tick(run_id='not-a-uuid')
        with self.assertRaises(ValueError):
            await self.tick(tick_time=True)
        self.assertNotIn('_alc_rev', self.raw.find_one({'user_id': 42}))


if __name__ == '__main__':
    unittest.main()

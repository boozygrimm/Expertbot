import asyncio
from copy import deepcopy
import unittest
from uuid import uuid4

import mongomock
from miniapp_backend.isolated_store import (
    Change, ConcurrentUpdate, InvalidStoreState, IsolatedProfileStore, OperationKeyConflict,
    OperationLogFull, OutcomeUnknown, ReadUnavailable, MAX_OPERATIONS, _fingerprint,
)


def uid():
    return str(uuid4())


class AsyncMockCollection:
    """Test-only stand-in. with_options does not emulate real Mongo durability."""
    def __init__(self, collection):
        self.raw = collection
        self.database = collection.database
        self.name = collection.name
        self.after_commit_error = False
        self.fail_read = False
        self.fail_before_write = False
        self.always_conflict = False
        self.options = None

    def with_options(self, **kwargs):
        self.options = kwargs
        return self

    async def find_one(self, query):
        await asyncio.sleep(0)  # Force interleavings between reads and writes.
        if self.fail_read:
            raise RuntimeError('private connection data')
        return self.raw.find_one(query)

    async def update_one(self, query, update):
        await asyncio.sleep(0)
        if self.fail_before_write:
            raise RuntimeError('connection lost before write')
        if self.always_conflict:
            return type('Result', (), {'matched_count': 0})()
        result = self.raw.update_one(query, update)
        if self.after_commit_error:
            self.after_commit_error = False
            raise RuntimeError('connection lost after write')
        return result


class IsolatedStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.raw = mongomock.MongoClient().lunapark_test_unit.alchemy_players
        self.raw.insert_one({'user_id': 42, 'inventory': {'w': 20, 'f': 20},
                             'discovered': [], 'production_slots': [], 'private': 'keep'})
        self.collection = AsyncMockCollection(self.raw)
        self.store = IsolatedProfileStore(self.collection, isolated_test_only=True)

    async def test_legacy_document_and_retry_survive_store_recreation(self):
        key = uid()
        first = await self.store.craft(42, key, ['w', 'f'])
        self.assertEqual(first, {'outcome': 'crafted', 'result_id': 'steam'})
        fresh_store = IsolatedProfileStore(AsyncMockCollection(self.raw), isolated_test_only=True)
        self.assertEqual(await fresh_store.craft(42, key, ['w', 'f']), first)
        doc = self.raw.find_one({'user_id': 42})
        self.assertEqual(doc['_alc_rev'], 1)
        self.assertEqual(doc['inventory'], {'w': 19, 'f': 19, 'steam': 1})
        self.assertEqual(doc['private'], 'keep')
        self.assertEqual(len(doc['_alc_ops']), 1)
        with self.assertRaises(OperationKeyConflict):
            await fresh_store.craft(42, key, ['f', 'w'])
        self.assertEqual(self.raw.find_one({'user_id': 42})['_alc_rev'], 1)

    async def test_simultaneous_identical_requests_apply_once(self):
        key = uid()
        results = await asyncio.gather(*(self.store.craft(42, key, ['w', 'f']) for _ in range(8)))
        self.assertTrue(all(item == results[0] for item in results))
        doc = self.raw.find_one({'user_id': 42})
        self.assertEqual(doc['inventory'], {'w': 19, 'f': 19, 'steam': 1})
        self.assertEqual(len(doc['_alc_ops']), 1)

    async def test_craft_and_example_production_writer_do_not_lose_updates(self):
        # This simulates an adopted background writer; it is NOT the real alc_tasks.
        def production(player):
            inventory = dict(player['inventory'])
            inventory['w'] += 2
            return Change({'inventory': inventory, 'production_slots': [{'type': 'mine', 'total_mined': 2}]},
                          {'outcome': 'produced'})
        peer = IsolatedProfileStore(AsyncMockCollection(self.raw), isolated_test_only=True)
        tasks = [self.store.craft(42, uid(), ['w', 'f']) for _ in range(4)]
        tasks += [peer.execute(42, uid(), {'kind': 'test_production', 'index': index}, production) for index in range(4)]
        results = await asyncio.gather(*tasks)
        self.assertEqual(sum(item['outcome'] == 'crafted' for item in results), 4)
        doc = self.raw.find_one({'user_id': 42})
        self.assertEqual(doc['inventory'], {'w': 24, 'f': 16, 'steam': 4})
        self.assertEqual(doc['_alc_rev'], 8)
        self.assertEqual(len(doc['_alc_ops']), 8)
        self.assertEqual(doc['private'], 'keep')

    async def test_failure_response_is_durable_and_does_not_turn_into_later_craft(self):
        self.raw.update_one({'user_id': 42}, {'$set': {'inventory': {'w': 0, 'f': 1}}})
        key = uid()
        expected = {'outcome': 'rejected', 'reason': 'insufficient_resources'}
        self.assertEqual(await self.store.craft(42, key, ['w', 'f']), expected)
        def credit(player):
            inventory = dict(player['inventory'])
            inventory['w'] += 5
            return Change({'inventory': inventory}, {'outcome': 'credited'})
        await self.store.execute(42, uid(), {'kind': 'test_credit'}, credit)
        self.assertEqual(await self.store.craft(42, key, ['w', 'f']), expected)
        self.assertEqual(self.raw.find_one({'user_id': 42})['inventory'], {'w': 5, 'f': 1})

    async def test_uncertain_write_outcome_requires_the_same_key(self):
        key = uid()
        self.collection.after_commit_error = True
        with self.assertRaises(OutcomeUnknown) as exc:
            await self.store.craft(42, key, ['w', 'f'])
        self.assertNotIn('connection', str(exc.exception))
        result = await self.store.craft(42, key, ['w', 'f'])
        self.assertEqual(result['outcome'], 'crafted')
        doc = self.raw.find_one({'user_id': 42})
        self.assertEqual(doc['inventory']['steam'], 1)
        self.assertEqual(len(doc['_alc_ops']), 1)

    async def test_write_failure_before_commit_safe_to_retry_same_key(self):
        key = uid()
        self.collection.fail_before_write = True
        with self.assertRaises(OutcomeUnknown):
            await self.store.craft(42, key, ['w', 'f'])
        self.collection.fail_before_write = False
        self.assertEqual((await self.store.craft(42, key, ['w', 'f']))['outcome'], 'crafted')
        self.assertEqual(self.raw.find_one({'user_id': 42})['inventory']['steam'], 1)

    async def test_log_window_keeps_replays_and_slides_oldest_out(self):
        key = uid()
        command = {'kind': 'craft_single_v1', 'ingredients': ['w', 'f']}
        entries = [{'key': uid(), 'fingerprint': 'x', 'result': {'outcome': 'done'}} for _ in range(MAX_OPERATIONS - 1)]
        entries.append({'key': key, 'fingerprint': _fingerprint(command), 'result': {'outcome': 'crafted', 'result_id': 'steam'}})
        self.raw.update_one({'user_id': 42}, {'$set': {'_alc_rev': 10, '_alc_ops': entries}})
        # A stored result inside the window is replayed, not recomputed.
        self.assertEqual((await self.store.craft(42, key, ['w', 'f']))['result_id'], 'steam')
        # A new operation slides the window instead of blocking all later writes.
        self.assertEqual((await self.store.craft(42, uid(), ['w', 'f']))['outcome'], 'crafted')
        operations = self.raw.find_one({'user_id': 42})['_alc_ops']
        self.assertEqual(len(operations), MAX_OPERATIONS)
        # The oldest entry left the window; the newest one is there.
        self.assertNotIn(entries[0]['key'], {entry['key'] for entry in operations})
        self.assertIn(key, {entry['key'] for entry in operations})

    async def test_hundred_operations_never_block_and_document_stays_bounded(self):
        """The owner's choice: window of the last 100 operations, never a hard stop."""
        for index in range(MAX_OPERATIONS + 40):
            result = await self.store.craft(42, uid(), ['w', 'f'])
            self.assertEqual(result['outcome'], 'crafted')
            # Keep the inventory stocked so the rejections never appear.
            if index % 10 == 0:
                inventory = dict(self.raw.find_one({'user_id': 42})['inventory'])
                inventory['w'] = inventory.get('w', 0) + 20
                inventory['f'] = inventory.get('f', 0) + 20
                self.raw.update_one({'user_id': 42}, {'$set': {'inventory': inventory}})
        operations = self.raw.find_one({'user_id': 42})['_alc_ops']
        self.assertLessEqual(len(operations), MAX_OPERATIONS)

    async def test_production_mode_accepts_only_the_lunapark_collection(self):
        allowed = AsyncMockCollection(mongomock.MongoClient().bender.lunapark_players)
        store = IsolatedProfileStore(allowed, allow_production=True)
        self.assertIsNotNone(store)
        other = AsyncMockCollection(mongomock.MongoClient().bender.alchemy_players)
        with self.assertRaises(ValueError):
            IsolatedProfileStore(other, allow_production=True)
        with self.assertRaises(ValueError):
            IsolatedProfileStore(allowed, isolated_test_only=True, allow_production=True)

    async def test_corrupt_or_unavailable_state_fails_closed(self):
        self.collection.fail_read = True
        with self.assertRaises(ReadUnavailable) as exc:
            await self.store.craft(42, uid(), ['w', 'f'])
        self.assertNotIn('private', str(exc.exception))
        self.collection.fail_read = False
        self.raw.update_one({'user_id': 42}, {'$set': {'_alc_rev': None}})
        with self.assertRaises(InvalidStoreState):
            await self.store.craft(42, uid(), ['w', 'f'])
        self.assertNotIn('_alc_ops', self.raw.find_one({'user_id': 42}))

    async def test_cas_retry_exhaustion_never_performs_a_write(self):
        self.collection.always_conflict = True
        before = deepcopy(self.raw.find_one({'user_id': 42}))
        with self.assertRaises(ConcurrentUpdate):
            await self.store.craft(42, uid(), ['w', 'f'])
        self.assertEqual(before, self.raw.find_one({'user_id': 42}))

    async def test_untrusted_fields_and_invalid_operation_id_are_rejected(self):
        with self.assertRaises(ValueError):
            await self.store.execute(42, uid(), {'kind':'x'}, lambda _: Change({'user_id': 123}, {}))
        with self.assertRaises(ValueError):
            await self.store.craft(42, 'bad', ['w', 'f'])
        self.assertNotIn('_alc_rev', self.raw.find_one({'user_id': 42}))

    def test_explicit_isolated_guard_and_majority_options(self):
        wrong = AsyncMockCollection(mongomock.MongoClient().beleyakov_db.alchemy_players)
        with self.assertRaises(ValueError):
            IsolatedProfileStore(wrong, isolated_test_only=True)
        with self.assertRaises(ValueError):
            IsolatedProfileStore(self.collection)
        # Production mode is an explicit opt-in and only for the known collection.
        with self.assertRaises(ValueError):
            IsolatedProfileStore(self.collection, allow_production=True)
        self.assertEqual(self.collection.options['write_concern'].document['w'], 'majority')
        self.assertEqual(self.collection.options['read_concern'].document['level'], 'majority')


if __name__ == '__main__':
    unittest.main()

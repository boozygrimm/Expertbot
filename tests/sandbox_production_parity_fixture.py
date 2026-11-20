"""Compare sandbox start/remove against the legacy Python callbacks, offline."""
import asyncio
import hashlib
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from handlers import alc_production, alc_core
from handlers.alc_base import HIDDEN_ITEMS
from handlers.alc_build import BUILDINGS
from miniapp_backend import sandbox_production as demo
from tests.production_characterization_fixture import run_case, NOW as TICK_NOW

NOW = 1000000


async def compare_start(worker, inventory, station='mine'):
    player = {'user_id': 42, 'inventory': inventory, 'production_slots': [], 'buildings': {'altar': 2}}
    original = deepcopy(player)
    db = SimpleNamespace(alchemy_players=SimpleNamespace(find_one=AsyncMock(return_value=deepcopy(player)), update_one=AsyncMock()))
    callback = SimpleNamespace(data=f'prod_final:0:{station}:{worker}', from_user=SimpleNamespace(id=42), answer=AsyncMock())
    with patch.object(alc_production, 'db', db), patch.object(alc_production, 'open_production', AsyncMock()), \
         patch('time.time', return_value=NOW):
        await alc_production.finalize_prod(callback)
    result, timestamp, outcome = demo.apply_production(player, NOW, {'action':'start', 'station':station, 'worker':worker}, str(uuid4()))
    assert player == original
    if result is None:
        assert outcome in ('missing_worker', 'missing_energy')
        assert db.alchemy_players.update_one.await_count == 0
        return
    assert timestamp == NOW and outcome == 'started'
    assert db.alchemy_players.update_one.await_count == 1
    query, update = db.alchemy_players.update_one.await_args.args
    assert query == {'user_id': 42}
    del result['production_slots'][0]['_sandbox_id']
    assert update['$set'] == {key: result[key] for key in ('inventory', 'production_slots')}


async def compare_remove(worker, stopped):
    slot_id = str(uuid4())
    slot = {'type': 'farm', 'worker': worker, 'assigned_at': NOW, 'total_mined': 4,
            'mined_items': {'w': 4}, 'stopped': stopped, '_sandbox_id': slot_id}
    player = {'user_id': 42, 'inventory': {'w': 2}, 'production_slots': [slot], 'buildings': {'altar': 1}}
    source = deepcopy(player)
    db = SimpleNamespace(alchemy_players=SimpleNamespace(find_one=AsyncMock(return_value=deepcopy(player)), update_one=AsyncMock()))
    callback = SimpleNamespace(data='prod_remove:0', from_user=SimpleNamespace(id=42), answer=AsyncMock())
    with patch.object(alc_production, 'db', db), patch.object(alc_production, 'open_production', AsyncMock()):
        await alc_production.remove_prod(callback)
    result, timestamp, outcome = demo.apply_production(player, NOW, {'action':'remove', 'slot_id':slot_id}, str(uuid4()))
    assert player == source and timestamp == NOW and outcome == 'removed'
    query, update = db.alchemy_players.update_one.await_args.args
    assert query == {'user_id': 42}
    assert update['$set'] == {key: result[key] for key in ('inventory', 'production_slots')}


async def main():
    assert demo.STORAGE_CAPACITY == alc_core.STORAGE_CAPACITY
    assert {k:v['items'] for k, v in demo.STATIONS.items()} == {k:v['items'] for k, v in alc_production.STATIONS.items()}
    assert demo.ALTAR_SLOTS == {0: 0, **{level: config['slots'] for level, config in BUILDINGS['altar']['levels'].items()}}
    assert set(demo.WORKERS) == set(alc_production.SPIRIT_STATS)
    assert set(HIDDEN_ITEMS) == set(demo.HIDDEN_ITEMS)
    for inv in ({'w': 0, 'e': 3, 'base_crystal': 1}, {'w': 1, 'f': 2}, {}):
        assert demo.used_slots(inv) == alc_core.get_used_slots(inv)
    for station in demo.STATIONS:
        await compare_start('none', {}, station)
        for worker in demo.WORKERS:
            await compare_start(worker, {worker: 1, 'energy': 1}, station)
    await compare_start('spirit_f', {'energy': 1})
    await compare_start('spirit_f', {'spirit_f': 1})
    await compare_remove('spirit_e', False)
    await compare_remove(None, True)
    # Compare the actual sandbox's injected rules and seed to a real scheduler
    # tick, including virtual expiration and a full warehouse.
    for station in demo.STATIONS:
        for age, full in ((3600, False), (24 * 3600, False), (3600, True)):
            key = str(uuid4())
            seed = int.from_bytes(hashlib.sha256(key.encode('ascii')).digest(), 'big')
            inventory = {f'item_{n}': 1 for n in range(20)} if full else {'w': 2, 'f': 2}
            slot = {'_sandbox_id':str(uuid4()), 'type':station, 'worker':'spirit_f',
                    'assigned_at':TICK_NOW - age, 'total_mined':0, 'stopped':False}
            player = {'user_id':42, 'inventory':inventory, 'production_slots':[slot], 'buildings':{'storage':0, 'altar':1}}
            actual = await run_case(player, seed=seed)
            result, clock, outcome = demo.apply_production(player, TICK_NOW - 3600, {'action':'advance'}, key)
            assert clock == TICK_NOW and outcome == 'advanced'
            if actual is None:
                assert result == player
            else:
                assert actual == {field:result[field] for field in ('inventory', 'production_slots')}
    alc_production.db.client.close()


if __name__ == '__main__':
    asyncio.run(main())

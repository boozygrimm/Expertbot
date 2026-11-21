"""Offline characterization of the existing Python production tick.

This runs with fake environment and replaces DB, time, random, and world effects;
it does not contact MongoDB, Telegram, or external providers.
"""
import asyncio
import random
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from handlers import alc_tasks
from miniapp_backend import production

NOW = 100_000


class FixedRandom:
    def __init__(self, rare):
        self.rare = rare

    def choice(self, choices):
        return choices[0]

    def random(self):
        return self.rare


class Cursor:
    def __init__(self, players):
        self.players = players

    def __aiter__(self):
        async def iterate():
            for player in self.players:
                yield deepcopy(player)
        return iterate()


async def run_case(player, *, effects=None, rare=0.9, seed=None):
    collection = SimpleNamespace(find=MagicMock(return_value=Cursor([player])), update_one=AsyncMock())
    legacy_rng = FixedRandom(rare) if seed is None else random.Random(seed)
    draft_rng = FixedRandom(rare) if seed is None else random.Random(seed)
    with patch.object(alc_tasks, 'db', SimpleNamespace(alchemy_players=collection)), \
         patch.object(alc_tasks.time, 'time', return_value=NOW), \
         patch.object(alc_tasks, 'get_world_effects', return_value=effects or {}), \
         patch.object(alc_tasks.random, 'choice', side_effect=legacy_rng.choice), \
         patch.object(alc_tasks.random, 'random', side_effect=legacy_rng.random):
        await alc_tasks.collect_production_loot()
    collection.find.assert_called_once_with({'production_slots': {'$exists': True, '$ne': []}})
    if not collection.update_one.await_count:
        actual = None
    else:
        assert collection.update_one.await_count == 1
        query, update = collection.update_one.await_args.args
        assert query == {'user_id': player['user_id']}
        actual = update['$set']
    original = deepcopy(player)
    with patch.object(production, 'get_world_effects', return_value=effects or {}):
        calculated = production.calculate_production_loot(player, NOW, rng=draft_rng)
    assert player == original
    assert calculated == actual, (calculated, actual)
    return actual


def player(inv, slot):
    return {'user_id': 42, 'inventory': inv, 'production_slots': [slot], 'buildings': {'storage': 0}}


async def main():
    slot = {'type': 'mine', 'worker': None, 'assigned_at': NOW - 60, 'total_mined': 0}
    base = player({'e': 2}, slot)
    original = deepcopy(base)
    got = await run_case(base)
    assert base == original
    assert got == {'inventory': {'e': 3}, 'production_slots': [slot | {'mined_items': {'e': 1}, 'total_mined': 1}]}

    spirit = slot | {'worker': 'spirit_e'}
    got = await run_case(player({'e': 2}, spirit), effects={'mine_yield': 1})
    assert got['inventory'] == {'e': 6}
    assert got['production_slots'][0]['mined_items'] == {'e': 4}
    assert got['production_slots'][0]['total_mined'] == 4

    got = await run_case(player({'e': 2}, slot), rare=0.0)
    assert got['inventory'] == {'e': 3, 'recipe_scroll': 1}
    assert got['production_slots'][0]['mined_items'] == {'e': 1}

    expired = slot | {'worker': 'spirit_e', 'assigned_at': NOW - 86400}
    got = await run_case(player({'e': 2}, expired))
    assert got['inventory'] == {'e': 2}
    assert got['production_slots'][0]['stopped'] is True
    assert got['production_slots'][0]['worker'] is None
    assert 'mined_items' not in got['production_slots'][0]

    assert await run_case(player({'e': 2}, slot | {'stopped': True})) is None

    full = {f'test_{n}': 1 for n in range(20)}
    got = await run_case(player(full, slot))
    assert got['inventory'] == full
    assert got['production_slots'][0]['mined_items'] == {}
    assert got['production_slots'][0]['total_mined'] == 0

    full_existing = {'e': 2} | {f'test_{n}': 1 for n in range(19)}
    got = await run_case(player(full_existing, slot))
    assert got['inventory'] == full_existing | {'e': 3}

    # Exercise all real station tables, workers, bonuses, rare finds and multiple
    # slots with deterministic but non-constant random streams.
    for station in ('mine', 'farm', 'factory', 'aero'):
        for worker in (None, 'spirit_e', 'spirit_w', 'other'):
            for seed in (0, 1, 42):
                candidate = player({'e': 2, 'w': 2, 'f': 2}, slot | {'type': station, 'worker': worker})
                candidate['production_slots'].append(slot | {'type': 'aero', 'worker': 'spirit_a'})
                await run_case(candidate, seed=seed, effects={'mine_yield': 2, 'farm_yield': 1,
                               'factory_yield': 1, 'aero_yield': 2, 'rare_find_bonus': 0.5})

    invalid = player({'e': 2}, slot | {'type': 'unknown'})
    try:
        await run_case(invalid)
    except KeyError:
        pass
    else:
        raise AssertionError('the original handler is expected to reject unknown stations')
    alc_tasks.db.client.close()


if __name__ == '__main__':
    asyncio.run(main())

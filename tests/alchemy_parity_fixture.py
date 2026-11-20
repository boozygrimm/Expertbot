"""Compare the existing Python Telegram handler with the new pure draft, without I/O.

Run via a subprocess with fake environment variables. All DB calls and Telegram
answers are mocked; this fixture must never access real services.
"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers import alc_lab
from handlers.alc_recipes import RECIPES, is_battle_unit
from miniapp_backend.crafting import CraftRejected, craft_transition


async def compare(ingredients, player):
    source = deepcopy(player)
    fake_players = SimpleNamespace(find_one=AsyncMock(return_value=deepcopy(player)), update_one=AsyncMock())
    callback = SimpleNamespace(from_user=SimpleNamespace(id=42), answer=AsyncMock())
    alc_lab.active_mixes[42] = list(ingredients)
    with patch.object(alc_lab, 'db', SimpleNamespace(alchemy_players=fake_players)), \
         patch.object(alc_lab, 'render_lab_window', AsyncMock()):
        await alc_lab.lab_mix_items(callback)
    try:
        decision = craft_transition(source, list(ingredients))
    except CraftRejected as exc:
        assert exc.code in {'insufficient_resources', 'fighter_recipe'}, exc.code
        assert fake_players.update_one.await_count == 0
        return
    assert fake_players.update_one.await_count == 1
    args = fake_players.update_one.await_args.args
    assert args[0] == {'user_id': 42}
    fields = args[1]['$set']
    if decision.outcome == 'no_recipe':
        assert fields == {'last_craft': decision.player['last_craft']}
    else:
        assert fields == {key: decision.player[key] for key in ('inventory', 'discovered', 'last_craft')}
    assert source == player


async def main():
    normal = {'inventory': {'f': 3, 'w': 3, 'steam': 2}, 'discovered': ['steam'], 'buildings': {}}
    await compare(['w', 'f'], normal)
    await compare(['f', 'w'], {'inventory': {'f': 1, 'w': 1}, 'discovered': [], 'buildings': {}})
    same = next(pair for pair, result in RECIPES.items() if pair[0] == pair[1] and not is_battle_unit(result))
    await compare(list(same), {'inventory': {same[0]: 2}, 'discovered': [], 'buildings': {}})
    await compare(list(same), {'inventory': {same[0]: 1}, 'discovered': [], 'buildings': {}})
    unit = next(pair for pair, result in RECIPES.items() if is_battle_unit(result))
    await compare(list(unit), {'inventory': {item: 2 for item in unit}, 'discovered': [], 'buildings': {}})
    unknown = next((a, b) for a in ['w', 'f', 'e', 'a', 'stone', 'moss']
                   for b in ['w', 'f', 'e', 'a', 'stone', 'moss'] if tuple(sorted((a, b))) not in RECIPES)
    await compare(list(unknown), {'inventory': {item: 2 for item in unknown}, 'discovered': [], 'buildings': {}})
    alc_lab.active_mixes.clear()
    alc_lab.db.client.close()


if __name__ == '__main__':
    asyncio.run(main())

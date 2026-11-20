"""Pure draft of shared crafting rules; deliberately not wired to the production bot.

This computes a candidate state only. Atomic persistence, idempotency across
restarts and coordination with all existing bot/scheduler writers are REQUIRED
before a real profile can use it.
"""
from copy import deepcopy
from dataclasses import dataclass
from handlers.alc_recipes import ELEMENTS, RECIPES, is_battle_unit
from .profile import HIDDEN_ITEMS


class CraftRejected(ValueError):
    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class CraftDecision:
    outcome: str
    player: dict
    result_id: str | None = None


def craft_transition(player: dict, ingredients: list[str]) -> CraftDecision:
    """Calculate a copy of a profile after one craft without external side effects.

    Mirrors the bot's current single-craft logic: unknown recipes do not consume
    ingredients but update last_craft; fighter recipes are redirected to barracks;
    discoveries are added once. Does not mutate the supplied player.
    """
    if (not isinstance(player, dict) or not isinstance(ingredients, list) or len(ingredients) != 2 or
        any(not isinstance(item, str) or item not in ELEMENTS or item in HIDDEN_ITEMS or
            item in {'recipe_scroll', 'pack_xl'} for item in ingredients)):
        raise CraftRejected('invalid_request', 400)
    inventory = player.get('inventory')
    discoveries = player.get('discovered', [])
    if not isinstance(inventory, dict) or not isinstance(discoveries, list):
        raise CraftRejected('invalid_profile', 409)
    for item in ingredients:
        count = inventory.get(item, 0)
        if type(count) is not int or count < ingredients.count(item):
            raise CraftRejected('insufficient_resources', 409)
    result = RECIPES.get(tuple(sorted(ingredients)))
    if result and is_battle_unit(result):
        raise CraftRejected('fighter_recipe', 409)
    if result and (type(inventory.get(result, 0)) is not int or inventory.get(result, 0) < 0):
        raise CraftRejected('invalid_profile', 409)
    next_player = deepcopy(player)
    next_player['last_craft'] = list(ingredients)
    if result is None:
        return CraftDecision('no_recipe', next_player)
    for item in ingredients:
        next_player['inventory'][item] -= 1
    next_player['inventory'][result] = next_player['inventory'].get(result, 0) + 1
    if 'discovered' not in next_player:
        next_player['discovered'] = []
    if result not in next_player['discovered']:
        next_player['discovered'].append(result)
    return CraftDecision('crafted', next_player, result)

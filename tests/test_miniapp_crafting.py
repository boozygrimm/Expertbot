from copy import deepcopy
import unittest

from handlers.alc_recipes import RECIPES, is_battle_unit
from miniapp_backend.crafting import CraftRejected, craft_transition


class CraftTransitionTests(unittest.TestCase):
    def test_success_is_pure_and_matches_existing_recipe(self):
        original = {'inventory': {'w': 2, 'f': 2}, 'discovered': [], 'unrelated': {'x': 1}}
        before = deepcopy(original)
        decision = craft_transition(original, ['f', 'w'])
        self.assertEqual(original, before)
        self.assertEqual((decision.outcome, decision.result_id), ('crafted', 'steam'))
        self.assertEqual(decision.player['inventory'], {'w': 1, 'f': 1, 'steam': 1})
        self.assertEqual(decision.player['last_craft'], ['f', 'w'])
        self.assertEqual(decision.player['discovered'], ['steam'])
        decision.player['unrelated']['x'] = 3
        self.assertEqual(original['unrelated']['x'], 1)

    def test_same_recipe_reverse_order_does_not_duplicate_discovery(self):
        player = {'inventory': {'w': 2, 'f': 2}, 'discovered': ['steam']}
        decision = craft_transition(player, ['w', 'f'])
        self.assertEqual(decision.player['discovered'], ['steam'])
        self.assertEqual(decision.result_id, 'steam')

    def test_repeated_ingredient_must_have_two_units(self):
        pair = next(pair for pair, result in RECIPES.items() if pair[0] == pair[1] and not is_battle_unit(result))
        item = pair[0]
        player = {'inventory': {item: 1}, 'discovered': []}
        with self.assertRaises(CraftRejected) as exc:
            craft_transition(player, list(pair))
        self.assertEqual(exc.exception.code, 'insufficient_resources')
        self.assertEqual(player['inventory'][item], 1)
        player['inventory'][item] = 2
        decision = craft_transition(player, list(pair))
        self.assertEqual(decision.player['inventory'][item], 0)

    def test_unknown_recipe_only_sets_last_craft(self):
        pair = next((a, b) for a in ['w', 'f', 'e', 'a', 'stone', 'moss']
                    for b in ['w', 'f', 'e', 'a', 'stone', 'moss'] if tuple(sorted((a, b))) not in RECIPES)
        player = {'inventory': {key: 2 for key in pair}, 'discovered': []}
        before = deepcopy(player)
        decision = craft_transition(player, list(pair))
        self.assertEqual(decision.outcome, 'no_recipe')
        self.assertEqual(decision.player['inventory'], before['inventory'])
        self.assertEqual(decision.player['last_craft'], list(pair))
        self.assertEqual(player, before)

    def test_fighter_recipe_is_rejected_without_change(self):
        pair = next(pair for pair, result in RECIPES.items() if is_battle_unit(result))
        player = {'inventory': {item: 2 for item in pair}, 'discovered': []}
        before = deepcopy(player)
        with self.assertRaises(CraftRejected) as exc:
            craft_transition(player, list(pair))
        self.assertEqual(exc.exception.code, 'fighter_recipe')
        self.assertEqual(player, before)

    def test_invalid_input_and_profiles_do_not_mutate_state(self):
        player = {'inventory': {'w': 2, 'f': 1}, 'discovered': []}
        before = deepcopy(player)
        for ingredients in ([], ['w'], ['w', '$inc'], ['w', 'base_crystal'], ['w', 'pack_xl']):
            with self.subTest(ingredients=ingredients), self.assertRaises(CraftRejected):
                craft_transition(player, ingredients)
        self.assertEqual(player, before)
        for malformed in ({'inventory': None}, {'inventory': {'w': True, 'f': 1}}, {'inventory': {'w': 1, 'f': 1}, 'discovered': None}):
            with self.subTest(player=malformed), self.assertRaises(CraftRejected):
                craft_transition(malformed, ['w', 'f'])


if __name__ == '__main__':
    unittest.main()

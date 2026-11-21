"""Страховка для диагностики реиграбельности алхимии.

Тест держит инварианты, на которые опирается ALCHEMY_REPLAY_PLAN.md: форма
дерева рецептов, стоимость боевых юнитов и работоспособность скрипта-пробы.
Когда баланс будут править (полосы заданий, лимит Тигля, мутаторы), проба должна
продолжать считаться — иначе цифры в плане станут неправдой.
"""
import importlib.util
import os
from pathlib import Path
import unittest

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

ROOT = Path(__file__).resolve().parents[1]


def load_probe():
    spec = importlib.util.spec_from_file_location('alchemy_economy_probe', ROOT / 'tools' / 'alchemy_economy_probe.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecipeTreeInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from handlers.alc_recipes import BATTLE_UNITS, ELEMENTS, RECIPES

        cls.elements = ELEMENTS
        cls.recipes = RECIPES
        cls.units = BATTLE_UNITS
        cls.probe = load_probe()

    def test_every_result_has_exactly_one_recipe(self):
        results = list(self.recipes.values())
        self.assertEqual(len(results), len(set(results)))

    def test_every_crafted_item_is_reachable_from_four_basics(self):
        shape = self.probe.recipe_shape(self.probe.load_recipes_module(), self.probe.leaf_costs(self.probe.load_recipes_module()))
        self.assertEqual(shape['duplicate_results'], [])
        self.assertGreaterEqual(shape['reachable_from_basics'], len(set(self.recipes.values())))

    def test_battle_units_have_recipe_and_element_cost(self):
        """Известная дыра: 'mech' потерял рецепт, из-за чего перекрафт в него бесплатен.

        Пока это не исправлено, тест фиксирует текущее состояние как известное
        исключение, чтобы новая поломка (второй бесплатный юнит) не проскочила.
        """
        from handlers.alc_recipes import ELEMENT_COSTS

        recipe_results = set(self.recipes.values())
        missing_recipe = sorted(unit for unit in self.units if unit not in recipe_results)
        missing_cost = sorted(unit for unit in self.units if unit not in ELEMENT_COSTS)
        self.assertEqual(missing_recipe, ['mech'])
        self.assertEqual(missing_cost, ['mech'])
        self.assertIsNone(ELEMENT_COSTS.get('mech'))

    def test_probe_reports_known_economy_pressure_points(self):
        report = self.probe.build_report()
        # Пороговые значения — не «правильные», а текущие: тест ловит резкий
        # сдвиг баланса, который нужно осознанно подтвердить в плане.
        self.assertGreaterEqual(report['quests']['share_tier_ge_18'], 0.5)
        self.assertGreaterEqual(report['quests']['share_pays_less_than_cost'], 0.2)
        self.assertGreaterEqual(report['converters']['crucible_median_multiplier'], 1.8)
        self.assertEqual(report['interactive_winrate']['diff22_t3_n3'], 1.0)
        self.assertGreater(report['shape']['dead_end_count'], 100)


if __name__ == '__main__':
    unittest.main()

"""Диагностика экономики и реиграбельности алхимии.

Скрипт ничего не пишет в БД и не требует aiogram: он разбирает только чистые
функции и константы из handlers/alc_recipes.py и handlers/alc_quests.py.
Нужен, чтобы любые правки баланса можно было проверять цифрами, а не на глаз.

Запуск:
    python tools/alchemy_economy_probe.py
    python tools/alchemy_economy_probe.py --json
"""

from __future__ import annotations

import argparse
import ast
import collections
import json
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BASIC = {"a", "e", "f", "w"}
PACK_VALUE = {"pack_s": 36, "pack_m": 84, "pack_l": 190, "pack_xl": 420}


# --------------------------------------------------------------------------
# Данные
# --------------------------------------------------------------------------
def load_recipes_module():
    import handlers.alc_recipes as recipes  # noqa: PLC0415 - тяжёлый импорт только здесь

    return recipes


def load_quest_functions():
    """Достаёт чистые функции квестов без импорта aiogram/db.

    roll_quest_rewards и get_item_tier не зависят ни от чего, кроме random и
    RECIPES, поэтому их безопасно исполнять из AST.
    """
    import handlers.alc_recipes as recipes  # noqa: PLC0415

    path = ROOT / "handlers" / "alc_quests.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    wanted = {"choose_reward_profile", "roll_quest_rewards", "get_item_tier"}
    module = ast.Module(
        body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted],
        type_ignores=[],
    )
    namespace = {"random": random, "TIER_CACHE": {}, "RECIPES": recipes.RECIPES}
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace


# --------------------------------------------------------------------------
# Метрики
# --------------------------------------------------------------------------
def leaf_costs(recipes) -> dict[str, int]:
    """Сколько базовых стихий нужно, чтобы собрать элемент с нуля."""
    cache: dict[str, int] = {}
    visiting: set[str] = set()

    def walk(item: str) -> int:
        if item in BASIC:
            return 1
        if item in cache:
            return cache[item]
        if item in visiting:
            return 1
        visiting.add(item)
        for (left, right), result in recipes.RECIPES.items():
            if result == item:
                cache[item] = walk(left) + walk(right)
                visiting.discard(item)
                return cache[item]
        visiting.discard(item)
        cache[item] = 10
        return 10

    return {result: walk(result) for result in set(recipes.RECIPES.values())}


def recipe_shape(recipes, costs) -> dict:
    results = list(recipes.RECIPES.values())
    ingredients = set()
    for pair in recipes.RECIPES:
        ingredients.update(pair)

    basic = set(BASIC)
    reachable = set(basic)
    changed = True
    while changed:
        changed = False
        for (left, right), result in recipes.RECIPES.items():
            if left in reachable and right in reachable and result not in reachable:
                reachable.add(result)
                changed = True

    values = sorted(costs.values())
    return {
        "elements": len(recipes.ELEMENTS),
        "recipes": len(recipes.RECIPES),
        "craftable_items": len(set(results)) + len(basic),
        "duplicate_results": [r for r, n in collections.Counter(results).items() if n > 1],
        "reachable_from_basics": len(reachable),
        "dead_ends": sorted(set(results) - ingredients),
        "dead_end_count": len(set(results) - ingredients),
        "sources": sorted(ingredients - set(results)),
        "median_cost": statistics.median(values),
        "share_cost_le_8": round(sum(1 for v in values if v <= 8) / len(values), 3),
        "share_cost_gt_32": round(sum(1 for v in values if v > 32) / len(values), 3),
        "deepest": sorted(((v, k) for k, v in costs.items()), reverse=True)[:5],
        "max_cost": values[-1],
    }


def quest_economy(recipes, costs, quests, samples: int = 40) -> dict:
    roll = quests["roll_quest_rewards"]
    get_tier = quests["get_item_tier"]
    invalid = set(recipes.BATTLE_UNITS) | {
        "base_crystal", "active_base", "pack_s", "pack_m", "pack_l", "pack_xl",
        "recipe_scroll", "energy", "spirit_f", "spirit_w", "spirit_e", "spirit_a",
    } | BASIC

    def reward_value(entry: dict) -> int:
        value = PACK_VALUE.get(entry["reward_pack"], 0)
        value += entry["reward_amt"] * costs.get(entry["reward_res"], 10)
        if entry.get("reward_bonus_item"):
            value += entry["reward_bonus_amt"] * costs.get(entry["reward_bonus_item"], 10)
        return value

    random.seed(11)
    rows = []
    for item in recipes.ELEMENTS:
        if item in invalid:
            continue
        tier = get_tier(item)
        rewards = [reward_value(roll(tier, 3, "normal")) for _ in range(samples)]
        rows.append({"item": item, "tier": tier, "cost": costs.get(item, 10), "reward": statistics.mean(rewards)})

    losing = [row for row in rows if row["reward"] < row["cost"]]
    deep = [row for row in rows if row["cost"] >= 100]
    tier_buckets = collections.Counter(
        "<8" if row["tier"] < 8 else "8-13" if row["tier"] < 14 else "14-17" if row["tier"] < 18 else ">=18"
        for row in rows
    )
    return {
        "eligible_items": len(rows),
        "tier_buckets": dict(tier_buckets),
        "share_tier_ge_18": round(tier_buckets[">=18"] / len(rows), 3),
        "median_cost": statistics.median(r["cost"] for r in rows),
        "median_reward": round(statistics.median(r["reward"] for r in rows), 1),
        "share_pays_less_than_cost": round(len(losing) / len(rows), 3),
        "deep_items": len(deep),
        "deep_median_reward": round(statistics.median(r["reward"] for r in deep), 1) if deep else None,
        "worst_examples": sorted(rows, key=lambda r: r["reward"] - r["cost"])[:5],
    }


def converter_arbitrage(recipes, costs) -> dict:
    """Тигель: 1 пара -> 2 результата. Реторта: 1 предмет -> листья."""
    pairs = [((a, b), r) for (a, b), r in recipes.RECIPES.items() if a != b]
    gain = []
    for (a, b), result in pairs:
        in_value = costs.get(a, 10) + costs.get(b, 10)
        out_value = 2 * costs.get(result, 10)
        if in_value:
            gain.append((out_value / in_value, f"{a}+{b}={result}"))
    gain.sort(reverse=True)

    retort = sorted(((costs[r], r) for r in costs if r not in BASIC), reverse=True)[:5]
    return {
        "crucible_pairs": len(pairs),
        "crucible_median_multiplier": round(statistics.median(g for g, _ in gain), 3),
        "crucible_best": [(round(g, 2), name) for g, name in gain[:3]],
        "retort_top_yield": retort,
    }


def battle_math(recipes) -> dict:
    """Боевые рецепты и дырки в них (например, юнит без рецепта)."""
    recipe_results = set(recipes.RECIPES.values())
    missing = sorted(unit for unit in recipes.BATTLE_UNITS if unit not in recipe_results)
    no_cost = sorted(unit for unit in recipes.BATTLE_UNITS if unit not in recipes.ELEMENT_COSTS)
    combo = collections.Counter(tuple(sorted(pair)) for pair in recipes.RECIPES)
    return {
        "battle_units": len(recipes.BATTLE_UNITS),
        "units_without_recipe": missing,
        "units_without_element_cost": no_cost,
        "shadowed_keys": [k for k, v in combo.items() if v > 1],
    }


def interactive_combat_winrate(trials: int = 20000, random_seed: int = 5) -> dict:
    """Шанс победы в интерактивной локации: сила отряда против difficulty*2+5..25."""
    rng = random.Random(random_seed)
    result = {}
    for difficulty in (10, 15, 22):
        for tier in (1, 2, 3):
            base = 3 * tier
            for size in (1, 3, 5):
                power = size * (base * 2 + base * 4)
                wins = 0
                for _ in range(trials):
                    enemy = difficulty * 2 + rng.randint(5, 25)
                    if power + rng.randint(0, 25) >= enemy + rng.randint(0, 20):
                        wins += 1
                result[f"diff{difficulty}_t{tier}_n{size}"] = round(wins / trials, 3)
    return result


def expedition_yield(recipes, costs) -> dict:
    """Сколько базовых единиц приносит один поход (15-20 дропов по LOOT_TABLE)."""
    path = ROOT / "handlers" / "alc_expeditions.py"
    source = path.read_text(encoding="utf-8")
    start = source.index("LOOT_TABLE = [")
    end = source.index("\n]", start) + 2
    namespace: dict = {}
    exec(compile(source[start:end], str(path), "exec"), namespace)
    table = namespace["LOOT_TABLE"]

    total = 0
    weight = 0
    for item, item_weight in table:
        value = costs.get(item) or PACK_VALUE.get(item) or 10
        total += value * item_weight
        weight += item_weight
    per_drop = total / weight
    return {
        "loot_kinds": len(table),
        "expected_leaf_value_per_drop": round(per_drop, 1),
        "expedition_15_drops": round(15 * per_drop),
        "expedition_20_drops": round(20 * per_drop),
    }


def one_shot_content(recipes) -> dict:
    """Сколько контента одноразовое и сколько повторяемого."""
    import re

    path = ROOT / "handlers" / "alc_achievements.py"
    source = path.read_text(encoding="utf-8")
    keys = re.findall(r'^    "([a-z_0-9]+)": \{"name"', source, re.MULTILINE)
    prefix = collections.Counter(key.split("_")[0] for key in keys)
    return {
        "achievements": len(keys),
        "craft_one_shots": prefix.get("craft", 0),
        "repeatable": {k: v for k, v in prefix.items() if k != "craft"},
        "recipe_book_completion": {"known": 0, "total": len(set(recipes.RECIPES.values()))},
    }


# --------------------------------------------------------------------------
# Вывод
# --------------------------------------------------------------------------
def build_report() -> dict:
    recipes = load_recipes_module()
    quests = load_quest_functions()
    costs = leaf_costs(recipes)
    return {
        "shape": recipe_shape(recipes, costs),
        "quests": quest_economy(recipes, costs, quests),
        "converters": converter_arbitrage(recipes, costs),
        "battle": battle_math(recipes),
        "interactive_winrate": interactive_combat_winrate(),
        "expeditions": expedition_yield(recipes, costs),
        "content": one_shot_content(recipes),
    }


def print_report(report: dict) -> None:
    shape = report["shape"]
    print("== Форма дерева рецептов ==")
    print(f"элементов: {shape['elements']}, рецептов: {shape['recipes']}, "
          f"достижимо из 4 стихий: {shape['reachable_from_basics']}")
    print(f"дублей-результатов: {len(shape['duplicate_results'])}, "
          f"тупиковых предметов (нигде не ингредиент): {shape['dead_end_count']}")
    print(f"медианная цена в листьях: {shape['median_cost']}, макс: {shape['max_cost']} "
          f"(самое дорогое: {shape['deepest'][0][1]})")
    print(f"дешёвых (<=8): {shape['share_cost_le_8']:.0%}, дорогих (>32): {shape['share_cost_gt_32']:.0%}")

    quests = report["quests"]
    print("\n== Доска заданий ==")
    print(f"предметов в пуле: {quests['eligible_items']}, из них tier>=18: {quests['share_tier_ge_18']:.0%}")
    print(f"медианная цена задания: {quests['median_cost']}, медианная награда: {quests['median_reward']}")
    print(f"заданий, где награда меньше себестоимости: {quests['share_pays_less_than_cost']:.0%}")
    print(f"глубоких предметов (>=100 листьев): {quests['deep_items']}, "
          f"их медианная награда: {quests['deep_median_reward']}")
    for row in quests["worst_examples"]:
        print(f"   - {row['item']}: стоит {row['cost']}, платят {row['reward']:.0f}")

    conv = report["converters"]
    print("\n== Конвертеры (Тигель / Реторта) ==")
    print(f"пар в тигле: {conv['crucible_pairs']}, "
          f"медианный множитель стоимости: x{conv['crucible_median_multiplier']}")
    print(f"лучшие пары: {conv['crucible_best']}")
    print(f"топ выхода реторты (листьев за 1 шт.): {conv['retort_top_yield']}")

    battle = report["battle"]
    print("\n== Боевая алхимия ==")
    print(f"юнитов: {battle['battle_units']}, без рецепта: {battle['units_without_recipe']}, "
          f"без стоимости: {battle['units_without_element_cost']}")
    print(f"перекрытые ключи рецептов: {battle['shadowed_keys']}")

    print("\n== Шанс победы в интерактивной локации ==")
    winrate = report["interactive_winrate"]
    for difficulty in (10, 15, 22):
        row = " ".join(
            f"T{t}·{n}чел={winrate[f'diff{difficulty}_t{t}_n{n}']:.0%}" for t in (1, 2, 3) for n in (3, 5)
        )
        print(f"сложность {difficulty}: {row}")

    exp = report["expeditions"]
    print("\n== Поход ==")
    print(f"стоимость одного дропа: {exp['expected_leaf_value_per_drop']} листьев; "
          f"поход (15-20 дропов): {exp['expedition_15_drops']}–{exp['expedition_20_drops']}")

    content = report["content"]
    print("\n== Одноразовость контента ==")
    print(f"ачивок: {content['achievements']}, из них одноразовых 'скрафти X': {content['craft_one_shots']}")
    print(f"остальные типы: {content['repeatable']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="вывести сырые данные")
    args = parser.parse_args()
    report = build_report()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

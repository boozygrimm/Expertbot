import os
import re
import unittest

os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("TELEGRAM_TOKEN", "123456:TESTTOKEN")

from app_setup import create_scheduler, handle_healthcheck, set_commands
from bot_commands import ADMIN_PRIVATE_COMMAND_SPECS, GROUP_COMMAND_SPECS, USER_PRIVATE_COMMAND_SPECS, build_bot_commands
from handlers.alc_core import format_duration, get_alchemy_cooldown, get_heritage_slots, get_used_slots
from handlers.alc_fighters import (
    FATIGUE_RECOVERY_SECONDS,
    can_pay_cost,
    consume_fighter_stamina,
    get_upgrade_cost,
    normalize_fighter,
    normalize_player_fighters,
    refresh_fighter,
    spend_cost,
)
from handlers.alc_heritage import normalize_lore_doc, with_bammm
from handlers.alc_build import (
    build_building_detail_text,
    format_building_cost,
    format_supply_report,
    get_building_status,
    get_building_status_emoji,
)
from handlers.alc_expeditions import (
    build_team_stats,
    calculate_unit_cargo,
    calculate_unit_hp,
    format_loot_summary,
    get_expedition_slot_limit,
    get_expedition_team_limit,
    trim_logs,
)
from handlers.alc_raids import (
    build_public_raid_line,
    get_allowed_fort_slots,
    get_fort_power,
    get_raid_power,
    get_recent_raid_ts,
)
from handlers.alc_quests import (
    TIER_CACHE,
    build_quest_pool,
    get_item_tier,
    get_pinned_quest_limit,
    get_valid_quest_items,
    is_invalid_quest_item,
)


class FakeBot:
    def __init__(self):
        self.calls = []

    async def set_my_commands(self, commands, scope):
        self.calls.append((commands, scope))


class StartupLogicTests(unittest.IsolatedAsyncioTestCase):
    def test_build_bot_commands_preserves_order_and_fields(self):
        commands = build_bot_commands([("help", "Справка"), ("base", "Штаб")])
        self.assertEqual([command.command for command in commands], ["help", "base"])
        self.assertEqual([command.description for command in commands], ["Справка", "Штаб"])

    def test_command_specs_use_valid_telegram_names(self):
        pattern = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
        for command, _ in USER_PRIVATE_COMMAND_SPECS + ADMIN_PRIVATE_COMMAND_SPECS + GROUP_COMMAND_SPECS:
            self.assertRegex(command, pattern)
        self.assertIn(("lore_help", "Справка по лору"), ADMIN_PRIVATE_COMMAND_SPECS)

    async def test_set_commands_registers_three_scopes(self):
        fake_bot = FakeBot()
        await set_commands(fake_bot)
        self.assertEqual(len(fake_bot.calls), 3)
        self.assertEqual([call[0][0].command for call in fake_bot.calls], ["help", "help", "help"])
        self.assertEqual([type(call[1]).__name__ for call in fake_bot.calls], [
            "BotCommandScopeAllPrivateChats",
            "BotCommandScopeChat",
            "BotCommandScopeAllGroupChats",
        ])

    def test_create_scheduler_registers_expected_jobs(self):
        scheduler = create_scheduler()
        jobs = scheduler.get_jobs()
        self.assertEqual(len(jobs), 8)
        job_ids = {job.func_ref.rsplit(":", 1)[-1] for job in jobs if job.func_ref}
        self.assertIn("check_cron_time", job_ids)
        self.assertIn("check_alchemy_stamina", job_ids)
        self.assertIn("process_random_fort_events", job_ids)

    async def test_handle_healthcheck_returns_ok_text(self):
        response = await handle_healthcheck(None)
        self.assertEqual(response.status, 200)
        self.assertIn("Бот онлайн", response.text)


class AlchemyCoreTests(unittest.TestCase):
    def test_get_heritage_slots_clamps_level(self):
        self.assertEqual(get_heritage_slots(0), 1)
        self.assertEqual(get_heritage_slots(3), 3)
        self.assertEqual(get_heritage_slots(99), 5)

    def test_get_alchemy_cooldown_uses_guild_reduction(self):
        self.assertEqual(get_alchemy_cooldown({"buildings": {"guild": 0}}), 4 * 3600)
        self.assertEqual(get_alchemy_cooldown({"buildings": {"guild": 1}}), 3 * 3600 + 30 * 60)
        self.assertEqual(get_alchemy_cooldown({"buildings": {"guild": 2}}), 3 * 3600)
        self.assertEqual(get_alchemy_cooldown({"buildings": {"guild": 3}}), 2 * 3600)

    def test_format_duration_formats_hours_and_minutes(self):
        self.assertEqual(format_duration(59), "0 мин.")
        self.assertEqual(format_duration(65 * 60), "1 ч. 5 мин.")

    def test_get_used_slots_ignores_hidden_items_and_zeroes(self):
        inventory = {
            "stone": 3,
            "sand": 0,
            "active_base": 1,
            "base_crystal": 2,
            "energy": 1,
        }
        self.assertEqual(get_used_slots(inventory), 2)


class FighterLogicTests(unittest.TestCase):
    def test_normalize_fighter_cleans_fields(self):
        fighter = normalize_fighter(
            {
                "id": "",
                "unit_id": "fire_swordsman",
                "tier": 0,
                "xp": -5,
                "custom_name": "  Командор   ",
                "history": ["  Первый бой  ", "", "x" * 200],
                "stamina": -1,
                "stamina_updated_at": 100,
                "created_at": 100,
            },
            now_ts=100,
        )
        self.assertTrue(fighter["id"])
        self.assertEqual(fighter["tier"], 1)
        self.assertEqual(fighter["xp"], 0)
        self.assertEqual(fighter["custom_name"], "Командор")
        self.assertEqual(fighter["history"][0], "Первый бой")
        self.assertEqual(len(fighter["history"][1]), 160)
        self.assertEqual(fighter["stamina"], 0)

    def test_refresh_fighter_recovers_stamina_by_time(self):
        fighter = {
            "unit_id": "fire_swordsman",
            "tier": 1,
            "stamina": 0,
            "stamina_updated_at": 1_000,
        }
        refreshed = refresh_fighter(fighter, now_ts=1_000 + FATIGUE_RECOVERY_SECONDS * 2)
        self.assertEqual(refreshed["stamina"], 2)
        self.assertEqual(refreshed["stamina_updated_at"], 1_000 + FATIGUE_RECOVERY_SECONDS * 2)

    def test_get_upgrade_cost_uses_unit_fallback_spirit(self):
        cost = get_upgrade_cost(2, {"unit_id": "fire_swordsman"})
        self.assertEqual(cost["energy"], 4)
        self.assertEqual(cost["spirit_f"], 2)
        self.assertNotIn("spirits", cost)

    def test_normalize_player_fighters_converts_old_inventory_units(self):
        player = {
            "inventory": {"fire_swordsman": 2, "stone": 5},
            "fighters": [],
            "active_expeditions": [],
            "raid_squad": {"fighter_ids": [], "fighter_snapshots": [], "energy_invested": 0, "created_at": 0},
        }
        normalized = normalize_player_fighters(player, now_ts=12345)
        self.assertEqual(normalized["inventory"], {"stone": 5})
        self.assertEqual(len(normalized["fighters"]), 2)
        self.assertTrue(all(fighter["unit_id"] == "fire_swordsman" for fighter in normalized["fighters"]))
        self.assertTrue(normalized["_fighters_changed"])

    def test_spend_cost_removes_depleted_items(self):
        inventory = {"energy": 4, "spirit_f": 2, "stone": 3}
        cost = {"energy": 4, "spirit_f": 1}
        self.assertTrue(can_pay_cost(inventory, cost))
        spend_cost(inventory, cost)
        self.assertEqual(inventory, {"spirit_f": 1, "stone": 3})

    def test_consume_fighter_stamina_updates_timestamp(self):
        fighter = {
            "unit_id": "fire_swordsman",
            "tier": 1,
            "stamina": 3,
            "stamina_updated_at": 100,
        }
        consume_fighter_stamina(fighter, now_ts=500)
        self.assertEqual(fighter["stamina"], 2)
        self.assertEqual(fighter["stamina_updated_at"], 500)


class HeritageLogicTests(unittest.TestCase):
    def test_normalize_lore_doc_clamps_and_filters(self):
        doc = normalize_lore_doc(
            {
                "id": " test ",
                "name": " ",
                "bonus_value": 99,
                "reward_amount": 0,
                "fragments": [
                    {"id": " one ", "title": "  A  ", "preview": " x "},
                    {"id": ""},
                    {"id": "two"},
                    {"id": "three"},
                    {"id": "four"},
                ],
            }
        )
        self.assertEqual(doc["id"], "test")
        self.assertEqual(doc["name"], "test")
        self.assertEqual(doc["bonus_value"], 3)
        self.assertEqual(doc["reward_amount"], 1)
        self.assertEqual([fragment["id"] for fragment in doc["fragments"]], ["one", "two", "three"])

    def test_with_bammm_appends_signature_only_once(self):
        text = with_bammm("История мира")
        self.assertIn("БАМММ", text)
        self.assertEqual(with_bammm("Финал уже есть. БАМММ!").count("БАМММ"), 1)


class BuildingLogicTests(unittest.TestCase):
    def test_get_building_status_reports_available_unavailable_and_maxed(self):
        player_ready = {
            "inventory": {"stone": 2, "sand": 2},
            "buildings": {"storage": 0, "lab": 0, "altar": 0, "guild": 0, "barracks": 0, "fort": 0, "heritage_center": 1},
        }
        status, can_upgrade, is_maxed = get_building_status("storage", player_ready)
        self.assertEqual(status, "🔵 апгрейд доступен")
        self.assertTrue(can_upgrade)
        self.assertFalse(is_maxed)

        player_blocked = {
            "inventory": {"stone": 1},
            "buildings": {"storage": 0, "lab": 0, "altar": 0, "guild": 0, "barracks": 0, "fort": 0, "heritage_center": 1},
        }
        status, can_upgrade, is_maxed = get_building_status("storage", player_blocked)
        self.assertEqual(status, "🔴 апгрейд недоступен")
        self.assertFalse(can_upgrade)
        self.assertFalse(is_maxed)

        player_maxed = {
            "inventory": {},
            "buildings": {"storage": 3, "lab": 0, "altar": 0, "guild": 0, "barracks": 0, "fort": 0, "heritage_center": 1},
        }
        status, can_upgrade, is_maxed = get_building_status("storage", player_maxed)
        self.assertEqual(status, "🟢 всё построено")
        self.assertFalse(can_upgrade)
        self.assertTrue(is_maxed)

    def test_get_building_status_emoji_extracts_marker(self):
        player = {
            "inventory": {"stone": 2, "sand": 2},
            "buildings": {"storage": 0, "lab": 0, "altar": 0, "guild": 0, "barracks": 0, "fort": 0, "heritage_center": 1},
        }
        self.assertEqual(get_building_status_emoji("storage", player), "🔵")

    def test_format_building_cost_marks_missing_and_available_resources(self):
        text = format_building_cost({"stone": 2, "sand": 1}, {"stone": 2, "sand": 2})
        self.assertIn("✅", text)
        self.assertIn("❌", text)
        self.assertIn("2/2", text)
        self.assertIn("1/2", text)

    def test_build_building_detail_text_marks_current_level_and_next_upgrade(self):
        player = {
            "inventory": {"brick": 4, "stone": 6, "metal": 1},
            "buildings": {"storage": 1, "lab": 0, "altar": 0, "guild": 0, "barracks": 0, "fort": 0, "heritage_center": 1},
        }
        text, can_upgrade = build_building_detail_text(player, "storage")
        self.assertTrue(can_upgrade)
        self.assertIn("✅ вы тут Ур. 1", text)
        self.assertIn("Следующий апгрейд: Ур. 2", text)
        self.assertIn("Лимит: 150 слотов", text)

    def test_format_supply_report_handles_empty_and_deserters(self):
        self.assertIn("данных пока нет", format_supply_report(None))
        report = {
            "drained_energy": 5,
            "served_units": 3,
            "deserters": 1,
            "energy_left": 7,
            "date_label": "сегодня",
        }
        text = format_supply_report(report)
        self.assertIn("списано 5 энергии", text)
        self.assertIn("Дезертировало: 1", text)


class ExpeditionLogicTests(unittest.TestCase):
    def test_expedition_limits_scale_by_buildings(self):
        self.assertEqual(get_expedition_slot_limit(0), 1)
        self.assertEqual(get_expedition_slot_limit(3), 2)
        self.assertEqual(get_expedition_slot_limit(5), 3)
        self.assertEqual(get_expedition_team_limit(0), 3)
        self.assertEqual(get_expedition_team_limit(2), 4)
        self.assertEqual(get_expedition_team_limit(3), 5)

    def test_team_stats_sum_hp_cargo_and_bonuses(self):
        units = ["medic", "earth_shielder", "sniper"]
        stats = build_team_stats(units, barracks_level=2)
        expected_hp = sum(calculate_unit_hp(unit, 2) for unit in units)
        expected_cargo = sum(calculate_unit_cargo(unit) for unit in units)
        self.assertEqual(stats["total_hp"], expected_hp)
        self.assertEqual(stats["cargo_capacity"], expected_cargo)
        self.assertTrue(stats["has_medic"])
        self.assertEqual(stats["defense_bonus"], 1)
        self.assertEqual(stats["loot_bonus"], 1)
        self.assertEqual(stats["tier_pressure"], 5)

    def test_trim_logs_keeps_latest_entries_only(self):
        logs = [f"log-{idx}" for idx in range(10)]
        self.assertEqual(trim_logs(logs), logs[-7:])

    def test_format_loot_summary_prioritizes_special_items(self):
        text = format_loot_summary({"stone": 2, "energy": 1, "pack_s": 1})
        first_item = text.split(", ", 1)[0]
        self.assertTrue("Энергия x1" in first_item or "Малый набор x1" in first_item)
        self.assertIn("Энергия x1", text)
        self.assertIn("Малый набор x1", text)
        self.assertIn("Камень x2", text)


class RaidLogicTests(unittest.TestCase):
    def test_get_raid_power_adds_tiers_energy_and_specialists(self):
        units = ["sniper", "saboteur", "medic"]
        self.assertEqual(get_raid_power(units, energy=2), 174)

    def test_get_fort_power_counts_level_traps_and_captives(self):
        player = {
            "buildings": {"fort": 2},
            "fort_defense": {"moat": "stone", "catapult": None, "aura": "bomb"},
            "captives": [1, 2, 3],
        }
        self.assertEqual(get_fort_power(player), 86)

    def test_get_allowed_fort_slots_and_recent_raid_ts(self):
        self.assertEqual(get_allowed_fort_slots(0), [])
        self.assertEqual(get_allowed_fort_slots(2), ["moat", "catapult"])
        self.assertEqual(get_recent_raid_ts({"raid_history": {"42": 123}}, 42), 123)
        self.assertEqual(get_recent_raid_ts({"raid_history": {}}, 42), 0)

    def test_build_public_raid_line_mentions_participants(self):
        text = build_public_raid_line("Альфа", "Бета", success=False, live_target=False, trap_triggered=True)
        self.assertIn("Альфа", text)
        self.assertIn("Бета", text)


class QuestLogicTests(unittest.TestCase):
    def setUp(self):
        TIER_CACHE.clear()

    def test_get_pinned_quest_limit_scales_with_guild(self):
        self.assertEqual(get_pinned_quest_limit(0), 0)
        self.assertEqual(get_pinned_quest_limit(1), 1)
        self.assertEqual(get_pinned_quest_limit(2), 2)
        self.assertEqual(get_pinned_quest_limit(3), 3)

    def test_get_item_tier_handles_basics_and_caches_results(self):
        self.assertEqual(get_item_tier("f"), 1)
        tier = get_item_tier("steam")
        self.assertEqual(tier, 2)
        self.assertEqual(TIER_CACHE["steam"], 2)

    def test_invalid_and_valid_quest_item_filters_work(self):
        self.assertTrue(is_invalid_quest_item("energy"))
        self.assertFalse(is_invalid_quest_item("stone"))
        valid_items = get_valid_quest_items({"stone"})
        self.assertNotIn("energy", valid_items)
        self.assertNotIn("stone", valid_items)
        self.assertIn("sand", valid_items)

    def test_build_quest_pool_respects_size_bounds(self):
        items = ["stone", "sand", "wood"]
        self.assertEqual(build_quest_pool(items, 0), [])
        self.assertEqual(build_quest_pool(items, 5), items)
        pool = build_quest_pool(items, 2)
        self.assertEqual(len(pool), 2)
        self.assertEqual(len(set(pool)), 2)


if __name__ == "__main__":
    unittest.main()

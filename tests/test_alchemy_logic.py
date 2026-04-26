import os
import re
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("TELEGRAM_TOKEN", "123456:TESTTOKEN")

import ai_logic
from ai_music_query import cleanup_music_query
from ai_summaries import collect_chunk_summaries, split_text_into_chunks
from app_setup import create_scheduler, handle_healthcheck, set_commands
from bot_commands import ADMIN_PRIVATE_COMMAND_SPECS, GROUP_COMMAND_SPECS, USER_PRIVATE_COMMAND_SPECS, build_bot_commands
from handlers.alc_base import (
    build_draft_cost,
    can_activate_base_from_universe,
    can_reset_legacy_worlds,
    collapse_starter_biome,
    get_layer_resource_pool,
    safe_edit_text,
    get_tutorial_layer_target,
    is_tutorial_world_open,
    build_barracks_status,
    build_base_summary,
    build_changelog_page,
    build_expeditions_status,
    build_fort_status,
    build_base_keyboard,
    build_lab_status_lines,
    build_production_status,
    build_storage_status,
    build_universe_keyboard,
    build_universe_layer_keyboard,
    build_universe_layer_text,
    build_universe_intro_text,
    build_world_status,
    format_process_status,
    get_headquarters_icon,
    universe_clear_layer,
    universe_reset_draft,
)
from handlers.alc_core import (
    STARTER_BIOME_COLLAPSE_BUNDLE,
    STARTER_WORLD_RESOURCE_GRANT,
    STARTER_WORLD_REWARD_BUNDLE,
    WORLD_DAILY_SPIRIT_TOKEN,
    build_base_resource_refund,
    ensure_legacy_base_crystal,
    evaluate_world_creation,
    format_duration,
    get_alchemy_cooldown,
    get_heritage_slots,
    get_used_slots,
    get_world_daily_income,
    get_world_effects,
    is_tutorial_biome,
)
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
from handlers.alc_tasks import build_daily_report_text, build_report_close_markup
from handlers.alc_heritage import normalize_lore_doc, with_bammm
from handlers.alc_build import (
    build_building_detail_text,
    format_building_cost,
    format_supply_report,
    get_building_world_focus_requirements,
    get_building_world_requirements,
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

    async def test_ai_logic_try_groq_wrapper_keeps_old_signature(self):
        with patch("ai_logic._try_groq", new=AsyncMock(return_value=("ok", 200))) as mock_try_groq:
            result = await ai_logic.try_groq("ping", use_fast_model=True)

        self.assertEqual(result, ("ok", 200))
        mock_try_groq.assert_awaited_once_with("ping", ai_logic.SYSTEM_GUARD, use_fast_model=True)

    async def test_ai_logic_make_rest_request_wrapper_injects_guard(self):
        with patch("ai_logic._make_rest_request", new=AsyncMock(return_value=("ok", 200))) as mock_request:
            result = await ai_logic.make_rest_request("prompt", "key", media_data=b"img", quiet=True)

        self.assertEqual(result, ("ok", 200))
        mock_request.assert_awaited_once_with(
            "prompt",
            "key",
            ai_logic.SYSTEM_GUARD,
            media_data=b"img",
            model="gemini-3-flash-preview",
            quiet=True,
        )

    def test_ai_logic_get_today_str_uses_msk_helper(self):
        fake_now = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
        with patch("ai_logic.get_msk_now", return_value=fake_now):
            self.assertEqual(ai_logic.get_today_str(), "26.04.2026")

    async def test_ai_logic_transcribe_audio_wrapper_keeps_old_contract(self):
        with patch("ai_logic._transcribe_audio_groq", new=AsyncMock(return_value="text")) as mock_transcribe:
            result = await ai_logic.transcribe_audio_groq("voice.ogg")

        self.assertEqual(result, "text")
        mock_transcribe.assert_awaited_once_with("voice.ogg")

    async def test_ai_logic_generate_fast_image_prompt_wrapper_keeps_old_contract(self):
        with patch("ai_logic._generate_fast_image_prompt", new=AsyncMock(return_value="prompt")) as mock_generate:
            result = await ai_logic.generate_fast_image_prompt("кот")

        self.assertEqual(result, "prompt")
        mock_generate.assert_awaited_once_with("кот")

    async def test_ai_logic_normalize_music_query_wrapper_keeps_old_contract(self):
        with patch("ai_logic._normalize_music_query", new=AsyncMock(return_value="artist title")) as mock_normalize:
            result = await ai_logic.normalize_music_query("включи artist title")

        self.assertEqual(result, "artist title")
        self.assertEqual(mock_normalize.await_args.args[0], "включи artist title")
        self.assertIs(mock_normalize.await_args.args[1], ai_logic.try_openrouter)

    def test_split_text_into_chunks_preserves_boundaries(self):
        text = "aaa\nbbb\nccc"
        chunks = split_text_into_chunks(text, chunk_limit=5)
        self.assertEqual(chunks, ["aaa", "bbb", "ccc\n"])

    def test_cleanup_music_query_removes_common_noise_words(self):
        self.assertEqual(cleanup_music_query("  Включи   песню Original Rammstein Sonne  "), "rammstein sonne")

    async def test_collect_chunk_summaries_keeps_non_empty_results(self):
        async def summarize(prompt: str) -> str:
            return "" if prompt == "skip:b" else f"sum:{prompt}"

        result = await collect_chunk_summaries(
            ["a", "b", "c"],
            lambda chunk: f"skip:{chunk}" if chunk == "b" else chunk,
            summarize,
            sleep_range=(0.0, 0.0),
        )
        self.assertEqual(result, ["sum:a", "sum:c"])

    async def test_universe_clear_layer_does_not_edit_when_layer_already_empty(self):
        callback = AsyncMock()
        callback.data = "universe_clear:foundation"
        callback.from_user.id = 101
        callback.from_user.first_name = "Tester"

        with patch("handlers.alc_base.universe_biome_drafts", {101: {"foundation": {}, "medium": {}, "energy": {}, "life": {}}}), patch(
            "handlers.alc_base.universe_active_layers", {}
        ):
            await universe_clear_layer(callback)

        callback.answer.assert_awaited_once_with("Слой уже пуст.")
        callback.message.edit_text.assert_not_awaited()

    async def test_universe_reset_draft_does_not_edit_when_draft_already_empty(self):
        callback = AsyncMock()
        callback.data = "universe_reset_draft"
        callback.from_user.id = 202
        callback.from_user.first_name = "Tester"

        with patch("handlers.alc_base.universe_biome_drafts", {202: {"foundation": {}, "medium": {}, "energy": {}, "life": {}}}), patch(
            "handlers.alc_base.universe_active_layers", {}
        ):
            await universe_reset_draft(callback)

        callback.answer.assert_awaited_once_with("Черновик уже пуст.")
        callback.message.edit_text.assert_not_awaited()

    async def test_safe_edit_text_ignores_only_message_not_modified(self):
        from aiogram.exceptions import TelegramBadRequest

        message = AsyncMock()
        message.edit_text.side_effect = TelegramBadRequest(method=AsyncMock(), message="Bad Request: message is not modified")

        await safe_edit_text(message, "same text")

        message.edit_text.assert_awaited_once()

    async def test_safe_edit_text_reraises_other_telegram_errors(self):
        from aiogram.exceptions import TelegramBadRequest

        message = AsyncMock()
        message.edit_text.side_effect = TelegramBadRequest(method=AsyncMock(), message="Bad Request: chat not found")

        with self.assertRaises(TelegramBadRequest):
            await safe_edit_text(message, "text")


class AlchemyCoreTests(unittest.TestCase):
    def test_get_heritage_slots_clamps_level(self):
        self.assertEqual(get_heritage_slots(0), 1)
        self.assertEqual(get_heritage_slots(3), 3)
        self.assertEqual(get_heritage_slots(99), 5)

    def test_ensure_legacy_base_crystal_restores_missing_progress_item(self):
        inventory, changed = ensure_legacy_base_crystal({"tutorial_step": 1, "inventory": {"stone": 5}})
        self.assertTrue(changed)
        self.assertEqual(inventory["base_crystal"], 1)
        self.assertEqual(inventory["stone"], 5)

    def test_ensure_legacy_base_crystal_skips_new_and_active_players(self):
        new_inventory, new_changed = ensure_legacy_base_crystal({"tutorial_step": 0, "inventory": {}})
        self.assertFalse(new_changed)
        self.assertEqual(new_inventory, {})

        active_inventory, active_changed = ensure_legacy_base_crystal({"tutorial_step": 2, "inventory": {"active_base": 1}})
        self.assertFalse(active_changed)
        self.assertEqual(active_inventory, {"active_base": 1})

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

    def test_get_world_effects_aggregates_biome_archetypes(self):
        player = {
            "worlds": [
                {"slots": {"matter": "stone", "medium": "swamp", "life": "moss"}},
                {"slots": {"matter": "stone", "medium": "steam", "life": "bacteria"}},
            ]
        }
        effects = get_world_effects(player)
        self.assertEqual(effects["farm_yield"], 1)
        self.assertEqual(effects["mine_yield"], 1)
        self.assertEqual(effects["river_bonus"], 2)
        self.assertEqual(effects["cave_bonus"], 2)


class BaseStatusTests(unittest.TestCase):
    def test_starter_world_grant_covers_tutorial_microbiome_cost(self):
        draft = {
            "foundation": "tutorial_seed",
            "medium": "tutorial_broth",
            "energy": "tutorial_pulse",
            "life": "tutorial_spore",
        }
        self.assertTrue(is_tutorial_biome(draft))
        cost = build_draft_cost(draft, None)
        self.assertTrue(can_pay_cost(STARTER_WORLD_RESOURCE_GRANT, cost))

    def test_tutorial_world_only_exposes_hinted_resources_by_layer(self):
        player = {"inventory": {"active_base": 1}, "worlds": []}
        self.assertTrue(is_tutorial_world_open(player))
        self.assertEqual(get_layer_resource_pool(player, "foundation"), ["stone", "wood"])
        self.assertEqual(get_layer_resource_pool(player, "medium"), ["swamp", "algae"])
        self.assertEqual(get_tutorial_layer_target("energy", "steam"), 1)
        self.assertEqual(get_tutorial_layer_target("life", "moss"), 1)

    def test_starter_world_reward_bundle_guarantees_storage_materials(self):
        self.assertGreaterEqual(STARTER_WORLD_REWARD_BUNDLE.get("stone", 0), 2)
        self.assertGreaterEqual(STARTER_WORLD_REWARD_BUNDLE.get("sand", 0), 2)

    def test_full_refund_converts_failed_biome_cost_to_basic_resources(self):
        refund = build_base_resource_refund({"steam": 1, "moss": 1}, ratio=1.0)
        self.assertEqual(refund["f"], 1)
        self.assertEqual(refund["energy"], 1)
        self.assertGreaterEqual(refund["w"], 4)
        self.assertNotIn("steam", refund)
        self.assertNotIn("moss", refund)

    @patch("handlers.alc_core.random.randint", return_value=1)
    def test_second_biome_can_fail_when_layers_pull_in_different_directions(self, _randint):
        success, capacity, absurdity = evaluate_world_creation(
            {
                "worlds": [
                    {
                        "name": "Микробиом",
                        "capacity": 4,
                        "role": "опора",
                        "support_bonus": 2,
                        "strain_cost": 1,
                        "starter": True,
                    }
                ]
            },
            {
                "foundation": "metal_frame",
                "medium": "swamp_basin",
                "energy": "ember_grid",
                "life": "spirit_garden",
            },
        )
        self.assertFalse(success)
        self.assertGreaterEqual(capacity, 1)
        self.assertGreaterEqual(absurdity, 0)

    def test_tutorial_world_grants_one_random_spirit_per_day(self):
        income = get_world_daily_income(
            {
                "worlds": [
                    {
                        "name": "Учебный микробиом",
                        "capacity": 4,
                        "role": "опора",
                        "daily_income": {WORLD_DAILY_SPIRIT_TOKEN: 1},
                        "starter": True,
                    }
                ]
            }
        )
        self.assertEqual(income, {WORLD_DAILY_SPIRIT_TOKEN: 1})

    def test_regular_world_keeps_role_based_daily_income(self):
        income = get_world_daily_income(
            {
                "worlds": [
                    {
                        "name": "Индустриальный пояс",
                        "capacity": 10,
                        "role": "промышленный",
                        "daily_income": {},
                        "starter": False,
                    }
                ]
            }
        )
        self.assertEqual(income["metal"], 2)
        self.assertEqual(income["glass"], 2)
        self.assertEqual(income["brick"], 2)
        self.assertEqual(income["energy"], 2)

    def test_collapse_starter_biome_removes_first_world_and_returns_bonus_bundle(self):
        remaining_worlds, rewards, collapsed_world = collapse_starter_biome(
            [
                {"name": "Учебный микробиом", "starter": True},
                {"name": "Индустриальный пояс", "starter": False},
            ]
        )
        self.assertEqual([world["name"] for world in remaining_worlds], ["Индустриальный пояс"])
        self.assertEqual(rewards, STARTER_BIOME_COLLAPSE_BUNDLE)
        self.assertEqual(collapsed_world["name"], "Учебный микробиом")

    def test_can_activate_base_from_universe_only_for_legacy_profiles_without_active_base(self):
        self.assertTrue(can_activate_base_from_universe({"tutorial_step": 1, "inventory": {}, "worlds": []}))
        self.assertFalse(can_activate_base_from_universe({"tutorial_step": 0, "inventory": {}, "worlds": []}))
        self.assertFalse(can_activate_base_from_universe({"tutorial_step": 2, "inventory": {"active_base": 1}, "worlds": []}))
        self.assertFalse(can_activate_base_from_universe({"tutorial_step": 2, "inventory": {}, "worlds": [{"name": "Карман"}]}))

    def test_can_reset_legacy_worlds_only_once_for_old_world_layouts(self):
        self.assertTrue(
            can_reset_legacy_worlds(
                {
                    "tutorial_step": 3,
                    "inventory": {"active_base": 1},
                    "worlds": [{"slots": {"matter": "stone", "medium": "swamp", "life": "moss"}}],
                    "world_legacy_reset_used": False,
                }
            )
        )
        self.assertTrue(
            can_reset_legacy_worlds(
                {
                    "tutorial_step": 3,
                    "inventory": {},
                    "worlds": [{"slots": {"matter": "stone", "medium": "swamp", "life": "moss"}}],
                    "world_legacy_reset_used": False,
                }
            )
        )
        self.assertFalse(
            can_reset_legacy_worlds(
                {
                    "tutorial_step": 3,
                    "inventory": {"active_base": 1},
                    "worlds": [{"slots": {"foundation": "stone_root", "medium": "swamp_basin", "energy": "spore_cycle", "life": "moss_bloom"}}],
                    "world_legacy_reset_used": False,
                }
            )
        )
        self.assertFalse(
            can_reset_legacy_worlds(
                {
                    "tutorial_step": 3,
                    "inventory": {"active_base": 1},
                    "worlds": [{"slots": {"matter": "stone", "medium": "swamp", "life": "moss"}}],
                    "world_legacy_reset_used": True,
                }
            )
        )

    def test_build_universe_intro_text_offers_reset_for_legacy_world_without_active_base(self):
        text = build_universe_intro_text(
            {
                "tutorial_step": 3,
                "inventory": {},
                "worlds": [{"slots": {"matter": "stone", "medium": "swamp", "life": "moss"}}],
                "user_id": 1,
            }
        )
        self.assertIn("одноразовая активация кристалла", text)
        self.assertIn("сбросит только старые биомы", text)

    def test_build_universe_keyboard_has_only_six_root_buttons_for_active_world_module(self):
        markup = build_universe_keyboard(
            {
                "user_id": 1,
                "tutorial_step": 3,
                "inventory": {"active_base": 1},
                "worlds": [],
            }
        )
        self.assertEqual([len(row) for row in markup.inline_keyboard], [1, 2, 2, 1])
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertEqual(labels, ["🧬 Биомы", "🪨 Основа", "🌫 Среда", "⚡ Энергия", "🧬 Жизнь", "🔙 В Штаб"])

    def test_build_universe_layer_text_shows_short_archetype_tip(self):
        text = build_universe_layer_text(
            {
                "user_id": 1,
                "inventory": {"active_base": 1},
                "worlds": [{"name": "Карман", "capacity": 4, "description": "ok", "humor": "держится"}],
            },
            "foundation",
        )
        self.assertIn("Камень тянет в опору", text)

    def test_build_universe_intro_text_shows_fortify_cost(self):
        text = build_universe_intro_text(
            {
                "user_id": 1,
                "tutorial_step": 3,
                "inventory": {"active_base": 1},
                "world_support_level": 0,
                "worlds": [{"name": "Карман", "capacity": 4, "description": "ok", "humor": "держится"}],
            }
        )
        self.assertIn("Следующее укрепление", text)
        self.assertIn("Камень", text)

    def test_build_universe_layer_keyboard_shows_step_and_grid_controls(self):
        markup = build_universe_layer_keyboard(
            {
                "user_id": 1,
                "inventory": {"active_base": 1, "stone": 2, "wood": 2},
                "worlds": [{"name": "Карман", "capacity": 4, "description": "ok", "humor": "держится"}],
                "inv_grid": 3,
            },
            "foundation",
        )
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertIn("x1", labels)
        self.assertIn("✅ x5", labels)
        self.assertIn("x10", labels)
        self.assertIn("x2", labels)
        self.assertIn("✅ x3", labels)
        self.assertIn("x4", labels)

    def test_get_headquarters_icon_scales_with_fort(self):
        self.assertEqual(get_headquarters_icon(0), "🛖")
        self.assertEqual(get_headquarters_icon(1), "🏕")
        self.assertEqual(get_headquarters_icon(2), "🏰")
        self.assertEqual(get_headquarters_icon(3), "🏯")

    def test_format_process_status_handles_empty_running_and_ready(self):
        self.assertEqual(
            format_process_status(None, running_text="варит", ready_text="готово"),
            "🔴 пусто",
        )
        running = format_process_status({"finish_time": 4_000_000_000}, running_text="варит", ready_text="готово")
        self.assertEqual(running, "🔵 варит")
        ready = format_process_status({"finish_time": 1}, running_text="варит", ready_text="готово")
        self.assertEqual(ready, "🟢 готово")

    def test_build_storage_status_counts_only_activatable_items(self):
        player = {"inventory": {"pack_s": 2, "recipe_scroll": 1, "stone": 9}}
        self.assertEqual(build_storage_status(player), "🟢 активаций 3")
        self.assertEqual(build_storage_status({"inventory": {"stone": 1}}), "🔴 активаций 0")

    def test_build_lab_status_lines_reflect_levels_and_processes(self):
        self.assertEqual(build_lab_status_lines({"buildings": {"lab": 0}}), [])
        lines = build_lab_status_lines(
            {
                "buildings": {"lab": 3},
                "crucible_process": {"finish_time": 4_000_000_000},
                "retort_process": {"finish_time": 1},
            }
        )
        self.assertEqual(len(lines), 2)
        self.assertIn("🔥 Тигель: 🔵 переплавляет", lines[0])
        self.assertIn("🧪 Реторта: 🟢 фракции готовы", lines[1])

    def test_build_production_barracks_expeditions_and_fort_statuses(self):
        self.assertEqual(build_production_status({"buildings": {"altar": 0}}), "")
        production = build_production_status(
            {
                "buildings": {"altar": 2},
                "production_slots": [{"assigned_at": 4_000_000_000}, {"assigned_at": 0, "stopped": True}],
            }
        )
        self.assertIn("🏭 Производство: 🔵 1/2 в работе", production)

        barracks = build_barracks_status(
            {
                "buildings": {"barracks": 1},
                "fighters": [
                    {"status": "available", "stamina": 2},
                    {"status": "expedition", "stamina": 1},
                    {"status": "dead", "stamina": 1},
                ],
            }
        )
        self.assertEqual(barracks, "🎖 Казармы: 🔵 строй 1 | поход 1")

        expeditions = build_expeditions_status(
            {
                "buildings": {"barracks": 1},
                "active_expeditions": [{"status": "arrived"}, {"status": "running"}],
            }
        )
        self.assertEqual(expeditions, "🧭 Экспедиции: 🟢 ждут 1 | в пути 1")

        fort = build_fort_status(
            {
                "buildings": {"fort": 1},
                "fort_defense": {"moat": "stone", "catapult": None},
                "captives": [1, 2],
            }
        )
        self.assertEqual(fort, "🏰 Форт: 🟢 ловушки 1 | плен 2")

    def test_build_base_summary_includes_key_sections(self):
        text = build_base_summary(
            {
                "user_name": "Тест",
                "last_mine": 0,
                "inventory": {"pack_s": 1, "active_base": 1},
                "buildings": {"fort": 2, "lab": 2, "altar": 1, "barracks": 1},
                "crucible_process": {"finish_time": 1},
                "production_slots": [],
                "fighters": [],
                "active_expeditions": [],
                "worlds": [{"name": "Карман", "capacity": 4, "description": "ok", "humor": "не дымит"}],
            }
        )
        self.assertIn("🏰 **ГЛАВНЫЙ ШТАБ**", text)
        self.assertIn("Тест | версия `", text)
        self.assertIn("🌍 Мир:", text)
        self.assertIn("🎒 Склад: 🟢 активаций 1", text)
        self.assertIn("🔥 Тигель:", text)

    def test_build_world_status_handles_empty_and_real_world(self):
        self.assertIn("нужен кристалл базы", build_world_status({"inventory": {}, "buildings": {}}))
        self.assertIn(
            "собери первый микробиом",
            build_world_status({"inventory": {"active_base": 1}, "buildings": {"lab": 1}, "tutorial_step": 3}),
        )
        self.assertIn(
            "1/4 вместимости",
            build_world_status(
                {
                    "inventory": {"active_base": 1},
                    "buildings": {"storage": 2},
                    "worlds": [{"name": "Карман", "capacity": 4, "description": "ok", "humor": "держится"}],
                }
            ),
        )

    def test_build_universe_intro_text_offers_direct_activation_for_legacy_profile(self):
        text = build_universe_intro_text({"tutorial_step": 1, "inventory": {}, "worlds": []})
        self.assertIn("кнопку ниже", text)
        self.assertIn("первому биому", text)

    def test_build_universe_intro_text_shows_one_time_tutorial_hints(self):
        text = build_universe_intro_text(
            {
                "tutorial_step": 2,
                "inventory": {"active_base": 1},
                "worlds": [],
                "world_tutorial": {"active": True, "created": False, "hints_shown": False},
            }
        )
        self.assertIn("Одноразовые подсказки", text)
        self.assertIn("Камня и Древесины", text)
        self.assertIn("Стартовых ресурсов", text)

    def test_build_universe_intro_text_hides_tutorial_hints_after_first_show(self):
        text = build_universe_intro_text(
            {
                "tutorial_step": 2,
                "inventory": {"active_base": 1},
                "worlds": [],
                "world_tutorial": {"active": True, "created": False, "hints_shown": True},
            }
        )
        self.assertNotIn("Одноразовые подсказки", text)

    def test_build_changelog_page_clamps_page_and_has_back_button(self):
        text, markup = build_changelog_page(999)
        self.assertIn("🧾 <b>АЛЬФА ИЗМЕНЕНИЙ</b>", text)
        self.assertIn("Текущая версия:", text)
        self.assertEqual(markup.inline_keyboard[-1][0].text, "🔙 В Штаб")

    def test_build_base_keyboard_shows_optional_sections(self):
        markup = build_base_keyboard(
            {
                "last_mine": 0,
                "buildings": {"altar": 1, "barracks": 1, "fort": 1},
            }
        )
        button_texts = [button.text for row in markup.inline_keyboard for button in row]
        self.assertIn("🏭", button_texts)
        self.assertIn("🧭", button_texts)
        self.assertIn("🏰 Форт", button_texts)


class ReportTextTests(unittest.TestCase):
    def test_build_daily_report_text_handles_full_and_empty_reports(self):
        empty = build_daily_report_text(None, None)
        self.assertIn("📊 <b>УТРЕННИЙ ОТЧЕТ</b> [сегодня]", empty)
        self.assertIn("Активных начислений не было.", empty)

        report = build_daily_report_text(
            {
                "drained_energy": 5,
                "served_units": 3,
                "deserters": 1,
                "exhausted_units": 2,
                "xp_lost": 4,
                "energy_left": 7,
                "date_label": "2026-04-26",
            },
            {
                "granted_lines": ["Камень x2"],
                "skipped_lines": ["Металл x1"],
            },
        )
        self.assertIn("Списано энергии: 5", report)
        self.assertIn("Остались без отдыха: 2", report)
        self.assertIn("Дезертировало: 1", report)
        self.assertIn("Получено: Камень x2", report)
        self.assertIn("Не влезло на склад: Металл x1", report)

    def test_build_report_close_markup_has_single_close_button(self):
        markup = build_report_close_markup()
        self.assertEqual(len(markup.inline_keyboard), 1)
        self.assertEqual(markup.inline_keyboard[0][0].text, "❌ Закрыть")
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "alc:base_close")


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
        self.assertEqual(status, "🔵")
        self.assertTrue(can_upgrade)
        self.assertFalse(is_maxed)

        player_blocked = {
            "inventory": {"stone": 1},
            "buildings": {"storage": 0, "lab": 0, "altar": 0, "guild": 0, "barracks": 0, "fort": 0, "heritage_center": 1},
        }
        status, can_upgrade, is_maxed = get_building_status("storage", player_blocked)
        self.assertEqual(status, "🔴")
        self.assertFalse(can_upgrade)
        self.assertFalse(is_maxed)

        player_maxed = {
            "inventory": {},
            "buildings": {"storage": 3, "lab": 0, "altar": 0, "guild": 0, "barracks": 0, "fort": 0, "heritage_center": 1},
        }
        status, can_upgrade, is_maxed = get_building_status("storage", player_maxed)
        self.assertEqual(status, "🟢")
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
            "buildings": {"storage": 1, "lab": 0, "altar": 0, "guild": 0, "barracks": 0, "fort": 0, "heritage_center": 1},
            "inventory": {"brick": 4, "stone": 6, "metal": 1, "active_base": 1},
            "worlds": [{"name": "Карман", "capacity": 4, "description": "ok", "humor": "держится"}],
        }
        text, can_upgrade = build_building_detail_text(player, "storage")
        self.assertTrue(can_upgrade)
        self.assertIn("✅ вы тут Ур. 1", text)
        self.assertIn("Следующий апгрейд: Ур. 2", text)
        self.assertIn("Лимит: 150 слотов", text)
        self.assertIn("Вместимость мира", text)

    def test_building_world_requirements_gate_specific_upgrades(self):
        self.assertEqual(get_building_world_requirements("altar", 2), {})
        player_ready = {
            "inventory": {"brick": 10, "metal": 4, "glass": 3, "active_base": 1},
            "buildings": {"storage": 0, "lab": 0, "altar": 1, "guild": 0, "barracks": 0, "fort": 0, "heritage_center": 1},
            "worlds": [
                {"name": "Чаща", "capacity": 4, "description": "ok", "humor": "держится", "role": "опора", "support_bonus": 2, "strain_cost": 1}
            ],
        }
        status, can_upgrade, is_maxed = get_building_status("altar", player_ready)
        self.assertEqual(status, "🔵")
        self.assertTrue(can_upgrade)
        self.assertFalse(is_maxed)

    def test_building_focus_requirements_gate_late_upgrades(self):
        self.assertEqual(get_building_world_focus_requirements("altar", 3), {})
        player_ready = {
            "inventory": {"electricity": 5, "diamond": 1, "robot": 1, "active_base": 1},
            "buildings": {"storage": 0, "lab": 0, "altar": 2, "guild": 0, "barracks": 0, "fort": 0, "heritage_center": 1},
            "worlds": [
                {"name": "Шов", "capacity": 4, "description": "ok", "humor": "держится", "role": "промышленный", "support_bonus": 1, "strain_cost": 2}
            ],
        }
        status, can_upgrade, _ = get_building_status("altar", player_ready)
        self.assertEqual(status, "🔵")
        self.assertTrue(can_upgrade)

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

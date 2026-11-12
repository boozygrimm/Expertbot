import re
import logging
from collections import OrderedDict

from aiogram import BaseMiddleware, F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import ADMIN_ID, get_msk_now
from bender_triggers import parse_video_request
from database import db

router = Router()
logger = logging.getLogger(__name__)

FEATURE_PANEL_SETTING = "feature_panel"


class PanelState(StatesGroup):
    waiting_for_daily_time = State()


FEATURES = OrderedDict({
    "assistant": {
        "label": "Эксперт",
        "icon": "🧠",
        "default": True,
        "hint": "Ответы Экса и ответы по reply на бота."
    },
    "video_notes": {
        "label": "Кружки Бендера",
        "icon": "🎬",
        "default": False,
        "hint": "Ответы видеокружком по /bender или Экс, кружок."
    },
    "images": {
        "label": "Картинки",
        "icon": "🎨",
        "default": True,
        "hint": "Команда /img и запросы на генерацию картинок."
    },
    "music": {
        "label": "Музыка",
        "icon": "🎵",
        "default": True,
        "hint": "Команда /song и музыкальные запросы через Экса."
    },
    "voice": {
        "label": "Голосовые",
        "icon": "🎙",
        "default": True,
        "hint": "Авторасшифровка и ручная расшифровка голосовых."
    },
    "reports": {
        "label": "Отчёты",
        "icon": "📰",
        "default": True,
        "hint": "Сплетни, выбор эксперта и сводки по чату."
    },
    "games": {
        "label": "Игры",
        "icon": "🎮",
        "default": True,
        "hint": "Поле чудес, Виселица и Анахронизм."
    },
    "social": {
        "label": "Социал RP",
        "icon": "🎭",
        "default": False,
        "hint": "Социальные RP-действия."
    },
    "social_18": {
        "label": "RP 18+",
        "icon": "🔞",
        "default": False,
        "hint": "Взрослые RP-действия."
    },
    "alchemy": {
        "label": "Алхимия",
        "icon": "⚗️",
        "default": True,
        "hint": "Походы, база, лаборатория, квесты и постройки."
    },
    "daily_reports": {
        "label": "Суточный отчёт",
        "icon": "⏱",
        "default": True,
        "hint": "Настройка и отправка суточного отчёта."
    },
})

SOCIAL_SAFE_TRIGGERS = [
    "обнять", "поцеловать", "укусить", "погладить", "пнуть",
    "ударить", "пожать руку", "дать пять", "кусь", "чмок", "похлопать"
]
SOCIAL_18_TRIGGERS = [
    "засосать", "шлёпнуть", "связать", "раздеть", "наказать", "выпороть", "шмальнуть"
]

ALCHEMY_CALLBACK_PREFIXES = (
    "alc:",
    "alc_use:",
    "alc_page:",
    "lab_",
    "build_",
    "quest_",
    "prod_",
    "crucible_",
    "retort_",
    "altar_open",
    "quests_open",
)

GAME_CALLBACK_PREFIXES = (
    "spin_drum",
    "guess_full_word",
    "cl_",
    "hm_take_turn",
    "hm_cl_",
    "anach:",
)

REPORT_CALLBACK_PREFIXES = (
    "rep:",
)

DAILY_CALLBACK_PREFIXES = (
    "cron_menu_",
    "cron_status",
    "cron_back",
    "c_set:",
    "c_stop:",
)


async def is_chat_admin(bot, chat_id: int, user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    if chat_id == user_id:
        return False
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ["administrator", "creator"]
    except TelegramBadRequest:
        return False


def get_default_feature_flags() -> dict:
    return {feature: meta["default"] for feature, meta in FEATURES.items()}


async def _get_legacy_social_state(chat_id: int, setting_name: str, default: bool) -> bool:
    doc = await db.settings.find_one({"setting": setting_name, "chat_id": chat_id})
    if not doc:
        return default
    return bool(doc.get("is_active", default))


async def _get_daily_report_doc(chat_id: int):
    return await db.settings.find_one({"setting": "daily_report", "chat_id": chat_id})


async def get_daily_report_time(chat_id: int) -> str | None:
    doc = await _get_daily_report_doc(chat_id)
    if doc and doc.get("is_active") and doc.get("time"):
        return doc["time"]
    return None


async def get_chat_feature_flags(chat_id: int) -> dict:
    flags = get_default_feature_flags()

    doc = await db.settings.find_one({"setting": FEATURE_PANEL_SETTING, "chat_id": chat_id})
    if doc:
        flags.update(doc.get("features", {}))

    if "social" not in (doc or {}).get("features", {}):
        flags["social"] = await _get_legacy_social_state(chat_id, "social_module", FEATURES["social"]["default"])
    if "social_18" not in (doc or {}).get("features", {}):
        flags["social_18"] = await _get_legacy_social_state(chat_id, "social_nsfw", FEATURES["social_18"]["default"])

    daily_report_time = await get_daily_report_time(chat_id)
    if flags.get("daily_reports", FEATURES["daily_reports"]["default"]) and not daily_report_time:
        flags["daily_reports"] = False

    return flags


async def is_feature_enabled(chat_id: int, feature: str) -> bool:
    if chat_id >= 0:
        return True
    if feature not in FEATURES:
        return True
    flags = await get_chat_feature_flags(chat_id)
    return bool(flags.get(feature, FEATURES[feature]["default"]))


async def set_feature_state(chat_id: int, feature: str, state: bool) -> None:
    if feature not in FEATURES:
        return

    await db.settings.update_one(
        {"setting": FEATURE_PANEL_SETTING, "chat_id": chat_id},
        {"$set": {f"features.{feature}": state, "updated_at": get_msk_now()}},
        upsert=True,
    )

    if feature == "social":
        await db.settings.update_one(
            {"setting": "social_module", "chat_id": chat_id},
            {"$set": {"is_active": state, "updated_at": get_msk_now()}},
            upsert=True,
        )
    elif feature == "social_18":
        await db.settings.update_one(
            {"setting": "social_nsfw", "chat_id": chat_id},
            {"$set": {"is_active": state, "updated_at": get_msk_now()}},
            upsert=True,
        )
    elif feature == "daily_reports" and not state:
        await db.settings.update_one(
            {"setting": "daily_report", "chat_id": chat_id},
            {"$set": {"is_active": False, "updated_at": get_msk_now()}},
            upsert=True,
        )


async def set_all_feature_states(chat_id: int, state: bool) -> None:
    features = {feature: state for feature in FEATURES}
    await db.settings.update_one(
        {"setting": FEATURE_PANEL_SETTING, "chat_id": chat_id},
        {"$set": {"features": features, "updated_at": get_msk_now()}},
        upsert=True,
    )

    await db.settings.update_one(
        {"setting": "social_module", "chat_id": chat_id},
        {"$set": {"is_active": state, "updated_at": get_msk_now()}},
        upsert=True,
    )
    await db.settings.update_one(
        {"setting": "social_nsfw", "chat_id": chat_id},
        {"$set": {"is_active": state, "updated_at": get_msk_now()}},
        upsert=True,
    )
    if not state:
        await db.settings.update_one(
            {"setting": "daily_report", "chat_id": chat_id},
            {"$set": {"is_active": False, "updated_at": get_msk_now()}},
            upsert=True,
        )


def _status_icon(state: bool) -> str:
    return "🟢" if state else "🔴"


async def build_feature_panel_markup(chat_id: int):
    flags = await get_chat_feature_flags(chat_id)
    builder = InlineKeyboardBuilder()

    for feature, meta in FEATURES.items():
        builder.button(
            text=f"{_status_icon(flags.get(feature, meta['default']))}{meta['icon']}",
            callback_data=f"feat:toggle:{feature}",
        )

    builder.button(text="✅ Всё вкл", callback_data="feat:all:on")
    builder.button(text="ℹ️ Подробнее", callback_data="feat:details")
    builder.button(text="🔄 Обновить", callback_data="feat:refresh")
    builder.button(text="❌ Закрыть", callback_data="feat:close")
    builder.adjust(5, 5, 2, 2)
    return builder.as_markup()


async def build_feature_panel_text(chat_id: int) -> str:
    flags = await get_chat_feature_flags(chat_id)
    daily_time = await get_daily_report_time(chat_id)

    lines = [
        "🛠 **ПАНЕЛЬ ЧАТА**",
        "",
        "Нажми кнопку ниже. Цвет показывает статус модуля.",
        "",
    ]

    for feature, meta in FEATURES.items():
        prefix = f"{_status_icon(flags.get(feature, meta['default']))} {meta['icon']} **{meta['label']}**"
        if feature == "daily_reports" and daily_time and flags.get(feature, meta["default"]):
            lines.append(f"{prefix} • `{daily_time}` МСК")
        else:
            lines.append(prefix)

    return "\n".join(lines)


async def build_feature_details_text(chat_id: int) -> str:
    flags = await get_chat_feature_flags(chat_id)
    daily_time = await get_daily_report_time(chat_id)

    try:
        from handlers.ex_logic import HELP_TEXT
    except Exception:
        HELP_TEXT = "Справка Экса временно недоступна."

    legend_lines = ["**Модули панели:**"]
    for feature, meta in FEATURES.items():
        line = f"{_status_icon(flags.get(feature, meta['default']))} {meta['icon']} **{meta['label']}** — {meta['hint']}"
        if feature == "daily_reports" and daily_time and flags.get(feature, meta["default"]):
            line += f" Сейчас: `{daily_time}` МСК."
        legend_lines.append(line)

    return (
        "ℹ️ **ПОДРОБНЕЕ**\n\n"
        "Ниже актуальная справка по Эксу и краткое описание модулей панели.\n\n"
        f"{HELP_TEXT}\n\n"
        + "\n".join(legend_lines)
    )


async def render_feature_panel(target):
    chat_id = target.chat.id if isinstance(target, types.Message) else target.message.chat.id
    text = await build_feature_panel_text(chat_id)
    markup = await build_feature_panel_markup(chat_id)

    if isinstance(target, types.Message):
        await target.answer(text, reply_markup=markup, parse_mode="Markdown")
    else:
        try:
            await target.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                raise
        await target.answer()


async def render_feature_details(callback: types.CallbackQuery):
    text = await build_feature_details_text(callback.message.chat.id)
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 К панели", callback_data="feat:back")
    builder.button(text="❌ Закрыть", callback_data="feat:close")
    builder.adjust(2)

    try:
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise
    await callback.answer()


async def render_daily_report_prompt(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="feat:back")
    builder.button(text="❌ Закрыть", callback_data="feat:close")
    builder.adjust(2)

    text = (
        "⏱ **СУТОЧНЫЙ ОТЧЁТ**\n\n"
        "Отправь время в формате `ЧЧ:ММ`.\n"
        "Пример: `09:30`\n\n"
        "Время указывается по МСК."
    )
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    await callback.answer()


async def ensure_feature_enabled(message: types.Message, feature: str) -> bool:
    return await is_feature_enabled(message.chat.id, feature)


async def ensure_feature_enabled_callback(callback: types.CallbackQuery, feature: str) -> bool:
    return await is_feature_enabled(callback.message.chat.id, feature)


def detect_message_feature(message: types.Message):
    if message.chat.type == "private":
        return None

    if message.voice or message.video_note:
        return "voice"

    text = (message.text or "").strip()
    if not text:
        return None

    lower_text = text.lower()

    if parse_video_request(text) is not None:
        return "video_notes"

    if lower_text.startswith(("/panel", "/панель")):
        return None
    if lower_text.startswith(("/img", "/pic", "/рис")):
        return "images"
    if lower_text.startswith("/song"):
        return "music"
    if lower_text.startswith("/voice"):
        return "voice"
    if re.match(r"^/?(вещай|сплетни|выбор)(\s+\d+)?$", lower_text):
        return "reports"
    if lower_text.startswith(("/vis", "/виселица", "/pole", "/anachronism", "/анахронизм", "/ставка", "/topgame", "/топигр", "/очки")):
        return "games"
    if lower_text.startswith(("/base", "/база", "/pohod", "/поход", "/добыча", "/regen_quests")):
        return "alchemy"
    if lower_text.startswith(("/set_daily", "/cron", "/stop_daily")):
        return "daily_reports"

    if any(lower_text.startswith(cmd) for cmd in SOCIAL_18_TRIGGERS):
        return "social_18"
    if any(lower_text.startswith(cmd) for cmd in SOCIAL_SAFE_TRIGGERS):
        return "social"

    is_ex_trigger = bool(re.match(r"(?i)^экс(?:перт)?[\s,]*(.*)", text))
    is_reply_to_bot = bool(message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id == message.bot.id)
    if is_ex_trigger or is_reply_to_bot:
        return "assistant"

    return None


def detect_callback_feature(callback: types.CallbackQuery):
    if not callback.message or callback.message.chat.type == "private":
        return None

    data = callback.data or ""
    if data.startswith("feat:"):
        return None
    if data in DAILY_CALLBACK_PREFIXES or any(data.startswith(prefix) for prefix in DAILY_CALLBACK_PREFIXES):
        return "daily_reports"
    if any(data.startswith(prefix) for prefix in REPORT_CALLBACK_PREFIXES):
        return "reports"
    if data in GAME_CALLBACK_PREFIXES or any(data.startswith(prefix) for prefix in GAME_CALLBACK_PREFIXES):
        return "games"
    if data == "altar_open" or data == "quests_open" or any(data.startswith(prefix) for prefix in ALCHEMY_CALLBACK_PREFIXES):
        return "alchemy"
    return None


class FeatureAccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, types.Message):
            state: FSMContext | None = data.get("state")
            if state:
                current_state = await state.get_state()
                if current_state == PanelState.waiting_for_daily_time.state:
                    return await handler(event, data)

            feature = detect_message_feature(event)
            if feature and not await is_feature_enabled(event.chat.id, feature):
                return

        if isinstance(event, types.CallbackQuery):
            feature = detect_callback_feature(event)
            if feature and not await is_feature_enabled(event.message.chat.id, feature):
                await event.answer()
                return

        return await handler(event, data)


@router.message(Command("panel", "панель"))
async def open_feature_panel(message: types.Message, bot, state: FSMContext):
    if message.chat.type not in {"group", "supergroup"}:
        return await message.reply("ℹ️ Эту панель нужно открывать прямо в группе, которую настраиваешь.")

    if not await is_chat_admin(bot, message.chat.id, message.from_user.id):
        return await message.reply("❌ Панелью могут управлять только администраторы чата.")

    await state.clear()
    await render_feature_panel(message)
    try:
        await message.delete()
    except Exception as e:
        logger.debug("Failed to delete panel command message in chat %s: %s", message.chat.id, e)


@router.callback_query(F.data.startswith("feat:toggle:"))
async def toggle_feature(callback: types.CallbackQuery, bot, state: FSMContext):
    if not await is_chat_admin(bot, callback.message.chat.id, callback.from_user.id):
        return await callback.answer("Только администраторы чата могут менять настройки.", show_alert=True)

    feature = callback.data.split(":")[2]
    flags = await get_chat_feature_flags(callback.message.chat.id)
    current_state = bool(flags.get(feature, FEATURES[feature]["default"]))

    if feature == "daily_reports":
        if current_state:
            await set_feature_state(callback.message.chat.id, feature, False)
            await state.clear()
            await render_feature_panel(callback)
            return

        await state.set_state(PanelState.waiting_for_daily_time)
        await state.update_data(
            panel_chat_id=callback.message.chat.id,
            panel_message_id=callback.message.message_id,
        )
        await render_daily_report_prompt(callback)
        return

    await set_feature_state(callback.message.chat.id, feature, not current_state)
    await state.clear()
    await render_feature_panel(callback)


@router.message(PanelState.waiting_for_daily_time)
async def process_daily_time_from_panel(message: types.Message, bot, state: FSMContext):
    if not await is_chat_admin(bot, message.chat.id, message.from_user.id):
        return await message.reply("❌ Менять суточный отчёт могут только администраторы чата.")

    time_str = (message.text or "").strip()
    if not re.match(r"^([01]\d|2[0-3]):([0-5]\d)$", time_str):
        return await message.reply("❌ Неверный формат. Используй `ЧЧ:ММ`, например `09:30`.", parse_mode="Markdown")

    data = await state.get_data()
    panel_message_id = data.get("panel_message_id")
    panel_chat_id = data.get("panel_chat_id", message.chat.id)

    await db.settings.update_one(
        {"setting": "daily_report", "chat_id": panel_chat_id},
        {
            "$set": {
                "time": time_str,
                "is_active": True,
                "set_by": message.from_user.id,
                "updated_at": get_msk_now(),
            }
        },
        upsert=True,
    )
    await set_feature_state(panel_chat_id, "daily_reports", True)
    await state.clear()

    if panel_message_id:
        text = await build_feature_panel_text(panel_chat_id)
        markup = await build_feature_panel_markup(panel_chat_id)
        try:
            await bot.edit_message_text(
                text=text,
                chat_id=panel_chat_id,
                message_id=panel_message_id,
                reply_markup=markup,
                parse_mode="Markdown",
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                raise

    await message.reply(f"✅ Суточный отчёт включён на `{time_str}` МСК.", parse_mode="Markdown")


@router.callback_query(F.data == "feat:details")
async def show_feature_details(callback: types.CallbackQuery, bot):
    if not await is_chat_admin(bot, callback.message.chat.id, callback.from_user.id):
        return await callback.answer("Только администраторы чата могут открывать панель.", show_alert=True)
    await render_feature_details(callback)


@router.callback_query(F.data == "feat:back")
async def back_to_feature_panel(callback: types.CallbackQuery, bot, state: FSMContext):
    if not await is_chat_admin(bot, callback.message.chat.id, callback.from_user.id):
        return await callback.answer("Только администраторы чата могут открывать панель.", show_alert=True)
    await state.clear()
    await render_feature_panel(callback)


@router.callback_query(F.data == "feat:refresh")
async def refresh_feature_panel(callback: types.CallbackQuery, bot):
    if not await is_chat_admin(bot, callback.message.chat.id, callback.from_user.id):
        return await callback.answer("Только администраторы чата могут открывать панель.", show_alert=True)
    await render_feature_panel(callback)


@router.callback_query(F.data == "feat:all:on")
async def enable_all_features(callback: types.CallbackQuery, bot, state: FSMContext):
    if not await is_chat_admin(bot, callback.message.chat.id, callback.from_user.id):
        return await callback.answer("Только администраторы чата могут менять настройки.", show_alert=True)
    await state.clear()
    await set_all_feature_states(callback.message.chat.id, True)
    await render_feature_panel(callback)


@router.callback_query(F.data == "feat:close")
async def close_feature_panel(callback: types.CallbackQuery, bot, state: FSMContext):
    if not await is_chat_admin(bot, callback.message.chat.id, callback.from_user.id):
        return await callback.answer("Только администраторы чата могут управлять панелью.", show_alert=True)
    await state.clear()
    await callback.message.delete()
    await callback.answer()

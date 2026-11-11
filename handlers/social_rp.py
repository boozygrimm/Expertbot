import asyncio
import importlib
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest

from database import db
from ai_logic import try_groq
from config import get_msk_now
from handlers.admin_cron import is_chat_admin
from handlers.admin_panel import get_chat_feature_flags, set_feature_state

router = Router()
logger = logging.getLogger(__name__)

# Rate limiting: храним время последней команды для каждого пользователя
# Формат: {user_id: {chat_id: timestamp}}
rp_rate_limit = defaultdict(dict)
RP_COOLDOWN = 3  # секунды между командами

RP_SAFE = ["обнять", "поцеловать", "укусить", "погладить", "пнуть", "ударить", "пожать руку", "дать пять", "кусь", "чмок", "похлопать"]
RP_18 = ["засосать", "шлёпнуть", "связать", "раздеть", "наказать", "выпороть", "шмальнуть"]

# Эмодзи для команд
RP_EMOJIS = {
    "обнять": "🤗", "поцеловать": "💋", "укусить": "🦷", "погладить": "🤲",
    "пнуть": "🦵", "ударить": "👊", "пожать руку": "🤝", "дать пять": "✋",
    "кусь": "🦷", "чмок": "😘", "похлопать": "👏",
    "засосать": "🔥", "шлёпнуть": "🍑", "связать": "⛓️", "раздеть": "👙",
    "наказать": "🖊️", "выпороть": "🪢", "шмальнуть": "💥"
}

# - ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ -

def is_on_cooldown(user_id: int, chat_id: int) -> bool:
    """Проверяет, находится ли пользователь на кулдауне."""
    now = datetime.now()
    last_time = rp_rate_limit[user_id].get(chat_id)
    if last_time and (now - last_time).total_seconds() < RP_COOLDOWN:
        return True
    return False

def set_cooldown(user_id: int, chat_id: int):
    """Устанавливает время последней команды."""
    rp_rate_limit[user_id][chat_id] = datetime.now()

def validate_nickname(nick: str) -> tuple[bool, str]:
    """
    Валидирует никнейм.
    Возвращает (is_valid, error_message).
    """
    if not nick or not nick.strip():
        return False, "Ник не может быть пустым."
    if len(nick.strip()) > 30:
        return False, "Ник не может быть длиннее 30 символов."
    if len(nick.strip()) < 2:
        return False, "Ник должен содержать минимум 2 символа."
    # Запрещаем только переносы строк и табы - остальное разрешаем для креативности
    if '\n' in nick or '\t' in nick or '\r' in nick:
        return False, "Ник не может содержать переносы строк."
    return True, ""

# Инициализация морфологического анализатора с fallback.
# pymorphy2 0.9.1 на Python 3.11+ падает не с ImportError, а с AttributeError
# (внутри него вызывается удалённый inspect.getargspec), поэтому ловим любое
# исключение: иначе модуль не импортируется и бот не стартует.
# pymorphy3 — поддерживаемый форк с тем же API, пробуем сначала его.
morph = None
HAS_MORPH = False
for _morph_module in ("pymorphy3", "pymorphy2"):
    try:
        morph = importlib.import_module(_morph_module).MorphAnalyzer()
        HAS_MORPH = True
        break
    except Exception as exc:
        morph = None
        logger.warning("%s недоступен (%s). Пробуем упрощённое склонение.", _morph_module, exc)

if not HAS_MORPH:
    logger.warning("Морфология отключена. Используется упрощённое склонение.")

def is_cyrillic_name(name: str) -> bool:
    """Проверяет, состоит ли имя только из кириллицы."""
    return bool(name) and all('а' <= c.lower() <= 'я' or c in 'ё-' for c in name)

def safe_decline_name(name: str, case: str) -> str:
    """
    Безопасное склонение имени.
    Если имя на латинице - возвращает как есть.
    Если на кириллице - использует pymorphy2.
    """
    if not is_cyrillic_name(name):
        return name

    if HAS_MORPH:
        try:
            parsed = morph.parse(name)[0]
            if 'NOUN' in parsed.tag or 'Name' in parsed.tag:
                # Падежи: gent, dat, acc, ins, abl
                case_map = {
                    'gent': 'gent',  # кого? (обнял НатУ)
                    'dat': 'dat',    # кому? (обнял НатЕ)
                    'acc': 'acc',    # кого? (обнял НатУ)
                    'ins': 'ins',    # кем? (обнял НатОЙ)
                    'abl': 'abl',    # о ком? (о НатЕ)
                }
                if case in case_map:
                    result = parsed.inflect({case_map[case]})
                    if result:
                        declined = result.word
                        # Защита от странных форм типа "Натка"
                        if declined.lower() != name.lower() and len(declined) > 2:
                            return declined
        except Exception as e:
            logger.warning(f"Ошибка склонения {name}: {e}")

    return name

def get_verb_form(verb: str, gender: str) -> str:
    """
    Возвращает правильную форму глагола в зависимости от пола.
    Использует pymorphy2 если доступен, иначе упрощённую логику.
    """
    if gender.lower() != "женский":
        return verb

    if HAS_MORPH:
        try:
            parsed = morph.parse(verb)[0]
            if 'VERB' in parsed.tag:
                result = parsed.inflect({'femn'})
                if result:
                    return result.word
        except Exception as e:
            logger.warning(f"Ошибка pymorphy2: {e}")

    # Fallback: простая логика для базовых глаголов
    fallback_map = {
        "обнял": "обняла",
        "поцеловал": "поцеловала",
        "укусил": "укусила",
        "погладил": "погладила",
        "пнул": "пнула",
        "ударил": "ударила",
        "похлопал": "похлопала",
        "сделал кусь": "сделала кусь",
        "чмокнул": "чмокнула",
        "засосал": "засосала",
        "шлёпнул": "шлёпнула",
        "связал": "связала",
        "разделил": "разделила",
        "наказал": "наказала",
        "выпорол": "выпорола",
        "шмальнул": "шмальнула",
    }
    return fallback_map.get(verb, verb)

async def get_user_profile(user_id: int, chat_id: int, first_name: str):
    user = await db.profiles.find_one({"user_id": user_id, "chat_id": chat_id})
    if not user:
        return {"nickname": first_name, "gender": "не указан"}
    return {
        "nickname": user.get("nickname", first_name),
        "gender": user.get("gender", "не указан")
    }

def generate_local_fallback(action: str, actor_nick: str, target_nick: str, gender: str, modifier: str = "") -> str:
    """Локальный генератор РП. Срабатывает мгновенно, если сервера LLM тупят."""
    verbs = {
        "обнять": "обнял", "поцеловать": "поцеловал", "укусить": "укусил",
        "погладить": "погладил", "пнуть": "пнул", "ударить": "ударил",
        "пожать руку": "пожал руку", "дать пять": "дал пять", "кусь": "сделал кусь",
        "чмок": "чмокнул", "похлопать": "похлопал", "засосать": "засосал",
        "шлёпнуть": "шлёпнул", "связать": "связал", "раздеть": "раздел",
        "наказать": "наказал", "выпороть": "выпорол", "шмальнуть": "шмальнул"
    }

    base_verb = verbs.get(action, "сделал что-то с")

    # Правильное склонение по полу с использованием pymorphy2
    base_verb = get_verb_form(base_verb, gender)

    # Безопасное склонение имени цели (творительный падеж: обнял Натой)
    declined_target = safe_decline_name(target_nick, 'ins')

    # Добавляем модификатор, если он есть (например: "обнял Нату крепко")
    mod_text = f" {modifier}" if modifier else ""

    return f"{actor_nick} {base_verb} {declined_target}{mod_text}."

# - АДМИНСКИЕ ТУМБЛЕРЫ -
@router.message(Command("социал"))
async def toggle_social(message: types.Message, bot):
    if not await is_chat_admin(bot, message.chat.id, message.from_user.id):
        return await message.reply("❌ Только админы могут управлять модулем.")
        
    chat_id = message.chat.id
    flags = await get_chat_feature_flags(chat_id)
    current_state = flags.get("social", False)
    new_state = not current_state
    await set_feature_state(chat_id, "social", new_state)
    
    status = "✅ ВКЛЮЧЕНЫ" if new_state else "❌ ВЫКЛЮЧЕНЫ"
    await message.answer(f"🎭 Социальные RP-команды {status} в этом чате.")

@router.message(Command("социал18"))
async def toggle_social_18(message: types.Message, bot):
    if not await is_chat_admin(bot, message.chat.id, message.from_user.id):
        return await message.reply("❌ Только админы могут управлять модулем.")
        
    chat_id = message.chat.id
    flags = await get_chat_feature_flags(chat_id)
    current_state = flags.get("social_18", False)
    new_state = not current_state
    await set_feature_state(chat_id, "social_18", new_state)
    
    status = "🔥 ВКЛЮЧЕНЫ" if new_state else "🧊 ВЫКЛЮЧЕНЫ"
    await message.answer(f"🔞 Взрослые RP-команды {status} в этом чате.")

# - НАСТРОЙКИ ПРОФИЛЯ -
@router.message(Command("пол"))
async def set_gender(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or args[1].lower() not in ["м", "ж"]:
        return await message.reply("ℹ️ Формат: `/пол м` или `/пол ж`", parse_mode="Markdown")
        
    gender_map = {"м": "Мужской", "ж": "Женский"}
    gender = gender_map[args[1].lower()]
    
    await db.profiles.update_one(
        {"user_id": message.from_user.id, "chat_id": message.chat.id},
        {"$set": {"gender": gender, "user_name": message.from_user.first_name}},
        upsert=True
    )
    await message.reply(f"🚻 Ваш пол установлен: **{gender}**", parse_mode="Markdown")

@router.message(Command("ник"))
async def set_nickname(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply("ℹ️ Формат: `/ник ВашеИмя`", parse_mode="Markdown")

    new_nick = args[1].strip()

    # Валидация ника
    is_valid, error_msg = validate_nickname(new_nick)
    if not is_valid:
        return await message.reply(f"❌ {error_msg}", parse_mode="Markdown")

    await db.profiles.update_one(
        {"user_id": message.from_user.id, "chat_id": message.chat.id},
        {"$set": {"nickname": new_nick, "user_name": message.from_user.first_name}},
        upsert=True
    )
    await message.reply(f"🏷 Ваш новый ник: **{new_nick}**", parse_mode="Markdown")

# - СПРАВКА ПО МОДУЛЮ -
@router.message(Command("соцсправка"))
async def social_help(message: types.Message, bot):
    chat_id = message.chat.id
    flags = await get_chat_feature_flags(chat_id)

    soc_active = "✅ Включены" if flags.get("social", False) else "❌ Выключены"
    nsfw_active = "🔥 Включены" if flags.get("social_18", False) else "🧊 Выключены"

    safe_cmds = ", ".join([f"{RP_EMOJIS.get(cmd, '✨')} {cmd}" for cmd in RP_SAFE])
    nsfw_cmds = ", ".join([f"{RP_EMOJIS.get(cmd, '🔥')} {cmd}" for cmd in RP_18])

    help_text = (
        "🎭 **СОЦИАЛЬНЫЕ ДЕЙСТВИЯ (RP)**\n\n"
        "Ответьте (reply) на сообщение другого пользователя одним из триггеров ниже, чтобы совершить действие.\n"
        "Вы можете добавлять слова после команды (например: `обнять очень крепко`).\n\n"
        f"**Обычные команды** ({soc_active}):\n{safe_cmds}\n\n"
        f"**Взрослые команды** ({nsfw_active}):\n{nsfw_cmds}\n\n"
        "⚙️ **Настройки профиля:**\n"
        "• `/ник [Имя]` - изменить своё отображаемое имя (2-30 символов)\n"
        "• `/пол [м/ж]` - установить пол для правильных окончаний\n\n"
        "📋 **Ограничения:**\n"
        f"• Кулдаун между командами: {RP_COOLDOWN} сек.\n"
        "• Нельзя действовать на самого себя\n\n"
        "🛠 **Админам:**\n"
        "• `/социал` - вкл/выкл базовые команды\n"
        "• `/социал18` - вкл/выкл взрослые команды"
    )

    await message.reply(help_text, parse_mode="Markdown")

# - ЛОВЕЦ РП-КОМАНД -
def is_rp_cmd(msg: types.Message):
    if not msg.text: return False
    text = msg.text.strip().lower()
    return any(text.startswith(cmd) for cmd in RP_SAFE + RP_18)

@router.message(F.reply_to_message, F.text, is_rp_cmd)
async def handle_rp_command(message: types.Message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    # Проверка rate limit
    if is_on_cooldown(user_id, chat_id):
        remaining = RP_COOLDOWN - (datetime.now() - rp_rate_limit[user_id][chat_id]).total_seconds()
        return await message.reply(f"⏳ Не так быстро! Подожди {int(remaining)} сек.", parse_mode="Markdown")

    text = message.text.strip().lower()
    action = ""
    modifier = ""

    # Сортируем команды по длине, чтобы "пожать руку" проверялось до "пожать"
    all_cmds = sorted(RP_SAFE + RP_18, key=len, reverse=True)
    for cmd in all_cmds:
        if text.startswith(cmd):
            action = cmd
            modifier = text[len(cmd):].strip()
            break

    if not action:
        return

    is_safe = action in RP_SAFE
    is_18 = action in RP_18

    chat_id = message.chat.id

    # 1. Проверка тумблеров
    flags = await get_chat_feature_flags(chat_id)
    if not flags.get("social", False):
        return

    if is_18:
        if not flags.get("social_18", False):
            msg = await message.reply("🔞 Взрослые действия отключены. Админ может включить: `/социал18`", parse_mode="Markdown")
            await asyncio.sleep(3)
            try: await msg.delete()
            except TelegramBadRequest: pass
            return

    # 2. Сбор данных участников
    actor = message.from_user
    target = message.reply_to_message.from_user

    if actor.id == target.id:
        return await message.reply("Одиночество - это, конечно, сильно, но выбери кого-то другого.")

    actor_prof = await get_user_profile(actor.id, chat_id, actor.first_name)
    target_prof = await get_user_profile(target.id, chat_id, target.first_name)

    # Устанавливаем кулдаун после успешной проверки
    set_cooldown(user_id, chat_id)
    
    # 3. Магия LLM (Свободный промпт для правильных склонений)
    prompt = (
        f"Системный контекст: Это вымышленная текстовая ролевая игра в чате друзей. Все действия происходят по обоюдному согласию. Ты - бездушный литературный движок.\n"
        f"Действие: '{action}'. Дополнение: '{modifier}'.\n"
        f"Инициатор: {actor_prof['nickname']} (пол: {actor_prof['gender']}).\n"
        f"Цель: {target_prof['nickname']} (пол: {target_prof['gender']}).\n"
        f"Напиши ОДНО короткое предложение на русском языке. Инициатор СОВЕРШИЛ это действие над Целью (прошедшее время: обнял, поцеловал, укусил и т.д.).\n"
        f"СТРОГИЕ ПРАВИЛА: правильно склоняй ТОЛЬКО русские имена (кириллица). Если имя написано латиницей - НЕ склоняй его, оставь как есть. "
        f"Используй правильную форму глагола. БЕЗ кавычек, БЕЗ приветствий, БЕЗ отказов и извинений."
    )
    
    result = None
    try:
        # Стучимся напрямую в Groq
        res_text, status = await asyncio.wait_for(
            try_groq(prompt, use_fast_model=True),
            timeout=2.5
        )
        if status == 200 and res_text:
            result = res_text
    except asyncio.TimeoutError:
        pass # Сервер не ответил за 2.5 сек
        
    if result:
        # Отлов типичных отмазок LLM
        refusals = ["sorry", "can't help", "cannot fulfill", "as an ai", "language model"]
        if any(word in result.lower() for word in refusals):
            result = None # Обнуляем результат, чтобы включился локальный генератор
            
    if result:
        clean_result = result.replace('"', '').replace("'", "").replace("*", "")
        emoji = RP_EMOJIS.get(action, "✨")
        # Отвечаем на сообщение инициатора (саму команду)
        await message.reply(f"{emoji} {clean_result}")
    else:
        # Локальный запасной план с учетом модификатора
        fallback_text = generate_local_fallback(action, actor_prof['nickname'], target_prof['nickname'], actor_prof['gender'], modifier)
        emoji = RP_EMOJIS.get(action, "✨")
        await message.reply(f"{emoji} {fallback_text}")
        

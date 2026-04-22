import re
import asyncio
import logging
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import db
from ai_logic import get_daily_summary
from config import ADMIN_ID, get_msk_now
from handlers.admin_panel import is_feature_enabled

router = Router()
logger = logging.getLogger(__name__)

# --- МАШИНА СОСТОЯНИЙ ДЛЯ ЛС ---
class CronAdminState(StatesGroup):
    waiting_for_time_set = State()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
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

async def get_known_groups(bot):
    """Собирает уникальные активные группы из истории сообщений бота"""
    chat_ids = await db.messages.distinct("chat_id")
    valid_groups = {}
    
    for cid in chat_ids:
        # Группы и супергруппы в Telegram всегда имеют отрицательный ID
        if cid < 0:
            try:
                chat = await bot.get_chat(cid)
                valid_groups[cid] = chat.title or f"Группа {cid}"
            except (TelegramBadRequest, TelegramForbiddenError):
                # Бот был удален из чата или нет доступа
                pass
    return valid_groups

# --- ГЛАВНОЕ МЕНЮ В ЛС / КОМАНДА В ГРУППЕ ---
@router.message(Command("set_daily", "cron"))
async def cmd_set_daily(message: types.Message, bot, state: FSMContext):
    is_pm = message.chat.type == "private"

    if is_pm:
        if message.from_user.id != ADMIN_ID:
            return await message.reply("❌ Эта команда в ЛС доступна только владельцу бота.")
        
        await state.clear()
        builder = InlineKeyboardBuilder()
        builder.button(text="➕ Назначить расписание", callback_data="cron_menu_set")
        builder.button(text="⏸ Отключить расписание", callback_data="cron_menu_stop")
        builder.button(text="📊 Статус всех отчетов", callback_data="cron_status")
        builder.adjust(1)

        await message.answer(
            "⚙️ **Управление ежедневными отчетами**\nВыберите действие:", 
            reply_markup=builder.as_markup(), 
            parse_mode="Markdown"
        )
    else:
        # Старая текстовая логика для групп
        args = message.text.split()
        if not await is_chat_admin(bot, message.chat.id, message.from_user.id):
            return await message.reply("❌ Настраивать отчеты могут только администраторы группы.")
            
        if len(args) != 2:
            return await message.answer(
                "ℹ️ **Формат:**\n`/set_daily <HH:MM>`\n*Пример:* `/set_daily 09:30`\n\n*(Время указывается по МСК)*", 
                parse_mode="Markdown"
            )
        time_str = args[1]
        
        if not re.match(r"^([01]\d|2[0-3]):([0-5]\d)$", time_str):
            return await message.answer("❌ Неверный формат времени. Используйте ЧЧ:ММ (например, 09:05, 23:30).")

        await db.settings.update_one(
            {"setting": "daily_report", "chat_id": message.chat.id},
            {"$set": {"time": time_str, "is_active": True, "set_by": message.from_user.id, "updated_at": get_msk_now()}},
            upsert=True
        )
        await message.answer(f"✅ Ежедневный отчет для этого чата успешно запланирован на **{time_str} (МСК)**.", parse_mode="Markdown")

# --- INLINE: НАЗНАЧЕНИЕ РАСПИСАНИЯ ---
@router.callback_query(F.data == "cron_menu_set")
async def cb_cron_menu_set(callback: types.CallbackQuery, bot):
    await callback.answer("Сканирую базу чатов...", show_alert=False)
    groups = await get_known_groups(bot)
    
    if not groups:
        return await callback.message.edit_text("🤷‍♂️ Я пока не состою ни в одной группе, либо в базе нет сообщений.")

    builder = InlineKeyboardBuilder()
    for cid, title in groups.items():
        # Передаем ID чата прямо в callback_data (лимит 64 байта, влезет легко)
        builder.button(text=f"📌 {title}", callback_data=f"c_set:{cid}")
    
    builder.button(text="🔙 Назад", callback_data="cron_back")
    builder.adjust(1)

    await callback.message.edit_text(
        "Выберите группу, для которой нужно **назначить** расписание:", 
        reply_markup=builder.as_markup()
    )

@router.callback_query(F.data.startswith("c_set:"))
async def cb_cron_select_set(callback: types.CallbackQuery, state: FSMContext):
    chat_id = int(callback.data.split(":")[1])
    await state.update_data(target_chat_id=chat_id)
    
    await callback.message.edit_text(
        "⏰ Отлично. Теперь отправьте в чат **время (по МСК)** в формате ЧЧ:ММ (например, 21:00):", 
        parse_mode="Markdown"
    )
    await state.set_state(CronAdminState.waiting_for_time_set)

@router.message(CronAdminState.waiting_for_time_set)
async def process_time_set(message: types.Message, bot, state: FSMContext):
    time_str = message.text.strip()
    if not re.match(r"^([01]\d|2[0-3]):([0-5]\d)$", time_str):
        return await message.answer("❌ Неверный формат. Используйте ЧЧ:ММ. Попробуйте еще раз:")
        
    data = await state.get_data()
    target_chat_id = data['target_chat_id']
    
    chat_title = str(target_chat_id)
    try:
        chat_info = await bot.get_chat(target_chat_id)
        chat_title = chat_info.title
    except Exception:
        pass

    await db.settings.update_one(
        {"setting": "daily_report", "chat_id": target_chat_id},
        {"$set": {"time": time_str, "is_active": True, "set_by": message.from_user.id, "updated_at": get_msk_now()}},
        upsert=True
    )
    
    await message.answer(f"✅ Отчет для группы **{chat_title}** запланирован на **{time_str} (МСК)**.", parse_mode="Markdown")
    await state.clear()

# --- INLINE: ОТКЛЮЧЕНИЕ РАСПИСАНИЯ ---
@router.callback_query(F.data == "cron_menu_stop")
async def cb_cron_menu_stop(callback: types.CallbackQuery, bot):
    # Ищем только те чаты, где УЖЕ есть активное расписание
    cursor = db.settings.find({"setting": "daily_report", "is_active": True})
    active_reports = await cursor.to_list(length=100)
    
    if not active_reports:
        return await callback.message.edit_text("📭 На данный момент нет активных ежедневных отчетов для отключения.")

    builder = InlineKeyboardBuilder()
    for rep in active_reports:
        cid = rep.get("chat_id")
        title = str(cid)
        try:
            chat_info = await bot.get_chat(cid)
            title = chat_info.title
        except Exception:
            pass
            
        builder.button(text=f"❌ {title} ({rep.get('time')})", callback_data=f"c_stop:{cid}")
        
    builder.button(text="🔙 Назад", callback_data="cron_back")
    builder.adjust(1)

    await callback.message.edit_text(
        "Выберите группу, для которой нужно **отключить** расписание:", 
        reply_markup=builder.as_markup()
    )

@router.callback_query(F.data.startswith("c_stop:"))
async def cb_cron_select_stop(callback: types.CallbackQuery, bot):
    chat_id = int(callback.data.split(":")[1])
    
    await db.settings.update_one(
        {"setting": "daily_report", "chat_id": chat_id},
        {"$set": {"is_active": False}}
    )
    
    chat_title = str(chat_id)
    try:
        chat_info = await bot.get_chat(chat_id)
        chat_title = chat_info.title
    except Exception:
        pass

    await callback.message.edit_text(f"⏸ Расписание для группы **{chat_title}** остановлено.", parse_mode="Markdown")

# --- СТАТУС И ВОЗВРАТ ---
@router.callback_query(F.data == "cron_status")
async def cb_cron_status(callback: types.CallbackQuery, bot):
    cursor = db.settings.find({"setting": "daily_report", "is_active": True})
    active_reports = await cursor.to_list(length=100)
    
    if not active_reports:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Назад", callback_data="cron_back")
        return await callback.message.edit_text("📭 На данный момент нет активных расписаний.", reply_markup=builder.as_markup())
        
    lines = ["📊 **АКТИВНЫЕ РАСПИСАНИЯ (МСК):**\n"]
    for rep in active_reports:
        cid = rep.get('chat_id')
        title = str(cid)
        try:
            chat_info = await bot.get_chat(cid)
            title = chat_info.title
        except Exception:
            pass
        lines.append(f"• **{title}** ⏰ {rep.get('time')}")
        
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="cron_back")
    await callback.message.edit_text("\n".join(lines), parse_mode="Markdown", reply_markup=builder.as_markup())

@router.callback_query(F.data == "cron_back")
async def cb_cron_back(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Назначить расписание", callback_data="cron_menu_set")
    builder.button(text="⏸ Отключить расписание", callback_data="cron_menu_stop")
    builder.button(text="📊 Статус всех отчетов", callback_data="cron_status")
    builder.adjust(1)
    
    await callback.message.edit_text(
        "⚙️ **Управление ежедневными отчетами**\nВыберите действие:", 
        reply_markup=builder.as_markup(), 
        parse_mode="Markdown"
    )

# --- ОТКЛЮЧЕНИЕ ЧЕРЕЗ КОМАНДУ (Для групп) ---
@router.message(Command("stop_daily"))
async def cmd_stop_daily_group(message: types.Message, bot):
    if message.chat.type == "private":
        return await message.answer("ℹ️ В ЛС используйте меню /cron")
        
    if not await is_chat_admin(bot, message.chat.id, message.from_user.id):
        return await message.reply("❌ Отключать отчеты могут только администраторы.")
        
    result = await db.settings.update_one(
        {"setting": "daily_report", "chat_id": message.chat.id},
        {"$set": {"is_active": False}}
    )

    if result.modified_count > 0:
        await message.answer(f"⏸ Ежедневный отчет для этого чата отключен.", parse_mode="Markdown")
    else:
        await message.answer("⚠️ Для этого чата нет активных отчетов.")

# --- ЛОГИКА CRON (Вызывается из main.py) ---
async def check_all_chats(bot, now):
    msk_now = get_msk_now()
    current_time_str = msk_now.strftime("%H:%M")
    
    cursor = db.settings.find({
        "setting": "daily_report",
        "is_active": True,
        "time": current_time_str
    })
    
    settings_list = await cursor.to_list(length=100)
    for setting in settings_list:
        chat_id = setting.get("chat_id")
        if chat_id:
            if not await is_feature_enabled(chat_id, "daily_reports"):
                continue
            asyncio.create_task(run_specific_daily_job(bot, chat_id))

async def run_specific_daily_job(bot, chat_id):
    logger.info(f"Запускаю расписание ежедневного отчета для {chat_id}!")
    try:
        await bot.send_message(chat_id, "📻 *Настраиваю антенну... Собираю сплетни за прошедшие сутки.*", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Не могу написать в чат {chat_id}. Ошибка: {e}")
        if "chat not found" in str(e).lower() or "bot was kicked" in str(e).lower() or "forbidden" in str(e).lower():
             await db.settings.update_one({"setting": "daily_report", "chat_id": chat_id}, {"$set": {"is_active": False}})
        return

    report = await get_daily_summary(chat_id)
    
    if report:
        try:
            sent_msg = await bot.send_message(chat_id, f"📰 **ГЛАВНОЕ ЗА 24 ЧАСА**\n{'-'*20}\n{report}", parse_mode="Markdown")
            await bot.pin_chat_message(chat_id, sent_msg.message_id, disable_notification=False)
        except TelegramBadRequest as e:
            if "can't parse entities" in str(e).lower():
                sent_msg = await bot.send_message(chat_id, f"📰 ГЛАВНОЕ ЗА 24 ЧАСА\n{'-'*20}\n{report}")
                try:
                    await bot.pin_chat_message(chat_id, sent_msg.message_id)
                except Exception as pin_e:
                    logger.error(f"Не удалось закрепить (без markdown): {pin_e}")
            else:
                logger.error(f"Ошибка отправки/закрепа в {chat_id}: {e}")
                

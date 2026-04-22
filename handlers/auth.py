import logging
from aiogram import Router, types, F, Bot, BaseMiddleware
from aiogram.filters import Command, or_f
from aiogram.filters.chat_member_updated import ChatMemberUpdatedFilter, IS_NOT_MEMBER, MEMBER, ADMINISTRATOR
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database import db
from config import ADMIN_ID

router = Router()
logger = logging.getLogger(__name__)

# --- MIDDLEWARE: ЗАЩИТА ---
class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: types.Message, data: dict):
        # Пропускаем ЛС и системные события
        if not isinstance(event, types.Message) or not event.chat or event.chat.type == "private":
            return await handler(event, data)

        chat_id = event.chat.id
        doc = await db.auth_chats.find_one({"chat_id": chat_id})

        # Если чат одобрен
        if doc and doc.get("status") == "approved":
            return await handler(event, data)

        # Если не одобрен и введена команда
        if event.text and event.text.startswith('/'):
            builder = InlineKeyboardBuilder()
            builder.button(text="📩 Запросить доступ", callback_data=f"req_acc:{chat_id}")
            try:
                await event.bot.send_message(
                    chat_id,
                    "⛔ **Доступ ограничен**\n\nБот не авторизован для работы в этой группе. Напиши @BoozyGrimm.",
                    reply_markup=builder.as_markup(),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Ошибка отправки заглушки: {e}")
        return # Блокируем дальнейшее выполнение

# --- СОБЫТИЕ: ДОБАВЛЕНИЕ В ЧАТ ---
@router.my_chat_member(
    or_f(
        ChatMemberUpdatedFilter(member_status_changed=IS_NOT_MEMBER >> MEMBER),
        ChatMemberUpdatedFilter(member_status_changed=IS_NOT_MEMBER >> ADMINISTRATOR)
    )
)
async def bot_added_to_chat(event: types.ChatMemberUpdated):
    chat_id = event.chat.id
    chat_title = event.chat.title or "Неизвестный чат"
    
    # Сразу регистрируем чат в базе
    await db.auth_chats.update_one(
        {"chat_id": chat_id},
        {"$set": {"status": "pending", "title": chat_title}},
        upsert=True
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="📩 Отправить запрос", callback_data=f"req_acc:{chat_id}")

    try:
        await event.bot.send_message(
            chat_id,
            f"👋 Привет! Я — Эксперт. Чтобы я начал работать в «{chat_title}», @BoozyGrimm должен подтвердить доступ, напиши ему.",
            reply_markup=builder.as_markup()
        )
    except Exception: pass

# --- АДМИН-МЕНЮ /chats ---
@router.message(Command("chats"))
async def admin_chats_menu(message: types.Message):
    if message.from_user.id != ADMIN_ID: return

    # ВАЖНО: Восстановлена подтяжка старых чатов, чтобы они не отвалились!
    old_ids = await db.messages.distinct("chat_id")
    for cid in old_ids:
        if cid < 0:
            await db.auth_chats.update_one(
                {"chat_id": cid},
                {"$setOnInsert": {"status": "pending", "title": f"Старый чат {cid}"}},
                upsert=True
            )

    chats = await db.auth_chats.find().to_list(length=100)
    if not chats:
        return await message.answer("Список чатов пуст.")

    builder = InlineKeyboardBuilder()
    for c in chats:
        status = "🟢" if c.get("status") == "approved" else ("🔴" if c.get("status") == "denied" else "🟡")
        chat_name = c.get("title") or "Неизвестный чат"
        display_name = (chat_name[:25] + '..') if len(chat_name) > 25 else chat_name
        
        builder.button(text=f"{status} {display_name}", callback_data=f"manage_chat:{c['chat_id']}")
    
    builder.adjust(1)
    await message.answer("📂 **Управление доступом чатов:**", reply_markup=builder.as_markup())

# --- ДЕЙСТВИЯ (ЗАПРОС / ПОДТВЕРДИТЬ / ОТКЛОНИТЬ) ---
@router.callback_query(F.data.startswith("req_acc:"))
async def request_access(callback: types.CallbackQuery, bot: Bot):
    chat_id = int(callback.data.split(":")[1])
    chat_title = callback.message.chat.title if callback.message.chat else "Неизвестный чат"
    
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Разрешить", callback_data=f"auth_allow:{chat_id}")
    builder.button(text="❌ Отказать", callback_data=f"auth_deny:{chat_id}")
    
    await bot.send_message(
        ADMIN_ID, 
        f"🚨 **Запрос доступа!**\nЧат: {chat_title}\nID: `{chat_id}`", 
        reply_markup=builder.as_markup()
    )
    await callback.answer("Запрос отправлен владельцу.")

@router.callback_query(F.data.startswith("manage_chat:"))
async def manage_chat_item(callback: types.CallbackQuery):
    chat_id = int(callback.data.split(":")[1])
    doc = await db.auth_chats.find_one({"chat_id": chat_id})
    
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Разрешить", callback_data=f"auth_allow:{chat_id}")
    builder.button(text="❌ Отключить", callback_data=f"auth_deny:{chat_id}")
    builder.button(text="🔙 Назад", callback_data="back_to_list")
    builder.adjust(2, 1)

    chat_title = doc.get('title') or "Неизвестный чат"
    await callback.message.edit_text(
        f"⚙️ **Чат:** {chat_title}\n**ID:** `{chat_id}`\n**Статус:** {doc.get('status')}",
        reply_markup=builder.as_markup()
    )

@router.callback_query(F.data == "back_to_list")
async def back_to_list(callback: types.CallbackQuery):
    await admin_chats_menu(callback.message)
    await callback.message.delete()

@router.callback_query(F.data.startswith("auth_allow:"))
async def approve_access(callback: types.CallbackQuery, bot: Bot):
    if callback.from_user.id != ADMIN_ID: return # Защита от чужих нажатий
    
    chat_id = int(callback.data.split(":")[1])
    await db.auth_chats.update_one({"chat_id": chat_id}, {"$set": {"status": "approved"}})
    await callback.answer("Готово!")
    await callback.message.edit_text(f"✅ Чат `{chat_id}` теперь авторизован.")
    try: await bot.send_message(chat_id, "🎉 Доступ разрешен! Бот готов к работе.")
    except Exception: pass

@router.callback_query(F.data.startswith("auth_deny:"))
async def deny_access(callback: types.CallbackQuery, bot: Bot):
    if callback.from_user.id != ADMIN_ID: return # Защита от чужих нажатий
    
    chat_id = int(callback.data.split(":")[1])
    await db.auth_chats.update_one({"chat_id": chat_id}, {"$set": {"status": "denied"}})
    await callback.answer("Заблокировано.")
    await callback.message.edit_text(f"🔴 Чат `{chat_id}` заблокирован. Бот выйдет из него.")
    try: 
        await bot.send_message(chat_id, "❌ Владелец отклонил доступ. Прощайте!")
        await bot.leave_chat(chat_id)
    except Exception: pass
    
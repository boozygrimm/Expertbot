"""Guided owner actions and explicit, one-use confirmations in private chat."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio
import logging
import secrets
from types import SimpleNamespace

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import ADMIN_ID, get_msk_now
from database import db
from news_prototype import owner_drafts, owner_trial

router = Router()
logger = logging.getLogger(__name__)
# Only one owner private chat uses these forms; serialize opening/cancelling them.
_input_lock = asyncio.Lock()


class OwnerInput(StatesGroup):
    waiting = State()


def authorized(user, chat) -> bool:
    return bool(user and user.id == ADMIN_ID and chat and chat.type == 'private' and chat.id == ADMIN_ID)


def proxy(message, user, *, text=None):
    """Pass actual owner identity and the real incoming/message ID to old handlers."""
    return SimpleNamespace(from_user=user, chat=message.chat, message_id=message.message_id,
        text=text, panel_mode=True, answer=message.answer, reply=getattr(message, 'reply', message.answer),
        answer_photo=getattr(message, 'answer_photo', None), reply_to_message=getattr(message, 'reply_to_message', None))


def menu(title, buttons):
    builder = InlineKeyboardBuilder()
    for text, action in buttons:
        builder.button(text=text, callback_data=action)
    builder.button(text='⬅️ Пульт', callback_data='owner_panel:home')
    builder.adjust(1)
    return title, builder.as_markup()


async def new_confirmation(message, owner_id: int, action: str, text: str, **data):
    token = secrets.token_hex(8)
    now = datetime.now(timezone.utc)
    await db.db.owner_panel_confirmations.delete_many({'expires_at': {'$lt': now}})
    await db.db.owner_panel_confirmations.insert_one({'_id': token, 'owner_id': owner_id,
        'action': action, 'data': data, 'stage': 'ready', 'expires_at': now + timedelta(minutes=15)})
    builder = InlineKeyboardBuilder()
    builder.button(text='✅ Подтвердить', callback_data=f'owner_tools:run:{token}')
    builder.button(text='❌ Отмена', callback_data=f'owner_tools:cancel:{token}')
    builder.adjust(1)
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode=None)


async def ask(callback, state: FSMContext, action: str, prompt: str, **data):
    token = secrets.token_hex(8)
    async with _input_lock:
        await state.clear()
        await state.set_state(OwnerInput.waiting)
        await state.update_data(action=action, input_token=token, **data)
    builder = InlineKeyboardBuilder()
    builder.button(text='❌ Отменить ввод', callback_data=f'owner_tools:cancel_input:{token}')
    await callback.answer()
    await callback.message.answer(prompt, reply_markup=builder.as_markup(), parse_mode=None)


async def profile_choice(callback, state, action_name: str, size: str = ''):
    # IDs are included alongside names to disambiguate equal display names.
    players = await db.db.alchemy_players.find({}, {'user_id': 1, 'user_name': 1}).sort('user_id', -1).limit(20).to_list(length=20)
    b = InlineKeyboardBuilder()
    code = size if action_name == 'pack' else action_name
    for player in players:
        number = player.get('user_id')
        if type(number) is not int or number <= 0:
            continue
        name = str(player.get('user_name') or 'Игрок')[:40]
        b.button(text=f'{name} · {number}', callback_data=f'owner_tools:profile:{code}:{number}')
    b.button(text='⌨️ Ввести ID вручную', callback_data=f'owner_tools:profileinput:{code}')
    b.button(text='⬅️ Пульт', callback_data='owner_panel:home')
    b.adjust(1)
    await state.clear()
    await callback.answer()
    label = f'промопак {size.upper()}' if action_name == 'pack' else ('удалить профиль' if action_name == 'reset' else 'очистить инвентарь')
    await callback.message.answer(f'Выбери игрока ({label}). Показаны до 20 профилей; можно ввести ID вручную. '
        'Профиль алхимии общий для всех групп. Далее потребуется подтверждение.',
        reply_markup=b.as_markup(), parse_mode=None)


@router.message(OwnerInput.waiting, F.text, ~F.text.regexp(r'^/(?:panel|панель)(?:@\w+)?(?:\s|$)'))
async def receive_input(message: types.Message, state: FSMContext):
    if not authorized(message.from_user, message.chat):
        return
    data = await state.get_data()
    action = data.get('action')
    text = (message.text or '').strip()
    if not text:
        return await message.answer('Нужно текстовое сообщение. Можно отменить ввод кнопкой.', parse_mode=None)
    try:
        if action == 'prepare':
            # Accept one or several links, plus an optional accent line.
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            count = len(lines) - int(bool(lines and lines[-1].casefold().startswith('акцент:')))
            if count == 1:
                content = '/news_prepare ' + lines[0] + ('\n' + lines[-1] if len(lines) > 1 else '')
                owner_drafts.prepare_input(content)
                handler_name = 'prepare'
            else:
                content = '/news_prepare_many\n' + '\n'.join(lines)
                owner_drafts.prepare_many_input(content)
                handler_name = 'prepare_many'
            await state.clear()
            from handlers import news_owner_drafts
            await getattr(news_owner_drafts, handler_name)(proxy(message, message.from_user, text=content))
        elif action == 'legacy_news':
            from handlers import owner_legacy_news, news_intake
            from news_prototype.manual_news import make_candidate
            if not owner_legacy_news.enabled():
                await state.clear()
                return await message.answer('Дополнительный режим выключен.', parse_mode=None)
            reply = getattr(message, 'reply_to_message', None)
            reply_user = getattr(reply, 'from_user', None) if reply else None
            reply_text = (getattr(reply, 'text', None) or getattr(reply, 'caption', None)) if reply and not getattr(reply_user, 'is_bot', False) else None
            if reply and not reply_text and not getattr(reply_user, 'is_bot', False):
                raise ValueError('В ответе нужен текст новости')
            command_text = '/news ' + text
            body, accent, url = news_intake.parse_input(command_text, reply_text)
            candidate = make_candidate(user_id=message.from_user.id, admin_id=ADMIN_ID, chat_type='private',
                message_id=message.message_id, text=body, accent=accent, url=url)
            summary = ' '.join((candidate['text'] or candidate['url']).split())[:300]
            source_preview = candidate['url'][:500] if candidate['text'] and candidate['url'] else ''
            accent_preview = ' '.join(candidate['accent'].split())[:200]
            await state.clear()
            await new_confirmation(message, message.from_user.id, 'legacy_news',
                'Сохранить непроверенный материал?\n' + summary +
                (('\nИсточник: ' + source_preview) if source_preview else '') +
                (('\nАкцент: ' + accent_preview) if accent_preview else '') +
                '\n\n' + owner_legacy_news.mode_notice(),
                command_text=command_text, reply_text=reply_text, original_message_id=message.message_id,
                mode_flags=owner_legacy_news.flag_snapshot())
        elif action == 'draw':
            if len(text) > 500:
                raise ValueError('Слишком длинный запрос')
            await state.clear()
            await new_confirmation(message, message.from_user.id, 'draw',
                f'Нарисовать по запросу «{text}»? Будут действовать обычные бесплатные попытки и стоимость в очках.', query=text)
        elif action == 'search_query':
            if len(text) > 500:
                raise ValueError('Слишком длинный запрос')
            await state.clear()
            await new_confirmation(message, message.from_user.id, 'search_probe',
                f'Проверить поиск картинки по запросу «{text}»? Это может расходовать квоту.', query=text)
        elif action == 'trial':
            content = '/news_trial\n' + text
            owner_trial.parse_message(content)
            await state.clear()
            await new_confirmation(message, message.from_user.id, 'trial',
                'Поставить этот проверенный сценарий на рендер? Видео придёт в личку, публикации не будет.',
                content=content)
        elif action == 'bulletin_source':
            if not await owner_trial.drafts_enabled(db.db):
                await state.clear()
                return await message.answer('Подготовка сценариев выключена.', parse_mode=None)
            from handlers import owner_bulletin_panel
            from news_prototype import owner_bulletin, owner_bulletin_sources
            result = await owner_bulletin_sources.provide_source(db.db, message.from_user.id, data['number'],
                data['section_id'], data['source_revision'], text)
            await state.clear()
            if result in ('new', 'source_missing'):
                item = await owner_bulletin.get(db.db, message.from_user.id, data['number'])
                await message.answer('Текст сохранён; редактор пока не запускался.', parse_mode=None)
                await owner_bulletin_panel.show_sources(message, item)
            else:
                await message.answer('Источник изменился. Открой вестник заново.', parse_mode=None)
        elif action == 'bulletin_video_remarks':
            if not await owner_trial.drafts_enabled(db.db):
                await state.clear()
                return await message.answer('Подготовка сценариев выключена.', parse_mode=None)
            from handlers import owner_bulletin_panel
            from news_prototype import owner_bulletin
            result = await owner_bulletin.change_video_remarks(db.db, message.from_user.id, data['number'],
                data['revision'], data['section_id'], text)
            await state.clear()
            if result in ('saved', 'unchanged'):
                item = await owner_bulletin.get(db.db, message.from_user.id, data['number'])
                await message.answer('Реплики сохранены; проверь новую версию и выбери видео заново.' if result == 'saved'
                                     else 'Изменений нет.', parse_mode=None)
                section = next(s for s in item['sections'] if s['id'] == data['section_id'])
                await owner_bulletin_panel.show_section(message, message.from_user.id, item, section)
            else:
                await message.answer('Версия или источник изменились. Открой вестник заново.', parse_mode=None)
        elif action == 'bulletin_edit':
            if not await owner_trial.drafts_enabled(db.db):
                await state.clear()
                return await message.answer('Подготовка сценариев выключена.', parse_mode=None)
            from handlers import owner_bulletin_panel
            from news_prototype import owner_bulletin
            owner_bulletin.parse_section_edit(text)
            result = await owner_bulletin.change_section(db.db, message.from_user.id, data['number'],
                data['revision'], data['section_id'], text=text)
            await state.clear()
            if result in ('saved', 'unchanged'):
                item = await owner_bulletin.get(db.db, message.from_user.id, data['number'])
                await message.answer('Изменения сохранены; проверь новую версию. Остальные новости открой из списка вестников.' if result == 'saved' else
                                     'Изменений нет.', parse_mode=None)
                section = next(s for s in item['sections'] if s['id'] == data['section_id'])
                await owner_bulletin_panel.show_section(message, message.from_user.id, item, section)
            else:
                await message.answer('Версия или источник изменились. Открой вестник заново.', parse_mode=None)
        elif action in ('news_source', 'news_edit'):
            if not await owner_trial.drafts_enabled(db.db):
                await state.clear()
                return await message.answer('Подготовка сценариев выключена.', parse_mode=None)
            if action == 'news_source':
                if len(text) > 8000:
                    raise ValueError('Слишком длинный текст')
                content = f'/news_source {data["number"]}\n{text}'
                handler = 'provide_source'
            else:
                content = f'/news_edit {data["number"]} {data["revision"]}\n{text}'
                owner_drafts.parse_edit(content)
                handler = 'edit_draft'
            await state.clear()
            from handlers import news_owner_drafts
            await getattr(news_owner_drafts, handler)(proxy(message, message.from_user, text=content))
        elif action == 'recipe':
            await state.clear()
            from handlers.alc_lab import cmd_recipe_cheatsheet
            await cmd_recipe_cheatsheet(proxy(message, message.from_user, text=text), SimpleNamespace(args=text))
        elif action in ('pack', 'clear'):
            if not text.isdecimal() or not 1 <= len(text) <= 15 or int(text) < 1:
                raise ValueError('Нужен ID игрока')
            player_id = int(text)
            player = await db.db.alchemy_players.find_one({'user_id': player_id})
            if not player:
                if action == 'pack':
                    await state.update_data(action='pack_new_name', player_id=player_id, size=data['size'])
                    await message.answer('Профиль не найден. Если это новый игрок, пришли его имя; иначе отмени ввод. Перед созданием и выдачей будет подтверждение.', parse_mode=None)
                else:
                    await message.answer('Профиль с таким ID не найден. Проверь ID или отмени ввод.', parse_mode=None)
                return
            name = str(player.get('user_name') or 'Игрок')[:100]
            await state.clear()
            desc = (f'Выдать игроку {name} (ID {player_id}) промопак размера {data["size"].upper()}?'
                    if action == 'pack' else f'Очистить весь инвентарь игрока {name} (ID {player_id})? Это необратимо.')
            await new_confirmation(message, message.from_user.id, action, desc,
                                   player_id=player_id, player_name=name, size=data.get('size'))
        elif action == 'pack_new_name':
            if len(text) > 100 or '\n' in text:
                raise ValueError('Нужно имя игрока одной строкой')
            await state.clear()
            await new_confirmation(message, message.from_user.id, 'pack',
                f'Создать профиль игрока {text} (ID {data["player_id"]}) и выдать промопак {data["size"].upper()}? Проверь ID.',
                player_id=data['player_id'], player_name=text, size=data['size'], allow_create=True)
        elif action in ('points', 'points_amount'):
            parts = text.split() if action == 'points' else [str(data['player_id']), text]
            if len(parts) != 2 or not parts[0].isascii() or not parts[0].isdigit() or len(parts[0]) > 15:
                raise ValueError('Нужны ID и сумма')
            player_id = int(parts[0])
            if player_id < 1 or not parts[1].lstrip('-').isascii() or not parts[1].lstrip('-').isdigit() or len(parts[1]) > 8:
                raise ValueError('Неверная сумма')
            amount = int(parts[1])
            if not amount or abs(amount) > 1000000:
                raise ValueError('Недопустимая сумма')
            player = await db.db.economy.find_one({'chat_id': data['chat_id'], 'user_id': player_id})
            if not player:
                await state.update_data(action='points_new_name', player_id=player_id, amount=amount)
                await message.answer('У этого ID пока нет записей очков в выбранной группе. Если это нужный игрок, пришли его имя; иначе отмени ввод. Перед изменением будет подтверждение.', parse_mode=None)
                return
            name = str(player.get('user_name') or 'Игрок')[:100]
            await state.clear()
            await new_confirmation(message, message.from_user.id, 'points',
                f'Изменить очки на {amount:+d} игроку {name} (ID {player_id}) в группе «{data["chat_title"]}» (ID {data["chat_id"]})?',
                chat_id=data['chat_id'], player_id=player_id, player_name=name, amount=amount)
        elif action == 'points_new_name':
            if len(text) > 100 or '\n' in text:
                raise ValueError('Нужно имя игрока одной строкой')
            await state.clear()
            await new_confirmation(message, message.from_user.id, 'points',
                f'Изменить очки на {data["amount"]:+d} новому игроку {text} (ID {data["player_id"]}) в группе «{data["chat_title"]}» (ID {data["chat_id"]})? Проверь ID и группу.',
                chat_id=data['chat_id'], player_id=data['player_id'], player_name=text, amount=data['amount'], allow_new=True)
        elif action == 'reset':
            if not text.isdecimal() or not 1 <= len(text) <= 15 or int(text) < 1:
                raise ValueError('Нужен ID игрока')
            player_id = int(text)
            player = await db.db.alchemy_players.find_one({'user_id': player_id})
            if not player:
                await message.answer('Игрок с таким ID не найден. Проверь ID или отмени ввод.', parse_mode=None)
                return
            await state.clear()
            await new_confirmation(message, message.from_user.id, 'reset',
                f'Удалить профиль алхимии игрока {player_id}? Действие необратимо.', player_id=player_id)
        else:
            await state.clear()
            await message.answer('Ввод устарел. Открой /panel.', parse_mode=None)
    except ValueError:
        await message.answer('Не удалось прочитать данные. Проверь формат и попробуй снова или отмени ввод.', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось выполнить ввод владельца: %s', type(exc).__name__)
        await state.clear()
        await message.answer('Результат не подтверждён. Проверь состояние в панели.', parse_mode=None)


@router.callback_query(F.data.startswith('owner_tools:'))
async def action(callback: types.CallbackQuery, state: FSMContext, bot):
    if not authorized(callback.from_user, callback.message.chat if callback.message else None):
        await callback.answer('Доступно только владельцу в личке.', show_alert=True)
        return
    parts = (callback.data or '').split(':')
    op = parts[1] if len(parts) > 1 else ''
    try:
        if op == 'cancel_input':
            if len(parts) not in (2, 3) or (len(parts) == 3 and
                    (len(parts[2]) != 16 or any(c not in '0123456789abcdef' for c in parts[2]))):
                return await callback.answer('Некорректная кнопка.', show_alert=True)
            async with _input_lock:
                current = await state.get_state()
                data = await state.get_data()
                # Old deployed prompts have no token. They may cancel only an
                # equally old owner-input state, never a newer form or cron/lore.
                matches = (current == OwnerInput.waiting.state and
                           (data.get('input_token') == parts[2] if len(parts) == 3
                            else not data.get('input_token')))
                if matches:
                    await state.clear()
            await callback.answer('Ввод отменён' if matches else 'Этот ввод уже завершён')
            await callback.message.edit_text('Ввод отменён. Пульт: /panel' if matches else
                                             'Этот ввод уже завершён. Пульт: /panel', parse_mode=None)
            return
        if op == 'prepare':
            if not await owner_trial.drafts_enabled(db.db):
                await callback.answer('Включи подготовку сценариев в разделе новостей.', show_alert=True)
                return
            await ask(callback, state, 'prepare',
                'Пришли от одной до четырёх ссылок, каждую с новой строки. '
                'При желании последней строкой напиши «Акцент: ...». '
                'Подготовка расходует квоту Gemini. Рендер и публикация не начнутся.')
            return
        if op == 'draw':
            await ask(callback, state, 'draw', 'Напиши, что нарисовать (до 500 символов). Действуют обычные бесплатные попытки и стоимость в очках; запрос потребуется подтвердить.')
            return
        if op == 'base':
            await state.clear()
            from handlers.alc_base import cmd_base
            await callback.answer()
            await cmd_base(proxy(callback.message, callback.from_user))
            return
        if op == 'help':
            await state.clear()
            from handlers.ex_logic import HELP_TEXT
            await callback.answer()
            await callback.message.answer(HELP_TEXT, parse_mode='Markdown')
            return
        if op == 'search_query':
            await ask(callback, state, 'search_query', 'Напиши поисковый запрос (до 500 символов). Проверка использует квоту поиска; перед запросом нужно подтверждение.')
            return
        if op == 'trial':
            if not await owner_trial.runtime_enabled(db.db):
                return await callback.answer('Пробный режим выключен или конфликтует с другими новостными режимами.', show_alert=True)
            await ask(callback, state, 'trial', 'Пришли проверенный сценарий одним сообщением в формате:\n'
                'Источник: https://...\nСтрока: ...\nБендер: ...\nКиберсенсей: ...\n'
                'Добавь остальные реплики отдельными строками. Перед рендером потребуется подтверждение.')
            return
        if op == 'trial_status':
            from handlers.news_owner_trial import trial_status
            await callback.answer()
            await trial_status(proxy(callback.message, callback.from_user))
            if await owner_trial.runtime_enabled(db.db):
                stage, finished_id = await owner_trial.status_info(db.db)
                if stage == 'review_sent' and finished_id:
                    await new_confirmation(callback.message, callback.from_user.id, 'trial_next',
                        'Освободить слот после отправленного видео? Новая задача автоматически не запустится.',
                        finished_id=finished_id)
            return
        if op == 'recipe':
            await ask(callback, state, 'recipe', 'Напиши точное название результата алхимии.')
            return
        if op == 'clear':
            await profile_choice(callback, state, 'clear')
            return
        if op == 'pack':
            b = InlineKeyboardBuilder()
            for size in ('s', 'm', 'l', 'xl'):
                b.button(text=size.upper(), callback_data=f'owner_tools:packsize:{size}')
            b.button(text='⬅️ Пульт', callback_data='owner_panel:home')
            b.adjust(4, 1)
            await callback.answer()
            await callback.message.answer('Выбери размер промопака:', reply_markup=b.as_markup(), parse_mode=None)
            return
        if op == 'packsize' and len(parts) == 3 and parts[2] in ('s', 'm', 'l', 'xl'):
            await profile_choice(callback, state, 'pack', parts[2])
            return
        if op == 'profileinput' and len(parts) == 3 and parts[2] in ('clear', 'reset', 's', 'm', 'l', 'xl'):
            code = parts[2]
            await ask(callback, state, code if code in ('clear', 'reset') else 'pack',
                      'Пришли числовой Telegram ID игрока. Профиль алхимии общий для всех групп. Перед изменением потребуется подтверждение.',
                      size=None if code in ('clear', 'reset') else code)
            return
        if op == 'profile' and len(parts) == 4 and parts[2] in ('clear', 'reset', 's', 'm', 'l', 'xl'):
            code, value = parts[2], parts[3]
            if not value.isascii() or not value.isdigit() or not 1 <= len(value) <= 15 or int(value) < 1:
                raise ValueError('Неверный игрок')
            player_id = int(value)
            player = await db.db.alchemy_players.find_one({'user_id': player_id})
            if not player:
                return await callback.answer('Профиль не найден.', show_alert=True)
            name = str(player.get('user_name') or 'Игрок')[:100]
            await state.clear()
            await callback.answer()
            if code == 'clear':
                desc = f'Очистить весь инвентарь игрока {name} (ID {player_id})? Это необратимо.'
            elif code == 'reset':
                desc = f'Удалить профиль алхимии игрока {name} (ID {player_id})? Это необратимо.'
            else:
                desc = f'Выдать игроку {name} (ID {player_id}) промопак размера {code.upper()}?'
            await new_confirmation(callback.message, callback.from_user.id, code if code in ('clear', 'reset') else 'pack',
                                   desc, player_id=player_id, player_name=name, size=None if code in ('clear', 'reset') else code)
            return
        if op == 'points':
            from handlers.admin_cron import get_known_groups
            groups = await get_known_groups(bot)
            b = InlineKeyboardBuilder()
            for cid, title in groups.items():
                b.button(text=str(title)[:80], callback_data=f'owner_tools:pointsgroup:{cid}')
            b.button(text='⬅️ Пульт', callback_data='owner_panel:home')
            b.adjust(1)
            await callback.answer()
            await callback.message.answer('Выбери группу для изменения очков:' if groups else 'Доступные группы с историей не найдены.', reply_markup=b.as_markup(), parse_mode=None)
            return
        if op in ('pointsgroup', 'pointsinput', 'pointspick'):
            from handlers.admin_cron import get_known_groups
            if len(parts) != (4 if op == 'pointspick' else 3):
                raise ValueError('Неверная группа')
            try:
                group_id = int(parts[2])
            except ValueError:
                return await callback.answer('Неверная группа.', show_alert=True)
            groups = await get_known_groups(bot)
            if group_id not in groups or group_id >= 0:
                return await callback.answer('Группа недоступна. Выбери её заново.', show_alert=True)
            title = str(groups[group_id])[:100]
            if op == 'pointsgroup':
                # A bounded recent scan avoids an expensive full collection grouping.
                rows = await db.db.economy.find({'chat_id': group_id}, {'user_id': 1, 'user_name': 1}).sort(
                    '_id', -1).limit(200).to_list(length=200)
                seen = set()
                b = InlineKeyboardBuilder()
                for row in rows:
                    player_id = row.get('user_id')
                    if type(player_id) is not int or player_id < 1 or player_id in seen:
                        continue
                    seen.add(player_id)
                    name = str(row.get('user_name') or 'Игрок')[:40]
                    b.button(text=f'{name} · {player_id}', callback_data=f'owner_tools:pointspick:{group_id}:{player_id}')
                    if len(seen) == 20:
                        break
                b.button(text='⌨️ Ввести ID вручную', callback_data=f'owner_tools:pointsinput:{group_id}')
                b.button(text='⬅️ Пульт', callback_data='owner_panel:home')
                b.adjust(1)
                await state.clear()
                await callback.answer()
                await callback.message.answer(f'Группа «{title}». Выбери игрока из недавних записей или введи ID вручную. '
                    'Будут изменены только очки этой группы.', reply_markup=b.as_markup(), parse_mode=None)
                return
            if op == 'pointsinput':
                await ask(callback, state, 'points', f'Группа «{title}». Пришли ID игрока и сумму через пробел, например: 123456789 100. '
                          'Для списания укажи минус. Перед изменением будет подтверждение.', chat_id=group_id, chat_title=title)
                return
            value = parts[3]
            if not value.isascii() or not value.isdigit() or not 1 <= len(value) <= 15 or int(value) < 1:
                raise ValueError('Неверный игрок')
            player_id = int(value)
            player = await db.db.economy.find_one({'chat_id': group_id, 'user_id': player_id})
            if not player:
                return await callback.answer('Игрок не найден в этой группе.', show_alert=True)
            name = str(player.get('user_name') or 'Игрок')[:100]
            await ask(callback, state, 'points_amount', f'Группа «{title}», игрок {name} (ID {player_id}). '
                      'Пришли сумму изменения, например 100 или -100. Перед изменением будет подтверждение.',
                      chat_id=group_id, chat_title=title, player_id=player_id)
            return
        if op == 'reset':
            await profile_choice(callback, state, 'reset')
            return
        if op == 'reports':
            from handlers.admin_cron import cmd_set_daily
            await callback.answer()
            await cmd_set_daily(proxy(callback.message, callback.from_user), bot, state)
            return
        if op == 'lore':
            from handlers.alc_heritage import lore_edit
            # The legacy editor seeds defaults on open and deletes this old ID.
            # Do not hide that potentially destructive migration behind a menu button.
            if await db.db.alchemy_lore.find_one({'id': 'forge_legacy'}):
                await callback.answer()
                await new_confirmation(callback.message, callback.from_user.id, 'lore_open',
                    'Открытие редактора удалит старую запись наследия forge_legacy. Продолжить?')
            else:
                await callback.answer()
                await lore_edit(proxy(callback.message, callback.from_user), SimpleNamespace(args=''), state)
            return
        if op == 'lorehelp':
            from handlers.alc_heritage import lore_help
            await callback.answer()
            await lore_help(proxy(callback.message, callback.from_user))
            return
        if op == 'recipes':
            from handlers.alc_lab import cmd_recipe_cheatsheet
            await callback.answer()
            await cmd_recipe_cheatsheet(proxy(callback.message, callback.from_user), SimpleNamespace(args=''))
            return
        prompts = {
            'regen': 'Перегенерировать квесты для всех игроков? Это изменит их текущие задания.',
            'seed': 'Обновить стартовые наследия из файла? Старая запись forge_legacy, если она существует, будет удалена.',
            'image_probe': 'Проверить Cloudflare и OVHcloud? Это реальные запросы, они расходуют квоту.',
            'search_probe': 'Проверить поиск картинки? Это реальный запрос, который может расходовать квоту.',
        }
        if op in prompts:
            await callback.answer()
            await new_confirmation(callback.message, callback.from_user.id, op, prompts[op])
            return
        if op in ('run', 'cancel') and len(parts) == 3:
            token = parts[2]
            if len(token) != 16 or any(c not in '0123456789abcdef' for c in token):
                raise ValueError('Неверный идентификатор')
            query = {'_id': token, 'owner_id': callback.from_user.id, 'stage': 'ready',
                     'expires_at': {'$gt': datetime.now(timezone.utc)}}
            if op == 'cancel':
                # Cancellation must also close an expired confirmation.  The CAS
                # still competes atomically with run's ready -> running claim.
                cancel_query = {'_id': token, 'owner_id': callback.from_user.id, 'stage': 'ready'}
                changed = await db.db.owner_panel_confirmations.update_one(cancel_query, {'$set': {'stage': 'cancelled'}})
                record = None if changed.modified_count else await db.db.owner_panel_confirmations.find_one(
                    {'_id': token, 'owner_id': callback.from_user.id})
                if record and record.get('stage') in ('running', 'done'):
                    # Do not delete or edit this message while run might be using
                    # it; cancellation cannot stop an already claimed operation.
                    await callback.answer('Действие уже обрабатывается или обработано. Отменить его кнопкой нельзя.', show_alert=True)
                    return
                cancelled = bool(changed.modified_count or (record and record.get('stage') == 'cancelled'))
                try:
                    await callback.answer('Отменено' if cancelled else 'Подтверждение уже недоступно')
                except Exception as exc:
                    # An expired callback acknowledgement must not prevent the
                    # now-cancelled message from being closed.
                    logger.debug('Не удалось подтвердить отмену кнопки: %s', type(exc).__name__)
                try:
                    await callback.message.delete()
                except Exception as exc:
                    logger.debug('Не удалось удалить отменённое подтверждение: %s', type(exc).__name__)
                    # If Telegram refuses deletion, replace the buttons with a
                    # neutral status; an absent record may have been processed.
                    text = ('Отменено. Действие не запущено.' if cancelled else
                            'Подтверждение недоступно. Если действие уже запускалось, проверь его результат.')
                    try:
                        await callback.message.edit_text(text, reply_markup=None, parse_mode=None)
                    except Exception as edit_exc:
                        # It may already have been deleted by a duplicate click.
                        logger.debug('Не удалось закрыть подтверждение: %s', type(edit_exc).__name__)
                return
            from pymongo import ReturnDocument
            record = await db.db.owner_panel_confirmations.find_one_and_update(
                query, {'$set': {'stage': 'running'}}, return_document=ReturnDocument.AFTER)
            if not record:
                await callback.answer('Уже выполнено или подтверждение устарело.', show_alert=True)
                return
            await callback.answer('Выполняю')
            await callback.message.edit_text('Выполняю действие. Повторное нажатие не запустит его снова.', parse_mode=None)
            message = proxy(callback.message, callback.from_user)
            name, data = record['action'], record['data']
            if name == 'regen':
                from handlers.alc_quests import admin_regen_quests
                await admin_regen_quests(message)
            elif name == 'lore_open':
                from handlers.alc_heritage import lore_edit
                await lore_edit(message, SimpleNamespace(args=''), state)
            elif name == 'seed':
                from handlers.alc_heritage import lore_edit
                await lore_edit(message, SimpleNamespace(args='seed'), state)
            elif name == 'reset':
                from handlers.alc_core import admin_hard_reset
                await admin_hard_reset(message, SimpleNamespace(args=str(data['player_id'])))
            elif name in ('pack', 'clear'):
                from handlers import alc_core
                player = await db.db.alchemy_players.find_one({'user_id': data['player_id']})
                if not player and not (name == 'pack' and data.get('allow_create')):
                    await message.answer('Профиль игрока больше не найден.', parse_mode=None)
                else:
                    target = SimpleNamespace(id=data['player_id'], first_name=data['player_name'], is_bot=False)
                    message.reply_to_message = SimpleNamespace(from_user=target)
                    if name == 'pack':
                        await alc_core.admin_promo(message, SimpleNamespace(args=data['size']))
                    else:
                        await alc_core.admin_clear_inv(message)
            elif name == 'points':
                if not data.get('allow_new') and not await db.db.economy.find_one({'chat_id': data['chat_id'], 'user_id': data['player_id']}):
                    await message.answer('Игрок больше не найден в выбранной группе.', parse_mode=None)
                else:
                    # One-use confirmation ID also makes the ledger insertion idempotent.
                    await db.db.economy.insert_one({'_id': 'owner-panel:' + token, 'game_type': 'promo',
                        'chat_id': data['chat_id'], 'user_id': data['player_id'], 'user_name': data['player_name'],
                        'score': data['amount'], 'date': get_msk_now()})
                    await message.answer(f'Очки изменены на {data["amount"]:+d} для игрока {data["player_id"]} в группе {data["chat_id"]}.', parse_mode=None)
            elif name == 'draw':
                from handlers.image_gen import process_image_request
                await process_image_request(message, data['query'])
            elif name == 'image_probe':
                from handlers.image_gen import cmd_image_probe
                await cmd_image_probe(message)
            elif name == 'search_probe':
                from handlers.web_search import cmd_search_image_probe
                await cmd_search_image_probe(message, SimpleNamespace(args=data.get('query')))
            elif name == 'legacy_news':
                from handlers import owner_legacy_news, news_intake
                if not owner_legacy_news.enabled() or owner_legacy_news.flag_snapshot() != data['mode_flags']:
                    await message.answer('Настройки старого режима изменились. Материал не сохранён; открой раздел и проверь настройки заново.', parse_mode=None)
                else:
                    message.message_id = data['original_message_id']
                    message.text = data['command_text']
                    message.reply_to_message = (SimpleNamespace(text=data['reply_text'], caption=None)
                                                if data['reply_text'] is not None else None)
                    await news_intake.add_news(message)
            elif name == 'legacy_digest':
                from handlers import owner_legacy_news, news_intake
                if not owner_legacy_news.enabled() or not owner_legacy_news.queue_enabled():
                    await message.answer('Дополнительный режим или его очередь выключены. Дайджест не поставлен в очередь.', parse_mode=None)
                else:
                    message.text = '/news_digest ' + ' '.join(data['ids'])
                    await news_intake.create_digest(message)
            elif name in ('noimage', 'nofocus'):
                from handlers import news_owner_drafts
                message.text = f'/news_{name} {data["number"]} {data["revision"]}'
                fn = news_owner_drafts.remove_image if name == 'noimage' else news_owner_drafts.remove_focus
                await fn(message)
            elif name in ('approve_draft', 'approve_program'):
                from handlers import news_owner_drafts
                command = '/news_approve' if name == 'approve_draft' else '/news_program_approve'
                message.text = f'{command} {data["number"]} {data["revision"]}'
                fn = news_owner_drafts.approve_draft if name == 'approve_draft' else news_owner_drafts.approve_program
                await fn(message)
            elif name == 'trial':
                from handlers.news_owner_trial import submit_trial
                message.text = data['content']
                await submit_trial(message)
            elif name == 'trial_next':
                from handlers.news_owner_trial import next_trial
                message.text = '/news_trial_next ' + data['finished_id']
                await next_trial(message)
            elif name == 'collect_bulletin_sources':
                from handlers import owner_bulletin_panel
                await owner_bulletin_panel.collect_selected(message, callback.from_user.id, data)
            elif name == 'run_bulletin_editor':
                from handlers import owner_bulletin_panel
                await owner_bulletin_panel.run_editor(message, callback.from_user.id, data)
            elif name == 'review_bulletin_video':
                from handlers import owner_bulletin_panel
                await owner_bulletin_panel.video_review_confirmed(message, callback.from_user.id, data, bot)
            elif name == 'use_bulletin_video':
                from handlers import owner_bulletin_panel
                await owner_bulletin_panel.use_video_confirmed(message, callback.from_user.id, data)
            elif name in ('render_bulletin', 'shorten_bulletin'):
                from handlers import owner_bulletin_panel
                fn = owner_bulletin_panel.render_confirmed if name == 'render_bulletin' else owner_bulletin_panel.shorten_confirmed
                await fn(message, callback.from_user.id, data)
            elif name == 'prepare_bulletin':
                from handlers import owner_bulletin_panel
                await owner_bulletin_panel.prepare_selected(message, callback.from_user.id, data)
            elif name == 'build_program':
                from news_prototype import owner_program
                from handlers import news_owner_drafts, owner_news_actions
                if not await owner_trial.drafts_enabled(db.db):
                    await message.answer('Подготовка сценариев выключена.', parse_mode=None)
                else:
                    for number in data['draft_ids']:
                        draft = await owner_drafts.get(db.db, callback.from_user.id, number)
                        if not await owner_program.eligible_draft(db.db, callback.from_user.id, draft) or draft.get('revision') != data['revisions'].get(str(number)):
                            await message.answer('Один из выбранных черновиков изменился. Проверь его и выбери заново.', parse_mode=None)
                            break
                    else:
                        # A confirmation message ID is unique in this Telegram chat.
                        program_id = callback.message.message_id
                        await owner_program.create(db.db, callback.from_user.id, program_id, data['draft_ids'])
                        program = await owner_program.get(db.db, callback.from_user.id, program_id)
                        await news_owner_drafts.show_program(message, program_id, program=program, panel_mode=True)
            else:
                raise ValueError('Неизвестное действие')
            await db.db.owner_panel_confirmations.update_one({'_id': token, 'stage': 'running'}, {'$set': {'stage': 'done'}})
            return
        await callback.answer('Неизвестное действие.', show_alert=True)
    except Exception as exc:
        logger.warning('Не удалось выполнить действие владельца: %s', type(exc).__name__)
        try:
            await callback.answer('Не удалось выполнить действие. Проверь состояние в панели.', show_alert=True)
        except Exception:
            try:
                await callback.message.answer('Не удалось подтвердить результат. Проверь состояние в панели; не повторяй действие вслепую.', parse_mode=None)
            except Exception:
                pass

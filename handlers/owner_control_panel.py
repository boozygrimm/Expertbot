"""Owner's private control panel, separate from per-group /panel settings."""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
import secrets
import unicodedata

from config import ADMIN_ID
from database import db
import owner_preferences
from handlers.owner_nav import add_nav, HOME_CALLBACK
from news_prototype import owner_daily, owner_trial

router = Router()
logger = logging.getLogger(__name__)
_input_state_lock = asyncio.Lock()
_raid_broadcast_lock = asyncio.Lock()
RAID_PREVIEW_URL = 'https://boozygrimm.github.io/Expertbot/lunapark-raid-magitech.html?telegram=1'
RAID_DESCRIPTION_LIMIT = 700  # UTF-16 units; the whole preview fits one Telegram message.


class RaidAnnouncementInput(StatesGroup):
    description = State()
    preview = State()


def raid_build_id():
    """Packaged game revision, not evidence that Pages/Render has deployed it."""
    try:
        with (Path(__file__).resolve().parents[1] / 'lunapark-raid-magitech.html').open(encoding='utf-8') as page:
            match = re.search(r'<meta name="raid-build" content="([a-f0-9]{12})">', page.read(1024))
        return match[1] if match else ''
    except (OSError, UnicodeError):
        return ''


def raid_preview_content(*, owner=True, updates_enabled=True):
    # Fixed public Pages target, never a user-supplied URL. The command/button
    # is restricted by the bot; the demo itself remains public and browser-local.
    build = raid_build_id()
    text = ('⚔️ RAID · магитех-тест Mini App\n\n'
            'Эта версия заменяет классический тест во входе /raid_test. '
            'Прогресс классики остаётся отдельно и сюда не переносится.\n'
            'Запуск из своей лички после допуска владельца. Сам пробник на Pages публичный.\n'
            'Без рабочей игровой базы, серверных наград и переноса прогресса. '
            'У каждого свой прогресс: сохранения локальные — забег, классы, настройки, рекорды и достижения. '
            'Данные не переносятся между устройствами и могут отличаться от обычного браузера.\n\n'
            'Скриншоты не блокируем. Выход из игры: «Меню → Закрыть Mini App».\n'
            'Локация закреплена на весь забег, в том числе после «Продолжить». '
            'Проверь цепочку пальцем, выбор наград, сворачивание и повторный вход.\n'
            + (f'Ожидаемая сборка: {build}.' if build else 'Номер сборки показан внутри пробника.'))
    b = InlineKeyboardBuilder()
    b.button(text='⚔️ Открыть Рейд · тест', web_app=types.WebAppInfo(url=RAID_PREVIEW_URL + (f'&build={build}' if build else '')))
    b.adjust(1)
    if owner:
        b.row(types.InlineKeyboardButton(text='👥 Тестеры', callback_data='raid_access:list:pending:0'))
        b.row(types.InlineKeyboardButton(text='📣 Сообщить об обновлении', callback_data='raid_announce:compose'))
        add_nav(b, back=HOME_CALLBACK, back_label='‹ Пульт', refresh='owner_panel:raid_test')
    else:
        b.row(types.InlineKeyboardButton(
            text='🔔 Обновления: включены' if updates_enabled else '🔕 Обновления: выключены',
            callback_data='raid_updates:preferences'))
        b.row(types.InlineKeyboardButton(text='🔄 Обновить допуск', callback_data='raid_access:open'),
              types.InlineKeyboardButton(text='Закрыть', callback_data='raid_access:close'))
    return text, b.as_markup()


def raid_private_user(message, user=None) -> bool:
    user = user or getattr(message, 'from_user', None)
    chat = getattr(message, 'chat', None)
    return bool(user and type(user.id) is int and 0 < user.id < 2 ** 52
                and not getattr(user, 'is_bot', False) and chat
                and chat.type == 'private' and chat.id == user.id)


async def raid_notify(bot, user_id, text, markup=None) -> bool:
    try:
        await bot.send_message(user_id, text, reply_markup=markup, parse_mode=None, protect_content=False)
        return True
    except Exception as exc:
        logger.warning('Доставка уведомления Рейда не подтверждена: %s', type(exc).__name__)
        return False


def raid_decision_buttons(doc):
    b = InlineKeyboardBuilder()
    uid, rev, status = doc['user_id'], doc['revision'], doc['status']
    if status != 'allowed':
        b.button(text='✅ Разрешить', callback_data=f'raid_access:allow:{uid}:{rev}')
    if status == 'pending':
        b.button(text='❌ Отклонить', callback_data=f'raid_access:deny:{uid}:{rev}')
    if status == 'allowed':
        b.button(text='Убрать допуск', callback_data=f'raid_access:revoke:{uid}:{rev}')
    b.adjust(2)
    add_nav(b, back='raid_access:list:all:0', refresh=f'raid_access:user:{uid}:{rev}')
    return b.as_markup()


def raid_tester_text(doc):
    status = {'pending': 'Заявка', 'allowed': 'Допущен', 'denied': 'Отклонён', 'revoked': 'Допуск отозван'}[doc['status']]
    return (f"👤 {doc.get('name', 'Тестер')}\nID: {doc['user_id']}\nСтатус: {status}\n\n"
            'Допуск только к пробнику, не к пульту и не к админским функциям. '
            'У каждого свой локальный прогресс. Страница Pages публична; отзыв '
            'останавливает выдачу новых кнопок, не блокирует старую ссылку и не удаляет сейвы.')


# Registered on the root dispatcher before any broad FSM input handlers.
async def open_raid_test(message: types.Message, state: FSMContext = None):
    if not raid_private_user(message):
        return
    owner = message.from_user.id == ADMIN_ID
    if not owner:
        try:
            doc = await owner_preferences.get_raid_tester(db.settings, message.from_user.id)
            created = False
            if doc is None:
                doc, created = await owner_preferences.request_raid_tester(
                    db.settings, message.from_user.id, getattr(message.from_user, 'first_name', 'Тестер'))
            if doc['status'] != 'allowed':
                if doc['status'] == 'pending':
                    notified = not created or await raid_notify(message.bot, ADMIN_ID,
                        '📩 Заявка на тест Рейда\n\n' + raid_tester_text(doc), raid_decision_buttons(doc))
                    text = ('Заявка на тест Рейда сохранена. Дождись решения владельца.' if notified else
                            'Заявка на тест Рейда сохранена. Уведомление владельцу не удалось доставить; сообщи ему о заявке.')
                else:
                    text = 'Заявка отклонена или допуск отозван. Для повторного допуска обратись к владельцу.'
                await message.answer(text, parse_mode=None)
                return
        except Exception as exc:
            logger.warning('Не удалось проверить допуск Рейда: %s', type(exc).__name__)
            await message.answer('Не удалось проверить или сохранить допуск. Попробуй позже.', parse_mode=None)
            return
    if state:
        await state.clear()
    text, markup = raid_preview_content(owner=owner,
        updates_enabled=True if owner else owner_preferences.raid_tester_updates_enabled(doc))
    await message.answer(text, reply_markup=markup, parse_mode=None, protect_content=False)


@router.callback_query(F.data.startswith('raid_access:'))
async def raid_access_action(callback: types.CallbackQuery, state: FSMContext = None):
    if not callback.from_user or not callback.message or not raid_private_user(callback.message, callback.from_user):
        await callback.answer('Допуск доступен только в своей личке.', show_alert=True)
        return
    action = (callback.data or '').removeprefix('raid_access:')
    if action not in ('open', 'close') and not authorized(callback):
        await callback.answer('Управлять тестерами может только владелец в личке.', show_alert=True)
        return
    try:
        if action == 'close':
            await callback.answer()
            await callback.message.delete()
            return
        if action == 'open':
            owner = callback.from_user.id == ADMIN_ID
            doc = None if owner else await owner_preferences.get_raid_tester(db.settings, callback.from_user.id)
            if not owner and (not doc or doc['status'] != 'allowed'):
                b = InlineKeyboardBuilder()
                b.button(text='Закрыть', callback_data='raid_access:close')
                await edit(callback.message, 'Допуска к тесту нет. Обратись к владельцу.', b.as_markup())
                await callback.answer('Допуска к тесту нет.', show_alert=True)
                return
            if state:
                await state.clear()
            text, markup = raid_preview_content(owner=owner,
                updates_enabled=True if owner else owner_preferences.raid_tester_updates_enabled(doc))
            await callback.message.answer(text, reply_markup=markup, parse_mode=None, protect_content=False)
            await callback.answer()
            return
        listing = re.fullmatch(r'list:(pending|allowed|all):(0|[1-9][0-9]{0,5})', action)
        item = re.fullmatch(r'(user|allow|deny|revoke|remove):([1-9][0-9]{0,15}):(0|[1-9][0-9]{0,8})', action)
        if not listing and not item:
            await callback.answer('Неизвестное действие. Обнови список тестеров.', show_alert=True)
            return
        if state:
            await state.clear()
        if listing:
            kind, page = listing[1], int(listing[2])
            rows, more = await owner_preferences.list_raid_testers(db.settings, kind, page)
            b = InlineKeyboardBuilder()
            for label, value in [('Заявки', 'pending'), ('Допущенные', 'allowed'), ('Все', 'all')]:
                b.button(text=label, callback_data=f'raid_access:list:{value}:0')
            b.adjust(3)
            lines = ['👥 Тестеры Рейда', 'Пульт › Рейд › Тестеры', '',
                     'Тестер пишет /raid_test в свою личку бота. Разрешение не даёт прав пульта.', '']
            for doc in rows:
                uid, rev = doc['user_id'], doc['revision']
                status_label = {'pending': 'заявка', 'allowed': 'допущен', 'denied': 'отклонён', 'revoked': 'допуск отозван'}.get(doc['status'], 'неизвестно')
                lines.append(f"{doc.get('name', 'Тестер')[:32]} · ID {uid} · {status_label}")
                b.row(types.InlineKeyboardButton(text=f"{doc.get('name', 'Тестер')[:22]} · {uid}",
                      callback_data=f'raid_access:user:{uid}:{rev}'))
            if not rows:
                lines.append('Список пуст.')
            navigation = []
            if page:
                navigation.append(types.InlineKeyboardButton(text='‹ Предыдущие', callback_data=f'raid_access:list:{kind}:{page-1}'))
            if more:
                navigation.append(types.InlineKeyboardButton(text='Следующие ›', callback_data=f'raid_access:list:{kind}:{page+1}'))
            if navigation:
                b.row(*navigation)
            add_nav(b, back='owner_panel:raid_test', refresh=f'raid_access:list:{kind}:{page}')
            await edit(callback.message, '\n'.join(lines), b.as_markup())
            await callback.answer()
            return
        command, uid, rev = item[1], int(item[2]), int(item[3])
        doc = await owner_preferences.get_raid_tester(db.settings, uid)
        if not doc:
            await callback.answer('Заявка не найдена.', show_alert=True)
            return
        if command == 'user':
            await edit(callback.message, raid_tester_text(doc), raid_decision_buttons(doc))
            await callback.answer()
            return
        if doc['revision'] != rev:
            await callback.answer('Решение уже изменилось. Обнови карточку тестера.', show_alert=True)
            return
        if command == 'revoke':
            if doc['status'] != 'allowed':
                await callback.answer('Допуск уже изменился.', show_alert=True)
                return
            b = InlineKeyboardBuilder()
            b.button(text='Да, убрать допуск', callback_data=f'raid_access:remove:{uid}:{rev}')
            b.button(text='Отмена', callback_data=f'raid_access:user:{uid}:{rev}')
            b.adjust(1)
            add_nav(b, back='raid_access:list:allowed:0', refresh=f'raid_access:user:{uid}:{rev}')
            await edit(callback.message, raid_tester_text(doc) + '\n\nУбрать допуск? Локальный прогресс не удаляется.', b.as_markup())
            await callback.answer()
            return
        status = {'allow': 'allowed', 'deny': 'denied', 'remove': 'revoked'}[command]
        if not await owner_preferences.change_raid_tester(db.settings, uid, rev, status):
            await callback.answer('Решение уже изменилось. Обнови карточку тестера.', show_alert=True)
            return
        if status == 'allowed':
            text, markup = raid_preview_content(owner=False, updates_enabled=owner_preferences.raid_tester_updates_enabled(doc))
            delivered = await raid_notify(callback.bot, uid, '✅ Допуск к тесту разрешён.\n\n' + text, markup)
        else:
            delivered = await raid_notify(callback.bot, uid,
                'Допуск к тесту Рейда ' + ('отозван' if status == 'revoked' else 'не разрешён')
                + '. Локальный прогресс не удалён. По вопросам обратись к владельцу.')
        doc = await owner_preferences.get_raid_tester(db.settings, uid)
        await edit(callback.message, raid_tester_text(doc), raid_decision_buttons(doc))
        await callback.answer('Решение сохранено.' if delivered else 'Решение сохранено, уведомление не удалось доставить.', show_alert=not delivered)
    except Exception as exc:
        logger.warning('Ошибка управления допуском Рейда: %s', type(exc).__name__)
        await callback.answer('Не удалось выполнить действие. Обнови список или попробуй позже.', show_alert=True)


@router.callback_query(F.data.startswith('raid_updates:'))
async def raid_update_subscription(callback: types.CallbackQuery):
    """Self-service preferences, including buttons on an old announcement."""
    if (not callback.from_user or not callback.message
            or not raid_private_user(callback.message, callback.from_user)):
        await callback.answer('Уведомления настраиваются только в своей личке.', show_alert=True)
        return
    action = (callback.data or '').removeprefix('raid_updates:')
    if action not in ('preferences', 'on', 'off') or callback.from_user.id == ADMIN_ID:
        await callback.answer('Эта настройка доступна тестеру в своей личке.', show_alert=True)
        return
    try:
        uid = callback.from_user.id  # Never accept a target ID from callback_data.
        doc = await owner_preferences.get_raid_tester(db.settings, uid)
        if doc is None:
            await callback.answer('Заявки на тест нет. Сначала отправь /raid_test.', show_alert=True)
            return
        if action != 'preferences':
            if not await owner_preferences.set_raid_tester_updates(db.settings, uid, action == 'on'):
                await callback.answer('Настройка не сохранена. Попробуй позже.', show_alert=True)
                return
            doc = await owner_preferences.get_raid_tester(db.settings, uid)
        enabled = owner_preferences.raid_tester_updates_enabled(doc)
        text = ('🔔 Обновления теста Рейда\n\n'
                + ('Сообщения об обновлениях включены.' if enabled else 'Сообщения об обновлениях выключены.')
                + '\nПолучают их только допущенные тестеры. Отказ не отменяет допуск, '
                'не меняет локальный прогресс и сохраняется после перезапуска бота. '
                'Сообщения о решении по допуску — отдельно. Включить обновления обратно можно здесь или через /raid_test.')
        b = InlineKeyboardBuilder()
        b.button(text='🔕 Не присылать обновления' if enabled else '🔔 Получать обновления',
                 callback_data='raid_updates:off' if enabled else 'raid_updates:on')
        if doc['status'] == 'allowed':
            b.button(text='‹ Рейд · тест', callback_data='raid_access:open')
        b.button(text='Закрыть', callback_data='raid_access:close')
        b.adjust(1)
        await edit(callback.message, text, b.as_markup())
        await callback.answer('Настройка сохранена.' if action != 'preferences' else None)
    except Exception as exc:
        logger.warning('Не удалось изменить уведомления Рейда: %s', type(exc).__name__)
        await callback.answer('Не удалось прочитать или сохранить настройку. Попробуй позже.', show_alert=True)


def raid_update_message(description, build):
    return (f'📣 Обновление теста Рейда\nСборка: {build}\n\n{description}\n\n'
            'Закрой уже открытый Mini App и открой тест заново кнопкой ниже. '
            'Номер внутри игры должен совпасть со сборкой выше. '
            'Твой локальный прогресс остаётся на этом устройстве.')


def raid_update_markup(build):
    b = InlineKeyboardBuilder()
    b.button(text='⚔️ Открыть обновлённый тест',
             web_app=types.WebAppInfo(url=RAID_PREVIEW_URL + f'&build={build}'))
    b.button(text='🔕 Не присылать обновления', callback_data='raid_updates:off')
    b.adjust(1)
    return b.as_markup()


def raid_announcement_preview(draft, count):
    token, build = draft['token'], draft['build']
    text = ('👁 Предпросмотр — ещё не отправлено\n\n'
            + raid_update_message(draft['description'], build)
            + f'\n\nПолучателей сейчас: {count}. Только допущенные, не отказавшиеся от обновлений. '
            'Перед отправкой допуск и подписка проверяются ещё раз. '
            'Сборка взята из этого процесса бота; готовность Pages проверь кнопкой. '
            'Публикация сама по себе ничего тестерам не рассылает.')
    b = InlineKeyboardBuilder()
    b.button(text='⚔️ Проверить сборку', web_app=types.WebAppInfo(url=RAID_PREVIEW_URL + f'&build={build}'))
    if count:
        b.button(text=f'📣 Отправить тестерам ({count})', callback_data=f'raid_announce:send:{token}')
    b.button(text='✏️ Изменить описание', callback_data=f'raid_announce:edit:{token}')
    b.button(text='Отмена', callback_data=f'raid_announce:cancel:{token}')
    b.adjust(1)
    add_nav(b, back='owner_panel:raid_test', back_label='‹ Рейд · тест', refresh=f'raid_announce:preview:{token}')
    return text, b.as_markup()


def raid_announcement_prompt(draft, previous=None):
    text = ('📣 Сообщить тестерам об обновлении\nПульт › Рейд › Объявление\n\n'
            f'Сборка этого процесса бота: {draft["build"]}.\n'
            f'Пришли краткое описание изменений одним текстовым сообщением (до {RAID_DESCRIPTION_LIMIT} символов; '
            'эмодзи могут занимать два). Сначала будет предпросмотр и отдельная кнопка отправки. '
            'Ничего не отправляется автоматически.\n\n'
            'После обновления main дождись Pages и автодеплоя Render, открой свежий /raid_test '
            'и сверь номер сборки в самой игре. Старое открытое окно само не обновится.')
    if previous and previous.get('status') in ('sending', 'interrupted'):
        text += ('\n\n⚠️ Итог предыдущей отправки не полный: часть сообщений могла дойти. '
                 'Автоматического продолжения или повтора нет. Не повторяй то же объявление вслепую.')
    elif previous and previous.get('status') == 'complete':
        text += (f'\n\nПоследний итог: доставлено {previous.get("sent", 0)}, '
                 f'не подтверждено {previous.get("unconfirmed", 0)}, пропущено {previous.get("skipped", 0)}.')
    b = InlineKeyboardBuilder()
    b.button(text='Отмена', callback_data=f'raid_announce:cancel:{draft["token"]}')
    b.adjust(1)
    add_nav(b, back='owner_panel:raid_test', back_label='‹ Рейд · тест', refresh='raid_announce:compose')
    return text, b.as_markup()


# Registered on the root dispatcher before broad FSM/media input handlers.
async def receive_raid_announcement(message: types.Message, state: FSMContext):
    if not raid_private_user(message) or message.from_user.id != ADMIN_ID:
        return
    async with _input_state_lock:
        if await state.get_state() != RaidAnnouncementInput.description.state:
            return
        data = await state.get_data()
        draft = data.get('raid_announcement') or {}
        if not re.fullmatch(r'[a-f0-9]{16}', draft.get('token', '')):
            return
        description = ''.join(c for c in (message.text or '').replace('\r\n', '\n').replace('\t', ' ')
                              if c == '\n' or not unicodedata.category(c).startswith('C')).strip()
        if not description or len(description.encode('utf-16-le')) // 2 > RAID_DESCRIPTION_LIMIT:
            await message.answer(f'Нужен текст от 1 до {RAID_DESCRIPTION_LIMIT} символов; '
                                 'эмодзи могут занимать два. Это пока только черновик.', parse_mode=None)
            return
        if (message.text or '').startswith('/'):
            return  # Root routing also leaves commands to their normal handlers.
        try:
            if draft.get('build') != raid_build_id():
                await state.clear()
                await message.answer('Сборка изменилась. Открой «Рейд · тест → Сообщить об обновлении» заново.', parse_mode=None)
                return
            recipients = await owner_preferences.raid_update_recipients(db.settings)
            draft = {**draft, 'description': description}
            await state.update_data(raid_announcement=draft)
            await state.set_state(RaidAnnouncementInput.preview)
            text, markup = raid_announcement_preview(draft, len(recipients))
            await message.answer(text, reply_markup=markup, parse_mode=None, protect_content=False)
        except Exception as exc:
            logger.warning('Не удалось подготовить объявление Рейда: %s', type(exc).__name__)
            await message.answer('Не удалось подготовить предпросмотр. Ничего не отправлено; открой форму заново.', parse_mode=None)


async def deliver_raid_announcement(callback, draft, recipients):
    """At most one attempt per snapshot recipient; no automatic retries/resume."""
    sent = unconfirmed = skipped = 0
    saved = True
    try:
        # A callback acknowledgement/UI failure must not lose a confirmed batch
        # after its durable claim. The owner can read the final report instead.
        try:
            await callback.answer('Отправка началась.')
        except Exception as exc:
            logger.warning('Не подтверждена кнопка объявления Рейда: %s', type(exc).__name__)
        text, markup = raid_update_message(draft['description'], draft['build']), raid_update_markup(draft['build'])
        for uid in recipients:
            try:
                doc = await owner_preferences.get_raid_tester(db.settings, uid)
            except Exception as exc:
                # Fail closed on unavailable/corrupt admission; do not broadcast
                # on a stale allowlist or assume the user is still subscribed.
                logger.warning('Отправка Рейда остановлена: %s', type(exc).__name__)
                break
            if not doc or doc['status'] != 'allowed' or not owner_preferences.raid_tester_updates_enabled(doc):
                skipped += 1
                continue
            if await raid_notify(callback.bot, uid, text, markup):
                sent += 1
            else:
                unconfirmed += 1  # A network timeout is NOT proof of non-delivery.
            await asyncio.sleep(.06)  # Below Telegram's global send rate; no retries.
    finally:
        remaining = len(recipients) - sent - unconfirmed - skipped
        try:
            await owner_preferences.finish_raid_announcement(db.settings, draft['token'],
                sent=sent, unconfirmed=unconfirmed, skipped=skipped, remaining=remaining)
        except Exception as exc:
            saved = False
            logger.warning('Не сохранён итог объявления Рейда: %s', type(exc).__name__)
    text = (f'📣 Объявление Рейда · {draft["build"]}\n\n'
            f'Доставлено: {sent}\nДоставка не подтверждена: {unconfirmed}\n'
            f'Пропущено (отказ/нет допуска): {skipped}\nНе завершено: {remaining}\n\n'
            'Автоматических повторов нет. Неподтверждённое сообщение могло дойти; '
            'не повторяй всю рассылку вслепую.')
    if not saved:
        text += '\nИтог не удалось сохранить в settings. Отправка автоматически не повторяется.'
    b = InlineKeyboardBuilder()
    b.button(text='Новое объявление', callback_data='raid_announce:compose')
    b.adjust(1)
    add_nav(b, back='owner_panel:raid_test', back_label='‹ Рейд · тест')
    await edit(callback.message, text, b.as_markup())


@router.callback_query(F.data.startswith('raid_announce:'))
async def raid_announcement_action(callback: types.CallbackQuery, state: FSMContext = None):
    if (not authorized(callback) or not raid_private_user(callback.message, callback.from_user)):
        await callback.answer('Объявления доступны только владельцу в своей личке.', show_alert=True)
        return
    action = (callback.data or '').removeprefix('raid_announce:')
    match = re.fullmatch(r'(preview|edit|cancel|send):([a-f0-9]{16})', action)
    if not state or action != 'compose' and not match:
        await callback.answer('Неизвестная или устаревшая кнопка. Открой объявление заново.', show_alert=True)
        return
    broadcast_lock = False
    try:
        async with _input_state_lock:
            if _raid_broadcast_lock.locked():
                await callback.answer('Предыдущая отправка ещё выполняется. Дождись итога.', show_alert=True)
                return
            if action == 'compose':
                build = raid_build_id()
                if not build:
                    await callback.answer('Номер сборки недоступен. Объявление не создано.', show_alert=True)
                    return
                previous = await owner_preferences.get_raid_announcement(db.settings)
                draft = {'token': secrets.token_hex(8), 'build': build}
                await state.clear()
                await state.set_state(RaidAnnouncementInput.description)
                await state.update_data(raid_announcement=draft)
                text, markup = raid_announcement_prompt(draft, previous)
                await edit(callback.message, text, markup)
                await callback.answer()
                return
            command, token = match[1], match[2]
            current, data = await state.get_state(), await state.get_data()
            draft = data.get('raid_announcement') or {}
            if (current not in (RaidAnnouncementInput.description.state, RaidAnnouncementInput.preview.state)
                    or draft.get('token') != token):
                await callback.answer('Этот черновик уже закрыт или заменён. Ничего не отправлено.', show_alert=True)
                return
            if command == 'cancel':
                await state.clear()
                text, markup = raid_preview_content()
                await edit(callback.message, text, markup)
                await callback.answer('Черновик отменён.')
                return
            if command == 'edit':
                draft = {'token': secrets.token_hex(8), 'build': raid_build_id()}
                if not draft['build']:
                    await callback.answer('Номер сборки недоступен. Ничего не отправлено.', show_alert=True)
                    return
                await state.update_data(raid_announcement=draft)
                await state.set_state(RaidAnnouncementInput.description)
                text, markup = raid_announcement_prompt(draft)
                await edit(callback.message, text, markup)
                await callback.answer()
                return
            if current != RaidAnnouncementInput.preview.state or not draft.get('description'):
                await callback.answer('Сначала пришли описание и проверь предпросмотр.', show_alert=True)
                return
            if draft.get('build') != raid_build_id():
                await callback.answer('Сборка изменилась. Создай новый предпросмотр перед отправкой.', show_alert=True)
                return
            recipients = await owner_preferences.raid_update_recipients(db.settings)
            if command == 'preview':
                text, markup = raid_announcement_preview(draft, len(recipients))
                await edit(callback.message, text, markup)
                await callback.answer()
                return
            if not recipients:
                await callback.answer('Нет допущенных подписанных тестеров. Ничего не отправлено.', show_alert=True)
                return
            if not await owner_preferences.claim_raid_announcement(db.settings, token, draft['build'], len(recipients)):
                await callback.answer('Эта отправка уже начиналась. Автоматического повтора нет.', show_alert=True)
                return
            await _raid_broadcast_lock.acquire()
            broadcast_lock = True
            # Consume confirmation BEFORE any send. Old callbacks/restarts must
            # never resend a partial batch, even if final UI/reporting fails.
            await state.clear()
        await deliver_raid_announcement(callback, draft, recipients)
    except Exception as exc:
        logger.warning('Не удалось выполнить объявление Рейда: %s', type(exc).__name__)
        try:
            await callback.answer('Действие не завершено. Проверь итог; не повторяй рассылку вслепую.', show_alert=True)
        except Exception as answer_error:
            logger.debug('Не удалось показать предупреждение: %s', type(answer_error).__name__)
    finally:
        if broadcast_lock:
            _raid_broadcast_lock.release()


def authorized(callback) -> bool:
    return bool(callback.from_user and callback.from_user.id == ADMIN_ID and callback.message
                and callback.message.chat.type == 'private' and callback.message.chat.id == ADMIN_ID)


async def home_content():
    # Migrate old defaults only when opening the panel, preserving current modes.
    await owner_trial.migrate_panel_defaults(db.db)
    await owner_preferences.migrate(db.settings)
    prefs = await owner_preferences.get(db.settings)
    daily = await owner_daily.enabled(db.db)
    drafts = await owner_trial.drafts_enabled(db.db)
    ready = await db.db.news_owner_drafts.count_documents({'owner_id': ADMIN_ID, 'stage': 'draft_ready'})
    image = 'Cloudflare + OVHcloud' if prefs['image_provider'] == 'cloudflare' else 'Pollinations'
    search = 'включены' if prefs['search_images'] else 'выключены'
    text = ('🎛 Пульт владельца\nПульт › Главная\n\n'
            'Состояние\n'
            f'· Сбор Crypto Sensei и RSS · {"включён" if daily else "выключен"}\n'
            f'· Сценарии · {"включены" if drafts else "выключены"} · готово к проверке: {ready}\n'
            f'· Рисование · {image}\n'
            f'· Картинки в поиске · {search}\n\n'
            'Разделы\n'
            '📰 Новости — сбор, сценарии и выпуски\n'
            '🎬 Видео в чат — отправить своё видео в группу\n'
            '👥 Группы — доступ, расписания и очки\n'
            '🎨 Картинки и поиск — общие настройки всех чатов\n\n'
            'Настройки картинок и поиска действуют во всех чатах. '
            'Модули группы — через /panel в этой группе.\n'
            'В каждом разделе есть «Назад», «Обновить» и «Закрыть».')
    builder = InlineKeyboardBuilder()
    builder.button(text='📰 Новости', callback_data='owner_panel:news')
    builder.button(text='🌍 Общая подборка', callback_data='gnews:home')
    builder.button(text='📝 Сценарии', callback_data='news_panel:scripts')
    builder.button(text='🎞 Вестники и выпуски', callback_data='news_panel:editions')
    builder.button(text='🎬 Видео в чат', callback_data='ovideo:open')
    builder.button(text='📊 Статус', callback_data='owner_panel:status')
    builder.button(text='👥 Группы', callback_data='owner_panel:group_menu')
    builder.button(text='🎨 Картинки и поиск', callback_data='owner_panel:media')
    builder.button(text='❓ Справка', callback_data='owner_panel:help')
    builder.adjust(1, 2, 2, 2, 2)
    builder.row(types.InlineKeyboardButton(text='⚔️ Рейд · тест', callback_data='owner_panel:raid_test'))
    add_nav(builder, refresh='owner_panel:home')
    return text, builder.as_markup()


def section_content(section):
    """Navigation only; existing action callbacks and confirmations stay intact."""
    menus = {
        'group_menu': ('👥 Группы\nПульт › Группы', 'Доступ к группам, расписания отчётов и очки участников.', [
            ('🔐 Доступ к группам', 'owner_panel:groups'),
            ('⏱ Расписания отчётов', 'owner_tools:reports'),
            ('🏅 Очки игрока в группе', 'owner_tools:points'),
        ], 'owner_panel:home'),
        'media': ('🎨 Картинки и поиск\nПульт › Картинки и поиск', 'Настройки действуют во всех чатах.', [
            ('🖌 Рисование', 'owner_panel:images'),
            ('🌐 Картинки в поиске', 'owner_panel:search'),
        ], 'owner_panel:home'),
        'help': ('❓ Справка\nПульт › Справка', 'Всё, что можно сделать, доступно кнопками. '
                          'Команды ниже — резерв, если экран не отвечает.', [
            ('📖 Справка по боту', 'owner_tools:help'),
            ('⌨️ Все команды', 'owner_panel:commands'),
        ], 'owner_panel:home'),
        # Keep the old route usable from already-sent keyboards and video flow.
        'tools': ('🧰 Инструменты\nПульт › Инструменты', 'Выбери раздел.', [
            ('🎬 Видео в чат', 'ovideo:open'),
            ('👥 Группы', 'owner_panel:group_menu'),
        ], 'owner_panel:home'),
    }
    head, lead, actions, back = menus[section]
    text = head + (f'\n\n{lead}' if lead else '')
    b = InlineKeyboardBuilder()
    for label, callback in actions:
        b.button(text=label, callback_data=callback)
    if section == 'help':
        b.button(text='💬 Связь с владельцем', url='https://t.me/boozygrimm')
    b.adjust(1)
    add_nav(b, back=back, refresh='owner_panel:' + section)
    return text, b.as_markup()


# One screen with every reserved text command, grouped the same way as the
# panels. Buttons stay the primary way; this is the printable map for them.
COMMAND_GROUPS = (
    ('Пробники', [('/raid_test', 'открыть локальный Рейд внутри Telegram без рабочих данных')]),
    ('Новости и сценарии', [
        ('/news_prepare ССЫЛКА', 'подготовить сценарий по ссылке, акцент — следующей строкой'),
        ('/news_prepare_many', 'то же для двух-четырёх ссылок'),
        ('/news_discover', 'найти новые публикации в настроенных источниках'),
        ('/news_pick', 'выбрать сюжет из найденного'),
        ('/news_drafts', 'список последних черновиков'),
        ('/news_draft НОМЕР', 'карточка одного черновика'),
        ('/news_source НОМЕР', 'прислать текст источника вместо недоступной статьи'),
        ('/news_retry НОМЕР', 'один повтор подготовки после ошибки редактора'),
        ('/news_edit', 'исправить текст сценария по версии'),
        ('/news_nofocus', 'убрать автоматические области увеличения'),
        ('/news_noimage', 'убрать картинку из черновика'),
        ('/news_novideo', 'убрать присланный видеофрагмент'),
        ('/news_approve НОМЕР ВЕРСИЯ', 'подтвердить рендер конкретной версии'),
        ('/news_daily_status', 'состояние ежедневного сбора'),
        ('/news_collected', 'что уже собрано за день'),
    ]),
    ('Выпуски и вестники', [
        ('/news_program', 'собрать выпуск из готовых черновиков'),
        ('/news_programs', 'список выпусков'),
        ('/news_program_status', 'состояние выпуска'),
        ('/news_program_approve', 'подтвердить рендер выпуска'),
    ]),
    ('Проба и доставка', [
        ('/news_trial', 'поставить свой сценарий в пробу'),
        ('/news_trial_next', 'перейти к следующей пробе'),
        ('/news_trial_status', 'состояние рендера и доставки'),
    ]),
    ('Прежний ручной режим', [
        ('/news, /news_list', 'старый ручной приём новостей'),
        ('/news_ready', 'готовые материалы прежнего режима'),
        ('/news_digest', 'собрать дайджест в старом режиме'),
        ('/news_digest_status', 'состояние старого дайджеста'),
    ]),
)


def commands_content():
    """Show the reserved text commands without dumping them into the chat."""
    lines = ['⌨️ Все команды', 'Пульт › Справка › Все команды', '',
             'Обычно ничего вводить не нужно: каждое действие есть кнопкой в разделе. '
             'Команды ниже остаются резервом — например, если экран не отвечает.', '']
    for title, entries in COMMAND_GROUPS:
        lines.append(f'— {title}')
        for command, purpose in entries:
            lines.append(f'{command} · {purpose}')
        lines.append('')
    lines.append('Команды работают только в личке владельца. В группе действует только /panel.')
    b = InlineKeyboardBuilder()
    b.button(text='📖 Справка по боту', callback_data='owner_tools:help')
    b.button(text='📝 К черновикам', callback_data='news_panel:scripts')
    b.adjust(1)
    add_nav(b, back='owner_panel:help', refresh='owner_panel:commands')
    # Telegram caps one message at 4096 characters; split only if a future
    # entry grows the list past that.
    return '\n'.join(lines), b.as_markup()


async def image_content():
    prefs = await owner_preferences.get(db.settings)
    active = prefs['image_provider']
    text = ('🖌 Рисование\nПульт › Картинки и поиск › Рисование\n\n'
            f'Сейчас: {"Cloudflare + резерв OVHcloud" if active == "cloudflare" else "Pollinations"}.\n'
            'Выбор действует во всех чатах и сам не запускает проверку. Cloudflare требует настроенный доступ. '
            'Проверка сервисов потребует подтверждения и может расходовать квоту.\n\n'
            'Выбери сервис:')
    builder = InlineKeyboardBuilder()
    builder.button(text=('✅ ' if active == 'legacy' else '') + 'Pollinations', callback_data='owner_panel:image_legacy')
    builder.button(text=('✅ ' if active == 'cloudflare' else '') + 'Cloudflare + OVHcloud', callback_data='owner_panel:image_cloudflare')
    builder.button(text='🖼 Нарисовать картинку', callback_data='owner_tools:draw')
    builder.button(text='🩺 Проверить сервисы', callback_data='owner_tools:image_probe')
    builder.adjust(1)
    add_nav(builder, back='owner_panel:media', back_label='‹ Картинки и поиск', refresh='owner_panel:images')
    return text, builder.as_markup()


async def search_content():
    prefs = await owner_preferences.get(db.settings)
    active = prefs['search_images']
    text = ('🌐 Картинки в поиске\nПульт › Картинки и поиск › Поиск\n\n'
            f'Сейчас: {"включены" if active else "выключены"} во всех чатах.\n'
            'При включении бот может добавлять найденную картинку к ответу; '
            'это может расходовать квоту поиска. Картинка не подтверждает достоверность ответа.')
    builder = InlineKeyboardBuilder()
    builder.button(text='⏸ Выключить' if active else '▶️ Включить',
                   callback_data='owner_panel:search_off' if active else 'owner_panel:search_on')
    builder.button(text='🩺 Проверить поиск', callback_data='owner_tools:search_probe')
    builder.button(text='🔎 Проверить по своему запросу', callback_data='owner_tools:search_query')
    builder.adjust(1)
    add_nav(builder, back='owner_panel:media', back_label='‹ Картинки и поиск', refresh='owner_panel:search')
    return text, builder.as_markup()


def _msk_time(value):
    if not isinstance(value, datetime):
        return 'ещё не было'
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)  # Mongo stores UTC.
    return value.astimezone(owner_daily.MSK).strftime('%d.%m.%Y %H:%M МСК')


VIDEO_FRAGMENT = 'news-story-manual-video-v1'
VIDEO_FRAGMENT_NEEDS = ('news-bulletin-v1', 'news-bulletin-video-v1', 'news-bulletin-manual-video-v1')


def video_fragment_status(worker: dict) -> str:
    """Say what the worker lacks instead of only "not supported".

    The order matters, and getting it wrong sent the owner to install ffmpeg
    that was already installed. `news-bulletin-manual-video-v1` is advertised
    only when ffmpeg *and* ffprobe exist, so this capability already proves the
    binaries are there: if it is present and only the story capability is
    missing, the worker runs old code. ffmpeg can only be the culprit when no
    video capability at all is advertised.
    """
    if not worker.get('reachable'):
        return ('воркер не ответил на проверку. Это не значит, что версия старая: '
                'проверь адрес и ключ воркера и что сервис запущен.')
    capabilities = worker.get('capabilities') or set()
    if VIDEO_FRAGMENT in capabilities:
        return 'воркер поддерживает видеофрагмент в одиночном сюжете.'
    missing = [name for name in VIDEO_FRAGMENT_NEEDS if name not in capabilities]
    if missing:
        return ('воркер без протокола видеофрагмента: не хватает ' + ', '.join(missing)
                + '. Нужен обновлённый воркер.')
    # Every bulletin video capability is present, so both binaries exist.
    return ('ffmpeg и ffprobe на месте (иначе не было бы news-bulletin-manual-video-v1), '
            'но запущен старый код воркера: возможность news-story-manual-video-v1 объявляет '
            'только worker.py из свежего main. Нужно обновить код воркера и перезапустить '
            'службу — устанавливать ffmpeg не нужно.')


async def worker_content():
    """Read-only worker check, opened explicitly by the owner."""
    from news_prototype.news_worker_client import NewsWorkerClient
    import os
    lines = ['🎬 Воркер видео\nПульт › Статус › Воркер', '']
    try:
        client = NewsWorkerClient(os.getenv('BENDER_WORKER_URL', 'https://worker.invalid').strip()
                                  or 'https://worker.invalid', os.getenv('BENDER_WORKER_KEY', ''))
    except (ValueError, TypeError):
        lines.append('Воркер не настроен: не заданы адрес или ключ.')
        worker = {'reachable': False, 'configured': False}
    else:
        worker = await client.media_capabilities()
    if worker.get('configured') is False:
        pass
    elif worker.get('reachable'):
        capabilities = sorted(worker.get('capabilities') or set())
        lines.append('Видеофрагмент в сюжете')
        lines.append(video_fragment_status(worker))
        lines.append('')
        lines.append('Объявленные возможности')
        if capabilities:
            lines.extend('· ' + name for name in capabilities)
        else:
            lines.append('· список пуст')
    else:
        lines.append(video_fragment_status(worker))
    lines.append('')
    lines.append('Эта проверка читает только /health воркера и ничего не запускает.')
    b = InlineKeyboardBuilder()
    b.button(text='🔄 Проверить снова', callback_data='owner_panel:worker')
    b.adjust(1)
    add_nav(b, back='owner_panel:status', back_label='‹ Статус', refresh='owner_panel:worker')
    return '\n'.join(lines), b.as_markup()


async def status_content():
    """Read-only snapshot of saved state; it does not probe external services."""
    from news_prototype import general_auto, general_editorial, general_sources
    from handlers.general_news_panel import run_summary
    prefs = await owner_preferences.get(db.settings)
    daily = await owner_daily.enabled(db.db)
    drafts = await owner_trial.drafts_enabled(db.db)
    video_enabled = await owner_trial.runtime_enabled(db.db)
    automatic = await general_auto.enabled(db.db)
    categories = len(general_editorial.LABELS) - len(await general_auto.disabled_categories(db.db))
    sources = await general_sources.list_sources(db.db)
    collected = await db.db.news_general_candidates.count_documents({'hidden': {'$ne': True}})
    ready = await db.db.news_owner_drafts.count_documents({'owner_id': ADMIN_ID, 'stage': 'draft_ready'})
    poll = await db.db.news_general_state.find_one({'_id': 'last_poll'}) or {}
    once = await db.db.news_general_state.find_one({'_id': 'one_shot_last'}) or {}
    last_auto = await db.db.news_general_state.find_one({'_id': 'auto_last'}) or {}
    usage = await general_auto.usage(db.db)
    stage, _ = await owner_trial.status_info(db.db)
    video_status = {
        'not_queued': 'слот свободен', 'admission_incomplete': 'сохранение не завершено',
        'queued': 'в очереди', 'submitting': 'проверяется приём', 'running': 'создаётся',
        'ready_for_review': 'готово к доставке', 'fetching_review': 'скачивается для просмотра',
        'sending_review': 'отправляется владельцу', 'review_sent': 'отправлено владельцу',
        'review_uncertain': 'отправка не подтверждена', 'review_too_large': 'слишком большое видео',
        'review_download_failed': 'ошибка получения видео', 'uncertain': 'приём не подтверждён',
        'interrupted': 'задача прервана', 'failed': 'ошибка', 'audio_too_long': 'слишком длинное аудио',
    }.get(stage, 'неизвестно, нужна проверка')
    statuses = {'no_candidates': 'нет подходящих новостей', 'draft_ready': 'сценарий подготовлен',
        'budget_exhausted': 'исчерпан лимит', 'keys_missing': 'не заданы ключи',
        'drafts_off': 'сценарии выключены', 'timeout_uncertain': 'время истекло, результат не подтверждён',
        'editor_failed': 'ошибка редактора', 'needs_source': 'нужен источник', 'skipped': 'пропущено редактором',
        'id_conflict': 'конфликт номера сценария',
        'uncertain': 'результат не подтверждён', 'already_claimed': 'новость уже обрабатывалась',
        'throttled': 'ожидание следующего интервала', 'busy': 'редактор занят', 'off': 'отбор выключен'}
    image = 'Cloudflare + OVHcloud' if prefs['image_provider'] == 'cloudflare' else 'Pollinations'
    text = ('📊 Статус пульта\nПульт › Статус\n\n'
            'Общая подборка\n'
            f'Автоотбор · {"включён" if automatic else "выключен"}\n'
            f'Категории · {categories} из {len(general_editorial.LABELS)}\n'
            f'Источники · {len(sources)} активных\n'
            f'Сохранено видимых новостей · {collected}\n'
            f'Последний сбор · {_msk_time(poll.get("at"))}\n')
    if poll.get('at'):
        text += f'Ошибок источников при сборе · {len(poll.get("failed") or [])}\n'
    else:
        text += 'Источники сбором ещё не проверялись.\n'
    text += f'Попытки сегодня · {usage} из {general_auto.DAILY_ATTEMPTS} (авто и запуск кнопкой)\n'
    if once.get('at'):
        text += f'Последний запуск кнопкой · {_msk_time(once["at"])}\n{run_summary(once)}\n'
    if last_auto.get('at'):
        text += (f'Последняя проверка отбора · {_msk_time(last_auto["at"])} · '
                 + statuses.get(last_auto.get('status'), 'состояние неизвестно') + '\n')
    text += ('\nСценарии и видео\n'
             f'Подготовка сценариев · {"включена" if drafts else "выключена"}\n'
             f'Черновиков готово к просмотру · {ready}\n'
             f'Режим видео · {"включён" if video_enabled else "выключен"}\n'
             f'Слот видео · {video_status}\n'
             f'Ключи Gemini и Groq · {"оба указаны" if general_auto.keys_ready() else "один или оба не указаны"}\n'
             'Доступность и квоты сервисов этим экраном не проверяются.\n\n'
             'Остальное\n'
             f'Сбор Crypto Sensei и RSS · {"включён" if daily else "выключен"}\n'
             f'Рисование · {image}\n'
             f'Картинки в поиске · {"включены" if prefs["search_images"] else "выключены"}\n\n'
             'Статус — сохранённые данные, а не проверка работы сервера или источников в реальном времени.')
    b = InlineKeyboardBuilder()
    b.button(text='🌍 Общая подборка', callback_data='gnews:home')
    b.button(text='📝 Готовые сценарии', callback_data='news_panel:drafts')
    b.button(text='📡 Crypto Sensei и RSS', callback_data='news_panel:collection')
    b.button(text='🎬 Воркер видео', callback_data='owner_panel:worker')
    if poll.get('failed'):
        b.button(text='⚠️ Ошибки источников', callback_data='gnews:failed:0')
    b.adjust(1)
    add_nav(b, back=HOME_CALLBACK, back_label='‹ Пульт', refresh='owner_panel:status')
    return text, b.as_markup()


async def edit(message, text, markup):
    try:
        await message.edit_text(text, reply_markup=markup, parse_mode=None)
    except TelegramBadRequest as exc:
        if 'message is not modified' not in str(exc).lower():
            raise


async def open_owner_panel(message):
    try:
        text, markup = await home_content()
        await message.answer(text, reply_markup=markup, parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось открыть пульт владельца: %s', type(exc).__name__)
        await message.answer('Не удалось открыть пульт. Попробуй позже.', parse_mode=None)


@router.callback_query(F.data.startswith('owner_panel:'))
async def control_action(callback: types.CallbackQuery, state: FSMContext = None):
    if not authorized(callback):
        await callback.answer('Пульт доступен только владельцу в личке.', show_alert=True)
        return
    action = (callback.data or '').partition(':')[2]
    if state and action in ('home', 'news', 'close', 'tools', 'images', 'search', 'groups',
                            'group_menu', 'media', 'help',
                            'commands', 'worker', 'status', 'raid_test'):
        # Leaving a section must also release an unfinished text input, or the
        # next non-command message would be swallowed by that old form.
        get_data = getattr(state, 'get_data', None)
        async with _input_state_lock:
            if get_data:
                try:
                    await get_data()
                except Exception as refresh_error:
                    logger.debug('Не удалось обновить данные экрана: %s', type(refresh_error).__name__)
            await state.clear()
    try:
        if action == 'close':
            await callback.answer()
            await callback.message.delete()
            return
        if action == 'groups':
            # Reuse the existing owner-only access control menu, without commands.
            from handlers import auth
            message = SimpleNamespace(from_user=callback.from_user, chat=callback.message.chat, answer=callback.message.answer)
            await callback.answer()
            await auth.admin_chats_menu(message)
            return
        if action == 'news':
            from handlers.news_owner_panel import panel_content
            text, markup = await panel_content()
        elif action == 'home':
            text, markup = await home_content()
        elif action == 'raid_test':
            text, markup = raid_preview_content()
            if getattr(callback.message, 'has_protected_content', False):
                # Editing cannot remove the old sendMessage protection flag.
                # Replace that launcher, then best-effort remove the old one.
                await callback.answer()
                await callback.message.answer(text, reply_markup=markup, parse_mode=None, protect_content=False)
                try:
                    await callback.message.delete()
                except TelegramBadRequest:
                    logger.warning('Старый защищённый вход Рейда не удалён; новый отправлен без защиты.')
                return
        elif action == 'status':
            text, markup = await status_content()
        elif action == 'worker':
            text, markup = await worker_content()
        elif action in ('tools', 'group_menu', 'media', 'help'):
            text, markup = section_content(action)
        elif action == 'commands':
            text, markup = commands_content()
        elif action == 'images':
            text, markup = await image_content()
        elif action == 'search':
            text, markup = await search_content()
        elif action in ('image_legacy', 'image_cloudflare'):
            await owner_preferences.set_image_provider(db.settings, action.removeprefix('image_'))
            text, markup = await image_content()
        elif action in ('search_on', 'search_off'):
            await owner_preferences.set_search_images(db.settings, action == 'search_on')
            text, markup = await search_content()
        else:
            await callback.answer('Неизвестная кнопка.', show_alert=True)
            return
        await callback.answer()
        await edit(callback.message, text, markup)
    except Exception as exc:
        logger.warning('Не удалось выполнить действие пульта: %s', type(exc).__name__)
        try:
            await callback.answer('Не удалось выполнить действие. Открой пульт заново.', show_alert=True)
        except Exception as answer_error:
            logger.debug('Не удалось показать предупреждение: %s', type(answer_error).__name__)

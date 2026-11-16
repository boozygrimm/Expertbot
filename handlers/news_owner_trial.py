"""Opt-in single reviewed news trial, exclusively in the owner's private chat."""
import logging
from aiogram import Router, types
from aiogram.filters import Command
from config import ADMIN_ID
from database import db
from news_prototype import owner_trial

router = Router()
logger = logging.getLogger(__name__)
HELP = ('Нужен проверенный сценарий в одном сообщении:\n'
        '/news_trial\nИсточник: https://...\nСтрока: ...\n'
        'Бендер: ...\nКиберсенсей: ...\nБендер: ...\nКиберсенсей: ...\n\n'
        'Каждая реплика — отдельная строка; ссылка и строка должны быть указаны один раз. '
        'Повтор той же пробы не создаёт новую. Публикации нет.')


def authorized(message):
    return bool(message.from_user and message.from_user.id == ADMIN_ID and message.chat.type == 'private')


@router.message(Command('news_trial'))
async def submit_trial(message: types.Message) -> None:
    if not authorized(message):
        return
    if not owner_trial.enabled():
        await message.answer('Изолированная проба выключена или конфликтует с настройками новостей.')
        return
    try:
        url, script = owner_trial.parse_message(message.text or '')
    except ValueError:
        await message.answer(HELP)
        return
    try:
        result = await owner_trial.admit(db.db, message.from_user.id, url, script)
    except ValueError:
        await message.answer('Слот пробы уже занят или данные не совпадают. Проверь /news_trial_status.')
        return
    except Exception as exc:
        logger.warning('Не удалось подтвердить пробную задачу: %s', type(exc).__name__)
        await message.answer('Не удалось подтвердить сохранение. Не повторяй вслепую: проверь /news_trial_status.')
        return
    await message.answer(('Сценарий поставлен в очередь.' if result == 'queued' else 'Эта проба уже сохранена.')
                         + ' Готовое видео придёт владельцу на проверку, не в канал. /news_trial_status')


@router.message(Command('news_trial_status'))
async def trial_status(message: types.Message) -> None:
    if not authorized(message):
        return
    if not owner_trial.enabled():
        await message.answer('Изолированная проба выключена или конфликтует с настройками новостей.')
        return
    try:
        stage = await owner_trial.status(db.db)
    except Exception as exc:
        logger.warning('Не удалось получить состояние пробной задачи: %s', type(exc).__name__)
        await message.answer('Не удалось получить состояние. Не создавай новую пробу вслепую.')
        return
    labels = {'not_queued':'Проба ещё не поставлена в очередь.',
              'admission_incomplete':'Сохранение не завершено; нужна ручная проверка.',
              'queued':'Ожидает свободного воркера.', 'submitting':'Проверяется приём воркером.',
              'running':'Сюжет создаётся.', 'ready_for_review':'Видео готово к доставке.',
              'fetching_review':'Видео скачивается для отправки владельцу.',
              'sending_review':'Идёт отправка; при неизвестном результате проверь личку.',
              'review_sent':'Видео отправлено владельцу.',
              'review_uncertain':'Отправка не подтверждена; сначала проверь личку, автоматически не повторяется.',
              'review_too_large':'Видео слишком велико для стандартной отправки ботом.',
              'review_download_failed':'Не удалось получить видео; нужна ручная проверка.',
              'uncertain':'Приём воркером не подтверждён; нужна ручная проверка.',
              'interrupted':'Воркер прерван; нужна ручная проверка.',
              'failed':'Подготовка не удалась; нужна ручная проверка.'}
    await message.answer(labels.get(stage, 'Неизвестное состояние; нужна ручная проверка.'))

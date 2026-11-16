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
        stage, finished_id = await owner_trial.status_info(db.db)
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
    text = labels.get(stage, 'Неизвестное состояние; нужна ручная проверка.')
    if stage == 'queued':
        try:
            job = await db.db.news_owner_trial_jobs.find_one({'stage': 'queued', '$or': [
                {'script.image_b64': {'$exists': True}}, {'kind': 'program'}]})
            if job:
                import os
                from news_prototype.news_worker_client import NewsWorkerClient
                client = NewsWorkerClient(os.getenv('BENDER_WORKER_URL', ''), os.getenv('BENDER_WORKER_KEY', ''))
                if job.get('kind') == 'program':
                    if not await client.supports_programs():
                        text += '\nВоркер сейчас не подтверждает поддержку выпусков; задача остаётся в очереди.'
                elif not await client.supports_images():
                    text += '\nВоркер сейчас не подтверждает поддержку картинки; задача останется в очереди без повторной отправки.'
        except Exception:
            # Status must remain available even when worker health is unreachable.
            text += '\nНе удалось проверить поддержку изображения воркером.'
    if stage == 'review_sent' and finished_id:
        text += ('\nДля новой пробы освободить слот вручную:\n'
                 f'/news_trial_next {finished_id}')
    await message.answer(text)


@router.message(Command('news_trial_next'))
async def next_trial(message: types.Message) -> None:
    if not authorized(message):
        return
    if not owner_trial.enabled():
        await message.answer('Изолированная проба выключена или конфликтует с настройками новостей.')
        return
    parts = (message.text or '').split()
    if len(parts) != 2:
        await message.answer('Нужна команда с идентификатором из /news_trial_status.')
        return
    try:
        result = await owner_trial.open_next(db.db, message.from_user.id, parts[1])
    except ValueError:
        await message.answer('Неверный идентификатор. Скопируй команду из /news_trial_status.')
        return
    except Exception as exc:
        logger.warning('Не удалось подтвердить освобождение слота: %s', type(exc).__name__)
        await message.answer('Не удалось подтвердить освобождение. Проверь /news_trial_status.')
        return
    labels = {
        'opened': 'Слот свободен. Предыдущий ролик сохранён; новый сценарий ещё не отправлен.',
        'already_open': 'Слот уже свободен; повторной отправки предыдущего ролика не было.',
        'not_allowed': 'Слот нельзя освободить: идентификатор не совпадает или доставка не подтверждена.',
        'changed': 'Состояние изменилось. Проверь /news_trial_status.',
    }
    await message.answer(labels.get(result, 'Состояние не подтверждено. Проверь /news_trial_status.'))

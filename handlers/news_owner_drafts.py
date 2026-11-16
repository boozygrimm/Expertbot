"""Private owner-only news preparation, editing, and explicit approval."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from aiogram import Router, types
from aiogram.filters import Command
from config import ADMIN_ID
from database import db
from news_prototype import article_fetch, editor_client, owner_drafts, owner_trial, owner_program, news_image_fetch

router = Router()
logger = logging.getLogger(__name__)


def enabled() -> bool:
    return os.getenv('NEWS_OWNER_DRAFT_ENABLED', '').strip() == '1' and owner_trial.enabled()


def authorized(message) -> bool:
    return bool(message.from_user and message.from_user.id == ADMIN_ID and message.chat.type == 'private')


async def make_draft(candidate):
    from ai_providers import fire_gemini_key
    from models import GEMINI_CHAT_MODEL
    return await editor_client.draft(candidate, fire_gemini_key(), GEMINI_CHAT_MODEL)


async def analyze_image(image_b64):
    from ai_providers import fire_gemini_key
    from models import GEMINI_CHAT_MODEL
    from news_prototype import image_vision
    return await image_vision.analyze(image_b64, fire_gemini_key(), GEMINI_CHAT_MODEL)


def _id(value: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]{0,14}', value):
        raise ValueError('Неверный номер')
    return int(value)


async def show(message, draft_id: int, *, for_program: bool = False) -> None:
    item = await owner_drafts.get(db.db, message.from_user.id, draft_id)
    if not item:
        await message.answer('Черновик не найден.', parse_mode=None)
        return
    stage = item.get('stage')
    if stage == 'draft_ready':
        editable = owner_drafts.editable_message(draft_id, item['url'], item['script'])
        await message.answer('Черновик непроверен: сверьте смысл, факты, шутки и строку с источником. '
                             'Для правки скопируйте следующее сообщение, исправьте и отправьте его боту. '
                             + ('После проверки соберите выпуск из нужных черновиков.' if for_program else
                                'Для рендера подтвердите текущую версию отдельной командой.'), parse_mode=None)
        await message.answer(editable, parse_mode=None, disable_web_page_preview=True)
        analysis = item.get('image_analysis')
        if analysis == 'analyzed':
            await message.answer('Распознавание картинки (может ошибаться, сверьте с оригиналом):\n'
                                 + item.get('image_description', ''), parse_mode=None)
        elif analysis == 'unavailable':
            await message.answer('Не удалось распознать картинку; сценарий подготовлен по тексту. '
                                 'Проверьте, нет ли важных сведений на изображении.', parse_mode=None)
        image = item['script'].get('image_b64')
        if image:
            from news_prototype.news_image_codec import decode_thumbnail
            label = 'кадр-превью видео' if item.get('image_kind') == 'video_thumbnail' else 'изображение поста'
            await message.answer_photo(types.BufferedInputFile(decode_thumbnail(image), filename='source.jpg'),
                                       caption=f'Найдено {label}. Оно появится в ролике крупным планом. '
                                               'Проверьте содержимое и кадрирование перед публикацией. '
                                               f'Убрать: /news_noimage {draft_id} {item["revision"]}', parse_mode=None)
            regions = item['script'].get('image_regions')
            if regions:
                from news_prototype.news_image_codec import review_preview
                await message.answer_photo(types.BufferedInputFile(review_preview(image, regions), filename='focus.jpg'),
                                           caption='Рамки показывают области для увеличения. '
                                                   'Если они выбраны неудачно, используйте обычное кадрирование: '
                                                   f'/news_nofocus {draft_id} {item["revision"]}', parse_mode=None)
        else:
            await message.answer('Картинка не найдена или недоступна — ролик будет без неё.', parse_mode=None)
        if not for_program:
            await message.answer(f'/news_approve {draft_id} {item["revision"]}', parse_mode=None)
        return
    labels = {
        'new': 'Запрос сохранён, подготовка ещё не началась.',
        'fetching': 'Читаю источник; при зависании не запускайте повтор вслепую.',
        'editing': 'Редактор готовит сценарий; при зависании не запускайте повтор вслепую.',
        'needs_source': f'Источник недоступен или текста мало. Пришлите текст одним сообщением:\n/news_source {draft_id}\nТекст источника',
        'skipped': 'Редактор отказался от сюжета: информации недостаточно.',
        'editor_failed': 'Не удалось подготовить сценарий. Автоматического повтора нет.',
        'approving': 'Подтверждение обрабатывается. Проверьте /news_trial_status, не повторяйте вслепую.',
        'approval_uncertain': 'Результат постановки не подтверждён. Проверьте /news_trial_status; не повторяйте вслепую.',
        'queued': 'Сценарий поставлен в очередь. Состояние видео: /news_trial_status.',
        'already_queued': 'Эта версия уже сохранялась; повторного рендера не запущено. /news_trial_status',
    }
    await message.answer(labels.get(stage, 'Неизвестное состояние; нужна проверка.'), parse_mode=None)


@router.message(Command('news_prepare'))
async def prepare(message: types.Message) -> None:
    if not authorized(message):
        return
    if not enabled():
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        url, accent = owner_drafts.prepare_input(message.text or '')
        created = await owner_drafts.create(db.db, message.from_user.id, message.message_id, url, accent)
    except ValueError:
        await message.answer('Используйте /news_prepare https://адрес/поста\nАкцент: что важно', parse_mode=None)
        return
    except Exception as exc:
        logger.warning('Не удалось сохранить запрос черновика: %s', type(exc).__name__)
        await message.answer('Сохранение не подтверждено. Проверьте /news_drafts; не повторяйте вслепую.', parse_mode=None)
        return
    draft_id = message.message_id
    if created:
        try:
            await message.answer(f'Готовлю черновик. Номер: {draft_id}. Это использует квоту Gemini; '
                                 'рендера и публикации пока нет. Проверка: /news_draft ' + str(draft_id), parse_mode=None)
        except Exception:
            # A lost informational message does not justify another editor call.
            pass
    try:
        await owner_drafts.process_new(db.db, message.from_user.id, draft_id, article_fetch.fetch_article,
                                       make_draft, news_image_fetch.fetch_thumbnail, analyze_image)
        await show(message, draft_id)
    except Exception as exc:
        logger.warning('Не удалось подтвердить состояние черновика: %s', type(exc).__name__)
        await message.answer(f'Состояние не подтверждено. Проверьте /news_draft {draft_id}; не повторяйте вслепую.', parse_mode=None)


async def prepare_urls(message: types.Message, urls: list[str], accent: str = '') -> None:
    ids = owner_drafts.batch_draft_ids(message.message_id, len(urls))
    for draft_id, url in zip(ids, urls):
        await owner_drafts.create(db.db, message.from_user.id, draft_id, url, accent)
    await message.answer('Готовлю по одному черновику на ссылку. Используется квота Gemini; '
                         'видео пока не создаётся.', parse_mode=None)
    results = await asyncio.gather(*(owner_drafts.process_new(
        db.db, message.from_user.id, draft_id, article_fetch.fetch_article,
        make_draft, news_image_fetch.fetch_thumbnail, analyze_image) for draft_id in ids),
        return_exceptions=True)
    for draft_id, result in zip(ids, results):
        if isinstance(result, Exception):
            logger.warning('Не удалось подготовить черновик: %s', type(result).__name__)
            await message.answer(f'Состояние черновика не подтверждено: /news_draft {draft_id}.', parse_mode=None)
        else:
            await show(message, draft_id, for_program=True)
    await message.answer('Когда проверите все готовые черновики, соберите выпуск:\n/news_program '
                         + ' '.join(str(draft_id) for draft_id in ids)
                         + '\nЕсли источника не хватает, сначала пришлите текст через /news_source.', parse_mode=None)


@router.message(Command('news_prepare_many'))
async def prepare_many(message: types.Message) -> None:
    if not authorized(message):
        return
    if not enabled():
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        urls, accent = owner_drafts.prepare_many_input(message.text or '')
        await prepare_urls(message, urls, accent)
    except ValueError:
        await message.answer('Укажите команду /news_prepare_many, затем с новой строки от двух до четырёх '
                             'разных ссылок и, при желании, строку Акцент: ...', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось подтвердить черновики: %s', type(exc).__name__)
        await message.answer('Подготовка не подтверждена. Проверьте /news_drafts; повторяйте только отсутствующие ссылки.', parse_mode=None)


@router.message(Command('news_discover'))
async def discover(message: types.Message) -> None:
    if not authorized(message):
        return
    if not enabled():
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    if len((message.text or '').split()) != 1:
        await message.answer('Используйте /news_discover без параметров.', parse_mode=None)
        return
    from news_prototype import owner_discovery
    try:
        batch = message.message_id
        snapshot = await owner_discovery.get_snapshot(db.db, message.from_user.id, batch)
        if not snapshot:
            await message.answer('Собираю новости за последние сутки. Для перевода заголовков может использоваться '
                                 'квота Gemini; сценарии и рендер не запускаются.', parse_mode=None)
            items, failed = await owner_discovery.collect()
            if items:
                from ai_providers import fire_gemini_key
                from models import GEMINI_CHAT_MODEL
                translations = await owner_discovery.translate_titles(items, fire_gemini_key(), GEMINI_CHAT_MODEL)
                if translations:
                    for item, translated in zip(items, translations):
                        item['translated_title'] = translated
            snapshot = await owner_discovery.save_snapshot(db.db, message.from_user.id, batch, items, failed)
        items = snapshot['items']
        if not items:
            if len(snapshot.get('failed', [])) == len(owner_discovery.SOURCES):
                text = 'Не удалось проверить источники.'
            else:
                text = 'За последние сутки новостей не найдено.'
            if snapshot.get('failed'):
                text += ' Недоступны источники: ' + ', '.join(snapshot['failed']) + '.'
            await message.answer(text, parse_mode=None)
            return
        header = 'Новости за последние сутки. Заголовки могут быть автоматически переведены; проверьте источник.\n'
        if snapshot.get('failed'):
            header += 'Недоступны: ' + ', '.join(snapshot['failed']) + '.\n'
        lines = []
        for index, item in enumerate(items, 1):
            title = item.get('translated_title', item['title'])
            date = item['published_at'][:16].replace('T', ' ') + ' UTC'
            lines.append(f'{index}. {title}\n{item["source"]} · {date}\n{item["url"]}\n')
        part = header
        for line in lines:
            if len((part + line).encode('utf-16-le')) // 2 > 3500:
                await message.answer(part, parse_mode=None, disable_web_page_preview=True)
                part = ''
            part += line
        if part:
            await message.answer(part, parse_mode=None, disable_web_page_preview=True)
        if len(items) < 2:
            await message.answer('Найден только один вариант. Для выпуска добавьте другую ссылку вручную '
                                 'через /news_prepare_many или обновите подборку позже.', parse_mode=None)
        else:
            await message.answer(f'Выбери от двух до четырёх новостей из подборки №{batch}:\n'
                                 f'/news_pick {batch} 1 2\n'
                                 'Замени 1 и 2 на нужные номера. Сценарии и ролик пока не созданы.', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось получить подборку: %s', type(exc).__name__)
        await message.answer('Не удалось получить подборку. Попробуйте позже.', parse_mode=None)


@router.message(Command('news_pick'))
async def pick(message: types.Message) -> None:
    if not authorized(message):
        return
    if not enabled():
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    from news_prototype import owner_discovery
    try:
        parts = (message.text or '').split()
        if not 4 <= len(parts) <= 6:
            raise ValueError
        batch = _id(parts[1])
        indices = [_id(value) for value in parts[2:]]
        snapshot = await owner_discovery.get_snapshot(db.db, message.from_user.id, batch)
        urls = owner_discovery.choose(snapshot, indices)
        await prepare_urls(message, urls)
    except (ValueError, article_fetch.ArticleError):
        await message.answer('Укажите номер подборки и от двух до четырёх разных номеров новостей: '
                             '/news_pick НОМЕР_ПОДБОРКИ 1 2', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось подготовить выбранные новости: %s', type(exc).__name__)
        await message.answer('Состояние не подтверждено. Проверьте /news_drafts; не повторяйте вслепую.', parse_mode=None)


@router.message(Command('news_drafts'))
async def list_drafts(message: types.Message) -> None:
    if not authorized(message):
        return
    if not enabled():
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        items = await db.db.news_owner_drafts.find({'owner_id': message.from_user.id}).sort(
            'created_at', -1).limit(5).to_list(length=5)
        if not items:
            await message.answer('Черновиков пока нет.', parse_mode=None)
            return
        lines = [f'/news_draft {item["draft_id"]} — {item.get("stage", "unknown")}' for item in items]
        await message.answer('Последние черновики:\n' + '\n'.join(lines), parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось получить черновики: %s', type(exc).__name__)
        await message.answer('Не удалось получить список черновиков.', parse_mode=None)


@router.message(Command('news_draft'))
async def get_draft(message: types.Message) -> None:
    if not authorized(message):
        return
    if not enabled():
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        parts = (message.text or '').split()
        if len(parts) != 2:
            raise ValueError
        await show(message, _id(parts[1]))
    except ValueError:
        await message.answer('Укажите номер: /news_draft НОМЕР', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось получить черновик: %s', type(exc).__name__)
        await message.answer('Не удалось получить черновик.', parse_mode=None)


@router.message(Command('news_source'))
async def provide_source(message: types.Message) -> None:
    if not authorized(message):
        return
    if not enabled():
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        first, sep, text = (message.text or '').partition('\n')
        parts = first.split()
        if not sep or len(parts) != 2:
            raise ValueError
        draft_id = _id(parts[1])
        result = await owner_drafts.provide_source(db.db, message.from_user.id, draft_id, text, make_draft)
        if result in ('not_waiting', 'changed'):
            await message.answer('Этот черновик не ожидает текста. Проверьте /news_draft ' + str(draft_id), parse_mode=None)
            return
        await show(message, draft_id)
    except ValueError:
        await message.answer('Укажите номер и достаточно полный текст источника: /news_source НОМЕР, затем текст с новой строки.', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось подтвердить источник: %s', type(exc).__name__)
        await message.answer('Состояние не подтверждено. Проверьте /news_drafts; не повторяйте вслепую.', parse_mode=None)


@router.message(Command('news_edit'))
async def edit_draft(message: types.Message) -> None:
    if not authorized(message):
        return
    if not enabled():
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        draft_id, expected, url, script = owner_drafts.parse_edit(message.text or '')
        result = await owner_drafts.edit(db.db, message.from_user.id, draft_id, expected, url, script)
        if result == 'stale':
            await message.answer(f'Версия изменилась. Сначала откройте /news_draft {draft_id}.', parse_mode=None)
        else:
            await message.answer('Изменения сохранены.' if result == 'saved' else 'Текст не изменился.', parse_mode=None)
            await show(message, draft_id)
    except ValueError:
        await message.answer('Проверьте формат, источник и реплики. Исходную ссылку менять нельзя.', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось подтвердить правку: %s', type(exc).__name__)
        await message.answer('Сохранение не подтверждено. Проверьте /news_drafts.', parse_mode=None)


@router.message(Command('news_nofocus'))
async def remove_focus(message: types.Message) -> None:
    if not authorized(message):
        return
    if not enabled():
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        parts = (message.text or '').split()
        if len(parts) != 3:
            raise ValueError
        draft_id = _id(parts[1])
        result = await owner_drafts.remove_focus(db.db, message.from_user.id, draft_id, parts[2])
        if result == 'stale':
            await message.answer(f'Версия изменилась. Проверьте /news_draft {draft_id}.', parse_mode=None)
        else:
            await message.answer('Автоматические области убраны.' if result == 'saved' else 'Областей не было.', parse_mode=None)
            await show(message, draft_id)
    except ValueError:
        await message.answer('Скопируйте команду из черновика.', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось изменить кадрирование: %s', type(exc).__name__)
        await message.answer('Сохранение не подтверждено. Проверьте /news_drafts.', parse_mode=None)


@router.message(Command('news_noimage'))
async def remove_image(message: types.Message) -> None:
    if not authorized(message):
        return
    if not enabled():
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        parts = (message.text or '').split()
        if len(parts) != 3:
            raise ValueError
        draft_id = _id(parts[1])
        result = await owner_drafts.remove_image(db.db, message.from_user.id, draft_id, parts[2])
        if result == 'stale':
            await message.answer(f'Версия изменилась. Проверьте /news_draft {draft_id}.', parse_mode=None)
        else:
            await message.answer('Изображение убрано.' if result == 'saved' else 'Изображения нет.', parse_mode=None)
            await show(message, draft_id)
    except ValueError:
        await message.answer('Скопируйте команду удаления картинки из черновика.', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось подтвердить удаление изображения: %s', type(exc).__name__)
        await message.answer('Сохранение не подтверждено. Проверьте /news_drafts.', parse_mode=None)


@router.message(Command('news_approve'))
async def approve_draft(message: types.Message) -> None:
    if not authorized(message):
        return
    if not enabled():
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        parts = (message.text or '').split()
        if len(parts) != 3:
            raise ValueError
        draft_id = _id(parts[1])
        item = await owner_drafts.get(db.db, message.from_user.id, draft_id)
        if item and item.get('stage') == 'draft_ready' and item.get('revision') == parts[2] and item.get('script', {}).get('image_b64'):
            from news_prototype.news_worker_client import NewsWorkerClient
            try:
                client = NewsWorkerClient(os.getenv('BENDER_WORKER_URL', ''), os.getenv('BENDER_WORKER_KEY', ''))
                supported = await client.supports_images()
                focus_supported = (not item['script'].get('image_regions') or await client.supports_image_focus())
            except (ValueError, OSError):
                supported = False
            if not supported:
                await message.answer(f'Воркер не подтвердил поддержку картинки. Рендер не запущен. '
                                     f'Можно убрать её командой /news_noimage {draft_id} {parts[2]} '
                                     'или сначала обновить воркер.', parse_mode=None)
                return
            if not focus_supported:
                await message.answer('Воркер не подтвердил поддержку выбранных областей картинки. '
                                     'Обновите воркер; рендер не запущен.', parse_mode=None)
                return
        result = await owner_drafts.approve(db.db, message.from_user.id, draft_id, parts[2])
    except ValueError:
        await message.answer('Скопируйте команду подтверждения из /news_draft НОМЕР.', parse_mode=None)
        return
    except Exception as exc:
        logger.warning('Не удалось подтвердить постановку: %s', type(exc).__name__)
        await message.answer('Состояние не подтверждено. Проверьте /news_trial_status; не повторяйте вслепую.', parse_mode=None)
        return
    labels = {
        'queued': 'Проверенный сценарий поставлен в общую очередь. Видео придёт только владельцу. /news_trial_status',
        'already_queued': 'Эта версия уже сохранялась; повторный рендер не запущен. /news_trial_status',
        'stale': f'Версия изменилась. Проверьте /news_draft {draft_id}.',
        'not_ready': f'Черновик не готов к подтверждению. Проверьте /news_draft {draft_id}.',
        'trial_busy': 'Предыдущая проба ещё не завершена или её результат неизвестен. Проверьте /news_trial_status.',
        'changed': f'Состояние изменилось. Проверьте /news_draft {draft_id} и /news_trial_status.',
        'approval_uncertain': 'Постановка не подтверждена. Проверьте /news_trial_status; не повторяйте вслепую.',
    }
    await message.answer(labels.get(result, 'Состояние не подтверждено. Проверьте /news_trial_status.'), parse_mode=None)


async def show_program(message, program_id: int) -> None:
    program = await owner_program.get(db.db, message.from_user.id, program_id)
    if not program:
        await message.answer('Выпуск не найден.', parse_mode=None)
        return
    stage = program.get('stage')
    if stage != 'ready_for_review':
        labels = {
            'approving': 'Подтверждение обрабатывается. Проверьте /news_trial_status.',
            'approval_uncertain': 'Постановка не подтверждена. Проверьте /news_trial_status.',
            'queued': 'Выпуск поставлен в очередь. Состояние: /news_trial_status.',
            'already_queued': 'Этот выпуск уже сохранялся; повторного рендера нет. /news_trial_status',
        }
        await message.answer(labels.get(stage, 'Неизвестное состояние выпуска.'), parse_mode=None)
        return
    await message.answer('Проверьте все сюжеты и картинки ниже. Если исправите один из черновиков, '
                         'создайте выпуск заново командой /news_program с теми же номерами. '
                         'Пока рендера нет.', parse_mode=None)
    for index, story in enumerate(program['stories'], 1):
        await message.answer(f'Сюжет {index} из {len(program["stories"])}', parse_mode=None)
        await message.answer(owner_drafts.editable_message(story['draft_id'], story['url'], story['script']),
                             parse_mode=None, disable_web_page_preview=True)
        image = story['script'].get('image_b64')
        if image:
            from news_prototype.news_image_codec import decode_thumbnail
            label = 'превью видео' if story.get('image_kind') == 'video_thumbnail' else 'картинка поста'
            await message.answer_photo(types.BufferedInputFile(decode_thumbnail(image), filename='source.jpg'),
                                       caption=f'Сюжет {index}: {label}. Убрать: '
                                               f'/news_noimage {story["draft_id"]} {story["revision"]}', parse_mode=None)
            regions = story['script'].get('image_regions')
            if regions:
                from news_prototype.news_image_codec import review_preview
                await message.answer_photo(types.BufferedInputFile(review_preview(image, regions), filename='focus.jpg'),
                                           caption=f'Сюжет {index}: автоматически выбранные области. '
                                                   f'Отменить: /news_nofocus {story["draft_id"]} {story["revision"]}',
                                           parse_mode=None)
        else:
            await message.answer('Этот сюжет будет без картинки.', parse_mode=None)
    await message.answer(f'Если все сюжеты проверены, подтвердите один общий рендер:\n'
                         f'/news_program_approve {program_id} {program["revision"]}', parse_mode=None)


@router.message(Command('news_program'))
async def create_program(message: types.Message) -> None:
    if not authorized(message):
        return
    if not enabled():
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        parts = (message.text or '').split()
        if not 3 <= len(parts) <= 5:
            raise ValueError
        ids = [_id(part) for part in parts[1:]]
        await owner_program.create(db.db, message.from_user.id, message.message_id, ids)
        await show_program(message, message.message_id)
    except ValueError:
        await message.answer('Укажите от двух до четырёх разных готовых черновиков: '
                             '/news_program НОМЕР1 НОМЕР2. Номера видны в /news_drafts.', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось подтвердить выпуск: %s', type(exc).__name__)
        await message.answer(f'Состояние не подтверждено. Проверьте /news_program_status {message.message_id}.', parse_mode=None)


@router.message(Command('news_programs'))
async def list_programs(message: types.Message) -> None:
    if not authorized(message):
        return
    if not enabled():
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        programs = await db.db.news_owner_programs.find({'owner_id': message.from_user.id}).sort(
            'created_at', -1).limit(5).to_list(length=5)
        if not programs:
            await message.answer('Выпусков пока нет.', parse_mode=None)
            return
        lines = [f'/news_program_status {item["program_id"]} — {item.get("stage", "unknown")}' for item in programs]
        await message.answer('Последние выпуски:\n' + '\n'.join(lines), parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось получить список выпусков: %s', type(exc).__name__)
        await message.answer('Не удалось получить список выпусков.', parse_mode=None)


@router.message(Command('news_program_status'))
async def get_program(message: types.Message) -> None:
    if not authorized(message):
        return
    if not enabled():
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        parts = (message.text or '').split()
        if len(parts) != 2:
            raise ValueError
        await show_program(message, _id(parts[1]))
    except ValueError:
        await message.answer('Укажите номер: /news_program_status НОМЕР', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось получить выпуск: %s', type(exc).__name__)
        await message.answer('Не удалось получить выпуск.', parse_mode=None)


@router.message(Command('news_program_approve'))
async def approve_program(message: types.Message) -> None:
    if not authorized(message):
        return
    if not enabled():
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        parts = (message.text or '').split()
        if len(parts) != 3 or not owner_drafts.REVISION.fullmatch(parts[2]):
            raise ValueError
        program_id = _id(parts[1])
    except ValueError:
        await message.answer('Скопируйте команду подтверждения из выпуска.', parse_mode=None)
        return
    try:
        program = await owner_program.get(db.db, message.from_user.id, program_id)
        if program and program.get('stage') == 'ready_for_review' and program.get('revision') == parts[2]:
            from news_prototype.news_worker_client import NewsWorkerClient
            try:
                client = NewsWorkerClient(os.getenv('BENDER_WORKER_URL', ''), os.getenv('BENDER_WORKER_KEY', ''))
                supported = await client.supports_programs()
                needs_focus = any(story.get('script', {}).get('image_regions') for story in program.get('stories', []))
                focus_supported = not needs_focus or await client.supports_image_focus()
            except (ValueError, OSError):
                supported = False
            if not supported:
                await message.answer('Воркер пока не подтвердил поддержку выпусков. Рендер не запущен.', parse_mode=None)
                return
            if not focus_supported:
                await message.answer('Воркер не подтвердил поддержку выбранных областей картинки. '
                                     'Обновите воркер; рендер не запущен.', parse_mode=None)
                return
        result = await owner_program.approve(db.db, message.from_user.id, program_id, parts[2])
    except ValueError as exc:
        logger.warning('Не удалось проверить данные выпуска: %s', type(exc).__name__)
        await message.answer(f'Не удалось проверить данные выпуска. Откройте /news_program_status {program_id}.', parse_mode=None)
        return
    except Exception as exc:
        logger.warning('Не удалось подтвердить выпуск: %s', type(exc).__name__)
        await message.answer('Состояние не подтверждено. Проверьте /news_trial_status.', parse_mode=None)
        return
    labels = {
        'queued': 'Выпуск поставлен в общую очередь. Готовый ролик придёт только владельцу. /news_trial_status',
        'already_queued': 'Этот выпуск уже сохранялся; повторный рендер не запущен. /news_trial_status',
        'stale': 'Версия выпуска не совпадает. Проверьте /news_program_status ' + str(program_id),
        'stale_draft': 'Черновик изменился. Создайте выпуск заново с актуальными номерами.',
        'not_ready': 'Выпуск не готов к подтверждению. Проверьте /news_program_status ' + str(program_id),
        'trial_busy': 'Предыдущая проба ещё не завершена или её результат неизвестен. /news_trial_status',
        'changed': 'Состояние изменилось. Проверьте /news_trial_status.',
        'approval_uncertain': 'Постановка не подтверждена. Проверьте /news_trial_status.',
    }
    await message.answer(labels.get(result, 'Состояние не подтверждено. Проверьте /news_trial_status.'), parse_mode=None)

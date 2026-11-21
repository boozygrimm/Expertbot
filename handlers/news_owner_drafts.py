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


def authorized(message) -> bool:
    return bool(message.from_user and message.from_user.id == ADMIN_ID and message.chat.type == 'private')


SOURCE_FAILURE_LABELS = {
    'grouped_post': 'Пост входит в альбом (видео или несколько картинок), и Telegram не отдал '
                    'его текст отдельной карточкой — даже в режиме ?single.',
    'no_text': 'В публичном посте нет текста, доступного без входа в аккаунт: только медиа.',
    'too_little_text': 'Текста в источнике слишком мало для сценария.',
    'unavailable': 'Источник недоступен или удалён.',
    'article_missing': 'Не удалось выделить текст статьи на странице.',
    'not_html': 'Ссылка ведёт не на HTML-страницу.',
    'too_large': 'Страница или пост слишком большие для разбора.',
    'network': 'Не удалось безопасно получить страницу: сеть, DNS или тайм-аут.',
    'unknown': 'Причину установить не удалось.',
}


def source_failure_text(item: dict) -> str:
    """Explain a stored source-fetch reason; older drafts have none."""
    error = item.get('source_error')
    if not isinstance(error, dict) or not isinstance(error.get('code'), str):
        return ''
    return SOURCE_FAILURE_LABELS.get(error['code'], SOURCE_FAILURE_LABELS['unknown'])


def editor_failure_text(item: dict) -> str:
    """Explain a stored, allowlisted diagnostic; never show provider output.

    Mirrors the bulletin wording so both news flows read the same, and keeps
    older failures (no stored code) explicitly labelled as unknown.
    """
    labels = {
        'configuration': 'Не настроен ключ или модель Gemini.',
        'timeout': 'Время ожидания Gemini истекло; результат запроса неизвестен.',
        'network': 'Не удалось получить ответ Gemini; результат запроса неизвестен.',
        'http': 'Gemini вернул HTTP-ошибку.',
        'response_too_large': 'Ответ Gemini превысил допустимый размер.',
        'max_tokens': 'Gemini израсходовал лимит вывода на рассуждение и не дошёл до сценария.',
        'incomplete_response': 'Gemini вернул незавершённый ответ: JSON обрывается '
                               'или не хватило лимита вывода.',
        'invalid_response': 'Gemini вернул ответ без пригодного текста '
                            '(пустые части или один лишь блок рассуждения).',
        'invalid_schema': 'Gemini вернул JSON, но не по схеме сценария: '
                          'не те поля, типы реплик или ведущие. Это сбой модели, '
                          'а не источника — повтори подготовку.',
        'source_invalid': 'Сохранённая карточка источника повреждена; '
                          'пришли текст источника заново.',
        'source_short': 'В источнике слишком мало текста для сценария; '
                        'пришли дополнительный текст или другую ссылку.',
        'refused': 'Gemini отказался обрабатывать этот материал (фильтр безопасности), '
                   'и повтор того же запроса даст тот же результат. '
                   'Нужен другой источник или пересказ без спорных формулировок.',
        'empty_answer': 'Gemini вернул пустой ответ: рассуждение или фильтр съели весь лимит вывода. '
                        'Повтор может помочь, но не гарантирован.',
        'invalid_json': 'Редактор не вернул корректный JSON.',
        'evidence': 'Цитата отсутствует или не совпала с текстом источника.',
        'speakers': 'В сценарии должны участвовать оба ведущих.',
        'too_long': 'Сценарий или число реплик превысили допустимый предел.',
        'validation': 'Ответ редактора не прошёл проверку формата или ограничений.',
        'save': 'Не удалось подтвердить сохранение результата.',
        'unknown': 'Причину не удалось определить.',
    }
    error = item.get('editor_error')
    if not isinstance(error, dict):
        return 'Причина этой ошибки не записана предыдущей версией бота.'
    code = error.get('code')
    if not isinstance(code, str) or code not in labels:
        return 'Причина не распознана: код диагностики неизвестен.'
    text = labels[code]
    status = error.get('http_status')
    if code == 'http' and type(status) is int and 100 <= status <= 599:
        text += f' Код HTTP: {status}.'
        if status == 429:
            text += ' Возможно, исчерпана квота или превышен лимит запросов.'
        elif status in (401, 403):
            text += ' Проверьте ключ и доступ к модели.'
        elif status == 404:
            text += ' Проверьте доступность настроенной модели.'
        elif 500 <= status <= 599:
            text += ' Сервис временно недоступен или вернул ошибку.'
    # Where exactly it broke, in a closed vocabulary. Without this a repeated
    # "bad format" says nothing about which check refused the answer.
    where = {
        'response:not_json': 'тело ответа API не JSON',
        'response:not_object': 'тело ответа API не объект',
        'response:bad_encoding': 'тело ответа API не в UTF-8',
        'response:no_candidates': 'в ответе нет кандидатов',
        'response:no_content': 'у кандидата нет поля content',
        'response:no_parts': 'в ответе нет текстовых частей',
        'response:bad_candidate': 'кандидат не объект',
        'answer:no_json': 'в тексте ответа нет JSON',
        'answer:truncated': 'JSON обрывается на середине',
        'answer:too_long': 'ответ длиннее допустимых 20 000 символов',
        'answer:not_object': 'JSON верхнего уровня не объект',
        'answer:no_decision': 'нет поля decision или turns',
        'answer:skip_without_reason': 'решение skip без причины',
        'answer:bad_turns': 'нет ни одной фактовой реплики или обоих ведущих',
        'answer:bad_turn': 'реплика без поля kind или evidence',
        'answer:bad_speaker': 'неверный набор ведущих',
    }
    detail = error.get('detail')
    if isinstance(detail, str) and detail in where:
        text += f'\nГде именно: {where[detail]} (`{detail}`).'
    return text


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


def source_warning(item: dict) -> str:
    """Warn when a fact rests on a picture that will not be in the video.

    The post text can be a single joke line while the numbers live on the
    screenshot. If that screenshot is missing, facts about it cannot be checked
    against the text and must not look as solid as the rest.
    """
    from news_prototype import story_editor
    script = item.get('script') or {}
    if not isinstance(script, dict):
        return ''
    counts = story_editor.evidence_sources(script, item)
    facts = sum(counts.values())
    if not facts:
        return ''
    if item.get('image_description'):
        if counts['image'] and not script.get('image_b64'):
            return (f'Часть фактов ({counts["image"]} из {facts} реплик) подтверждена только '
                    'описанием картинки. Картинки в ролике не будет — проверь эти факты '
                    'по оригиналу поста.')
        return ''
    if counts['unknown']:
        return (f'Часть цитат ({counts["unknown"]} из {facts}) не нашлась в сохранённом '
                'тексте источника — вероятно, они с картинки. Проверь по оригиналу.')
    return ''


def image_status(item: dict) -> str:
    """Say *why* there is no picture instead of one catch-all sentence.

    A photo that failed to download, an unreadable thumbnail and a post with no
    photo at all need different actions, and the stored draft already knows
    which one happened. Nothing here is provider text: the reason is a fixed
    code mapped to our own wording.
    """
    reasons = {
        'too_large': 'файл больше допустимого размера',
        'unavailable': 'Telegram не отдал файл',
        'network': 'не удалось скачать файл',
        'unsafe_url': 'адрес файла не прошёл проверку безопасности',
        'redirect': 'файл отдаётся только через перенаправление, а оно запрещено',
        'encoding': 'неподдерживаемая кодировка ответа',
        'compression': 'файл пришёл сжатым, а распаковка запрещена',
        'codec': 'формат или содержимое файла не подошли',
        'other': 'неизвестная ошибка загрузки',
    }
    if not item.get('image_kind'):
        if item.get('image_error') in reasons:
            return ('в посте была картинка, но загрузить её не удалось: '
                    f'{reasons[item["image_error"]]}. Сюжет соберётся без неё.')
        if item.get('image_analysis') == 'unavailable':
            return ('в посте была картинка, но прочитать её не удалось. '
                    'Сюжет соберётся без неё.')
        return ('в источнике не нашлось подходящего изображения, либо его убрали вручную. '
                'Сюжет соберётся без картинки.')
    return ('картинка была выбрана, но не скачалась или не прошла проверку. '
            'Сюжет соберётся без неё; можно прислать свою.')


async def show(message, draft_id: int, *, for_program: bool = False, item: dict | None = None,
               panel_mode: bool = False, notice: str = '') -> None:
    panel_mode = panel_mode or getattr(message, 'panel_mode', False)
    for_program = for_program or getattr(message, 'for_program', False)
    if item is None:
        item = await owner_drafts.get(db.db, message.from_user.id, draft_id)
    if not item:
        await message.answer('Черновик не найден.', parse_mode=None)
        return
    stage = item.get('stage')
    if stage == 'draft_ready':
        from news_prototype import story_review
        review_text = story_review.display(item)
        editable = owner_drafts.editable_message(draft_id, item['url'], item['script'])
        if panel_mode:
            image = item['script'].get('image_b64')
            status = ('Картинка найдена — открой и проверь её и кадрирование.' if image else
                      'Без картинки: ' + image_status(item))
            warning = source_warning(item)
            text = (f'📰 Черновик №{draft_id} · сценарий\n'
                    + (notice + '\n' if notice else '') + editable.split('\n', 1)[1]
                    + '\n\n' + status
                    + ('\n⚠️ ' + warning if warning else '')
                    + '\nСверь факты, шутки и строку с источником.'
                    + ('\n\n' + review_text if review_text else ''))
            from handlers.owner_bulletin_panel import _chunks
            from handlers.owner_news_actions import card
            # A near-limit UTF-16 script may need multiple messages, but common
            # cases remain one card containing both review and controls.
            chunks = list(_chunks(text)) if len(text.encode('utf-16-le')) // 2 > 4096 else [text]
            for chunk in chunks[:-1]:
                await message.answer(chunk, parse_mode=None, disable_web_page_preview=True)
            await card(message, message.from_user.id, 'draft', item, for_program=for_program, text=chunks[-1])
            return
        else:
            await message.answer(('Черновик непроверен: сверь смысл, факты, шутки и строку с источником. '
                                  + (review_text + '\n' if review_text else '')) +
                                 'Все действия — кнопками в карточке ниже. '
                                 + ('После проверки собери выпуск из нужных черновиков.' if for_program else
                                    'Рендер начнётся только после отдельного подтверждения.'), parse_mode=None)
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
            label = {'video_thumbnail': 'кадр-превью видео', 'article_image': 'изображение из метаданных статьи', 'manual': 'присланное владельцем изображение'}.get(item.get('image_kind'), 'изображение поста')
            await message.answer_photo(types.BufferedInputFile(decode_thumbnail(image), filename='source.jpg'),
                                       caption=f'Найдено {label}. Оно появится в ролике крупным планом. '
                                               'Проверьте содержимое и кадрирование перед публикацией.', parse_mode=None)
            regions = item['script'].get('image_regions')
            if regions:
                from news_prototype.news_image_codec import review_preview
                await message.answer_photo(types.BufferedInputFile(review_preview(image, regions), filename='focus.jpg'),
                                           caption='Рамки показывают области для увеличения. Если они выбраны '
                                                   'неудачно, убери их кнопкой «Убрать увеличенные области».',
                                           parse_mode=None)
        else:
            await message.answer('Без картинки: ' + image_status(item)
                                 + ' Можно прислать свою кнопкой «Добавить картинку вручную».', parse_mode=None)
        if item['script'].get('video_sha256'):
            await message.answer('Видеофрагмент добавлен: он прозвучит в паузе между репликами, '
                                 'видео будет на экране студии около 10 секунд. Права на использование '
                                 'проверяешь ты.', parse_mode=None)
        # The screen itself carries every action: no command, hash or owner
        # action is ever printed as text in the private chat.
        from handlers.owner_news_actions import card
        await card(message, message.from_user.id, 'draft', item, for_program=for_program,
                   text=('Действия с этой версией:' if not for_program else
                         'Проверь сценарий, затем собери из него выпуск:'))
        return
    source_reason = source_failure_text(item)
    labels = {
        'new': 'Запрос сохранён, подготовка ещё не началась.',
        'fetching': 'Читаю источник; при зависании не запускайте повтор вслепую.',
        'editing': 'Редактор готовит сценарий; при зависании не запускайте повтор вслепую.',
        'needs_source': ('Источник недоступен или текста мало.'
                         + (f'\nПричина: {source_reason}' if source_reason else '')
                         + '\nПришли текст кнопкой «Прислать текст источника».'),
        'skipped': 'Редактор отказался от сюжета: информации недостаточно.',
        'editor_failed': 'Не удалось подготовить сценарий. Автоматического повтора нет.',
        'approving': 'Подтверждение обрабатывается. Состояние — в карточке, не повторяй вслепую.',
        'approval_uncertain': 'Результат постановки не подтверждён. Проверь карточку; не повторяй вслепую.',
        'queued': 'Сценарий поставлен в очередь. Состояние рендера и доставки — в карточке ниже.',
        'already_queued': 'Эта версия уже сохранялась; повторного рендера не запущено.',
    }
    if stage == 'needs_source':
        await message.answer('Источник недоступен или текста мало.'
                             + (f'\nПричина: {source_reason}' if source_reason else '')
                             + '\nПришли полный текст кнопкой ниже.', parse_mode=None)
    elif stage == 'editor_failed':
        # The generic label hides whether it was a quota error or a bad quote.
        await message.answer(labels['editor_failed'] + '\n' + editor_failure_text(item), parse_mode=None)
    else:
        await message.answer(labels.get(stage, 'Неизвестное состояние; нужна проверка.'), parse_mode=None)
    # Every stage gets the same button screen, including failures: the owner
    # must never have to retype a command or a revision hash.
    from handlers.owner_news_actions import card
    await card(message, message.from_user.id, 'draft', item)


async def show_image_details(message, owner_id: int, item: dict, *, for_program: bool = False) -> None:
    """Explicit review on request; never changes the draft or starts a job."""
    from news_prototype.news_image_codec import decode_thumbnail, review_preview
    from handlers.owner_news_actions import card
    image = item['script'].get('image_b64')
    if not image:
        await message.answer('Картинка не выбрана.', parse_mode=None)
        return
    label = {'video_thumbnail': 'кадр-превью видео', 'article_image': 'изображение из метаданных статьи',
             'manual': 'присланное тобой изображение'}.get(item.get('image_kind'), 'изображение поста')
    await message.answer_photo(types.BufferedInputFile(decode_thumbnail(image), filename='source.jpg'),
        caption=f'Найдено {label}. Проверь содержимое и право использования.', parse_mode=None)
    regions = item['script'].get('image_regions')
    if regions:
        await message.answer_photo(types.BufferedInputFile(review_preview(image, regions), filename='focus.jpg'),
            caption='Рамки показывают области для увеличения. Проверь кадрирование.', parse_mode=None)
    analysis = item.get('image_analysis')
    if analysis == 'analyzed':
        description = item.get('image_description', '')
        text = 'Распознавание картинки может ошибаться; сверь с оригиналом:\n' + description
    elif analysis == 'unavailable':
        text = 'Картинку не удалось распознать. Проверь, нет ли на ней важных сведений.'
    else:
        text = 'Проверь картинку и кадрирование перед рендером.'
    from handlers.owner_bulletin_panel import _chunks
    chunks = list(_chunks(text)) if len(text.encode('utf-16-le')) // 2 > 4096 else [text]
    for chunk in chunks[:-1]:
        await message.answer(chunk, parse_mode=None, disable_web_page_preview=True)
    await card(message, owner_id, 'draft', item, for_program=for_program, text=chunks[-1], media_view=True)


@router.message(Command('news_prepare'))
async def prepare(message: types.Message) -> None:
    if not authorized(message):
        return
    if not await owner_trial.drafts_enabled(db.db):
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        url, accent = owner_drafts.prepare_input(message.text or '')
        created = await owner_drafts.create(db.db, message.from_user.id, message.message_id, url, accent)
    except ValueError:
        await message.answer('Пришли ссылку на пост, а следующей строкой — акцент. '
                             'Обычно проще нажать «Подготовить по ссылкам» в разделе «Сценарии».', parse_mode=None)
        return
    except Exception as exc:
        logger.warning('Не удалось сохранить запрос черновика: %s', type(exc).__name__)
        await message.answer('Сохранение не подтверждено. Обнови список сценариев; не повторяй вслепую.', parse_mode=None)
        return
    draft_id = message.message_id
    if created:
        try:
            await message.answer(f'Готовлю черновик. Номер: {draft_id}. Это использует квоту Gemini; '
                                 'рендера и публикации пока нет. Карточка черновика — в списке сценариев.', parse_mode=None)
        except Exception:
            # A lost informational message does not justify another editor call.
            pass
    try:
        await owner_drafts.process_new(db.db, message.from_user.id, draft_id, article_fetch.fetch_article,
                                       make_draft, news_image_fetch.fetch_thumbnail, analyze_image)
        await show(message, draft_id)
    except Exception as exc:
        logger.warning('Не удалось подтвердить состояние черновика: %s', type(exc).__name__)
        await message.answer(f'Состояние черновика {draft_id} не подтверждено; не повторяй вслепую.', parse_mode=None)


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
            await message.answer(f'Состояние черновика {draft_id} не подтверждено. Открой его в списке сценариев.',
                                 parse_mode=None)
        else:
            await show(message, draft_id, for_program=True)
    await message.answer('Когда проверишь готовые черновики, собери выпуск кнопкой «Собрать выпуск» '
                         'в панели новостей. Если источника не хватает, пришли текст кнопкой в его карточке.',
                         parse_mode=None)


@router.message(Command('news_prepare_many'))
async def prepare_many(message: types.Message) -> None:
    if not authorized(message):
        return
    if not await owner_trial.drafts_enabled(db.db):
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        urls, accent = owner_drafts.prepare_many_input(message.text or '')
        await prepare_urls(message, urls, accent)
    except ValueError:
        await message.answer('Укажи /news_prepare_many, затем с новой строки от двух до четырёх '
                             'разных ссылок и, при желании, строку Акцент: ...', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось подтвердить черновики: %s', type(exc).__name__)
        await message.answer('Подготовка не подтверждена. Обнови список сценариев; повторяй только отсутствующие ссылки.', parse_mode=None)


@router.message(Command('news_discover'))
async def discover(message: types.Message) -> None:
    if not authorized(message):
        return
    if not await owner_trial.drafts_enabled(db.db):
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
            # The picker is a screen with toggles, so a command with indexes is
            # no longer the only way to choose from a collection.
            await message.answer(f'Выбери от двух до четырёх новостей из подборки №{batch} кнопками: '
                                 'открой подборку в панели новостей и отметь нужные. '
                                 'Сценарии и ролик пока не созданы.', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось получить подборку: %s', type(exc).__name__)
        await message.answer('Не удалось получить подборку. Попробуйте позже.', parse_mode=None)


@router.message(Command('news_daily_status'))
async def daily_status(message: types.Message) -> None:
    if not authorized(message):
        return
    from news_prototype import owner_daily
    try:
        if not await owner_daily.enabled(db.db):
            await message.answer('Ежедневный сбор новостей выключен. Управление: /panel', parse_mode=None)
            return
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        state = await db.db.news_owner_daily_state.find_one({'_id': 'last_poll'})
        count = await db.db.news_owner_daily_items.count_documents(
            {'published_at': {'$gte': (now - timedelta(hours=24)).isoformat(), '$lte': now.isoformat()}})
        text = f'Сбор включён: каждые 30 минут, подборка в 03:00 по Москве.\nСобрано за сутки: {count}.'
        if state:
            at = state.get('at')
            if at:
                if at.tzinfo is None:
                    at = at.replace(tzinfo=timezone.utc)
                text += '\nПоследняя проверка: ' + at.astimezone(owner_daily.MSK).strftime('%d.%m %H:%M МСК') + '.'
            if state.get('failed'):
                text += '\nНедоступны при проверке: ' + ', '.join(state['failed']) + '.'
        else:
            text += '\nПервая проверка ещё не завершена.'
        last = await db.db.news_owner_daily_deliveries.find_one({'owner_id': message.from_user.id}, sort=[('day', -1)])
        if last:
            labels = {'preparing': 'готовится', 'ready': 'ожидает отправки', 'sending': 'отправка не подтверждена',
                      'uncertain': 'отправка не подтверждена', 'sent': 'доставлена'}
            text += f'\nПоследняя подборка ({last["day"]}): {labels.get(last.get("stage"), "неизвестно")}.'
        text += '\nПосмотреть накопленное: /news_collected\nУправление: /panel'
        if not await owner_trial.drafts_enabled(db.db):
            text += '\nПодготовка сценариев сейчас выключена; сбору это не мешает.'
        await message.answer(text, parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось получить статус сбора: %s', type(exc).__name__)
        await message.answer('Не удалось получить статус сбора.', parse_mode=None)


@router.message(Command('news_collected'))
async def collected(message: types.Message) -> None:
    if not authorized(message):
        return
    from news_prototype import owner_daily
    try:
        if not await owner_daily.enabled(db.db):
            await message.answer('Ежедневный сбор новостей выключен. Управление: /panel', parse_mode=None)
            return
        from ai_providers import fire_gemini_key
        from models import GEMINI_CHAT_MODEL
        text = await owner_daily.preview(db.db, message.from_user.id, message.message_id,
                                         fire_gemini_key(), GEMINI_CHAT_MODEL)
        await message.answer(text, parse_mode=None, disable_web_page_preview=True)
    except Exception as exc:
        logger.warning('Не удалось показать собранные новости: %s', type(exc).__name__)
        await message.answer('Не удалось показать собранные новости.', parse_mode=None)


@router.message(Command('news_pick'))
async def pick(message: types.Message) -> None:
    if not authorized(message):
        return
    if not await owner_trial.drafts_enabled(db.db):
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
        await message.answer('Состояние не подтверждено. Обнови список сценариев; не повторяй вслепую.', parse_mode=None)


@router.message(Command('news_drafts'))
async def list_drafts(message: types.Message) -> None:
    if not authorized(message):
        return
    if not await owner_trial.drafts_enabled(db.db):
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        items = await db.db.news_owner_drafts.find({'owner_id': message.from_user.id}).sort(
            'created_at', -1).limit(5).to_list(length=5)
        if not items:
            await message.answer('Черновиков пока нет.', parse_mode=None)
            return
        lines = [f'{item["draft_id"]} — {item.get("stage", "unknown")}' for item in items]
        # The chat stays a screen, not a terminal: the list is opened by button.
        await message.answer('Последние черновики:\n' + '\n'.join(lines)
                             + '\nОткрыть нужный — кнопкой в разделе «Новости → Сценарии».', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось получить черновики: %s', type(exc).__name__)
        await message.answer('Не удалось получить список черновиков.', parse_mode=None)


@router.message(Command('news_draft'))
async def get_draft(message: types.Message) -> None:
    if not authorized(message):
        return
    if not await owner_trial.drafts_enabled(db.db):
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        parts = (message.text or '').split()
        if len(parts) != 2:
            raise ValueError
        await show(message, _id(parts[1]))
    except ValueError:
        await message.answer('Нужен номер черновика — проще открыть его кнопкой в разделе «Сценарии».', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось получить черновик: %s', type(exc).__name__)
        await message.answer('Не удалось получить черновик.', parse_mode=None)


@router.message(Command('news_source'))
async def provide_source(message: types.Message) -> None:
    if not authorized(message):
        return
    if not await owner_trial.drafts_enabled(db.db):
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
            await message.answer('Этот черновик не ожидает текста. Обнови карточку черновика.', parse_mode=None)
            return
        await show(message, draft_id)
    except ValueError:
        await message.answer('Пришли номер и достаточно полный текст источника: /news_source НОМЕР, '
                             'затем текст с новой строки. Обычно проще нажать кнопку в карточке.', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось подтвердить источник: %s', type(exc).__name__)
        await message.answer('Состояние не подтверждено. Обнови список сценариев; не повторяй вслепую.', parse_mode=None)


@router.message(Command('news_retry'))
async def retry_draft(message: types.Message) -> None:
    """Owner-initiated repeat of a failed draft; one Gemini request, no fetch."""
    if not authorized(message):
        return
    if not await owner_trial.drafts_enabled(db.db):
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        parts = (message.text or '').split()
        if len(parts) != 2:
            raise ValueError
        draft_id = _id(parts[1])
        result = await owner_drafts.retry(db.db, message.from_user.id, draft_id, make_draft)
        if result == 'not_failed':
            await message.answer(f'Черновик {draft_id} не в состоянии ошибки. Обнови карточку.', parse_mode=None)
            return
        if result in ('not_found', 'not_ready'):
            await message.answer('Повтор недоступен: нет сохранённого источника. '
                                 'Пришли текст кнопкой в карточке черновика.', parse_mode=None)
            return
        if result == 'changed':
            await message.answer('Состояние уже изменилось; не повторяй вслепую. Обнови карточку.', parse_mode=None)
            return
        await message.answer('Повторяю один запрос к Gemini по сохранённому источнику.', parse_mode=None)
        await show(message, draft_id)
    except ValueError:
        await message.answer('Нужен номер черновика — повтор доступен кнопкой в его карточке.', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось подтвердить повтор: %s', type(exc).__name__)
        await message.answer(f'Состояние черновика {draft_id} не подтверждено; не повторяй вслепую.', parse_mode=None)


@router.message(Command('news_edit'))
async def edit_draft(message: types.Message) -> None:
    if not authorized(message):
        return
    if not await owner_trial.drafts_enabled(db.db):
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        draft_id, expected, url, script = owner_drafts.parse_edit(message.text or '')
        result = await owner_drafts.edit(db.db, message.from_user.id, draft_id, expected, url, script)
        if result == 'stale':
            await message.answer('Версия изменилась. Открой черновик в списке сценариев заново.', parse_mode=None)
        else:
            notice = 'Изменения сохранены.' if result == 'saved' else 'Текст не изменился.'
            if getattr(message, 'panel_mode', False):
                await show(message, draft_id, notice=notice)
            else:
                await message.answer(notice, parse_mode=None)
                await show(message, draft_id)
    except ValueError:
        await message.answer('Проверьте формат, источник и реплики. Исходную ссылку менять нельзя.', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось подтвердить правку: %s', type(exc).__name__)
        await message.answer('Сохранение не подтверждено. Обнови список сценариев.', parse_mode=None)


@router.message(Command('news_nofocus'))
async def remove_focus(message: types.Message) -> None:
    if not authorized(message):
        return
    if not await owner_trial.drafts_enabled(db.db):
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        parts = (message.text or '').split()
        if len(parts) != 3:
            raise ValueError
        draft_id = _id(parts[1])
        result = await owner_drafts.remove_focus(db.db, message.from_user.id, draft_id, parts[2])
        if result == 'stale':
            await message.answer('Версия изменилась. Открой карточку черновика заново.', parse_mode=None)
        else:
            notice = 'Автоматические области убраны.' if result == 'saved' else 'Областей не было.'
            if getattr(message, 'panel_mode', False):
                await show(message, draft_id, notice=notice)
            else:
                await message.answer(notice, parse_mode=None)
                await show(message, draft_id)
    except ValueError:
        await message.answer('Убери области кнопкой в карточке черновика.', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось изменить кадрирование: %s', type(exc).__name__)
        await message.answer('Сохранение не подтверждено. Обнови список сценариев.', parse_mode=None)


@router.message(Command('news_noimage'))
async def remove_image(message: types.Message) -> None:
    if not authorized(message):
        return
    if not await owner_trial.drafts_enabled(db.db):
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        parts = (message.text or '').split()
        if len(parts) != 3:
            raise ValueError
        draft_id = _id(parts[1])
        result = await owner_drafts.remove_image(db.db, message.from_user.id, draft_id, parts[2])
        if result == 'stale':
            await message.answer('Версия изменилась. Открой карточку черновика заново.', parse_mode=None)
        else:
            notice = 'Изображение убрано.' if result == 'saved' else 'Изображения нет.'
            if getattr(message, 'panel_mode', False):
                await show(message, draft_id, notice=notice)
            else:
                await message.answer(notice, parse_mode=None)
                await show(message, draft_id)
    except ValueError:
        await message.answer('Убери картинку кнопкой в карточке черновика.', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось подтвердить удаление изображения: %s', type(exc).__name__)
        await message.answer('Сохранение не подтверждено. Обнови список сценариев.', parse_mode=None)


@router.message(Command('news_novideo'))
async def remove_video(message: types.Message) -> None:
    if not authorized(message):
        return
    if not await owner_trial.drafts_enabled(db.db):
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        parts = (message.text or '').split()
        if len(parts) != 3:
            raise ValueError
        draft_id = _id(parts[1])
        result = await owner_drafts.remove_video(db.db, message.from_user.id, draft_id, parts[2])
        if result == 'stale':
            await message.answer('Версия изменилась. Открой карточку черновика заново.', parse_mode=None)
        else:
            notice = 'Видеофрагмент убран.' if result == 'saved' else 'Видеофрагмента нет.'
            if getattr(message, 'panel_mode', False):
                await show(message, draft_id, notice=notice)
            else:
                await message.answer(notice, parse_mode=None)
                await show(message, draft_id)
    except ValueError:
        await message.answer('Скопируйте команду удаления фрагмента из черновика.', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось подтвердить удаление фрагмента: %s', type(exc).__name__)
        await message.answer('Сохранение не подтверждено. Обнови список сценариев.', parse_mode=None)


@router.message(Command('news_approve'))
async def approve_draft(message: types.Message) -> None:
    if not authorized(message):
        return
    if not await owner_trial.drafts_enabled(db.db):
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        parts = (message.text or '').split()
        if len(parts) != 3:
            raise ValueError
        draft_id = _id(parts[1])
        item = await owner_drafts.get(db.db, message.from_user.id, draft_id)
        if item and item.get('stage') == 'draft_ready' and item.get('revision') == parts[2]:
            from news_prototype.news_worker_client import NewsWorkerClient, NewsWorkerError
            try:
                client = NewsWorkerClient(os.getenv('BENDER_WORKER_URL', ''), os.getenv('BENDER_WORKER_KEY', ''))
                capabilities = await client.capabilities()
            except (ValueError, OSError, NewsWorkerError):
                await message.answer('Не удалось проверить воркер: он может просыпаться или быть недоступен. '
                                     'Рендер не запущен, черновик сохранён. Проверь подключение и попробуй '
                                     'подтвердить эту версию позже.', parse_mode=None)
                return
            if 'news-story-bulletin-studio-v1' not in capabilities:
                await message.answer('Воркер ответил, но новая студия одиночных сюжетов ещё не доступна. '
                                     'Обновите воркер; рендер не запущен, черновик сохранён.', parse_mode=None)
                return
            if item.get('script', {}).get('image_b64') and 'news-story-image-v1' not in capabilities:
                await message.answer('Воркер ответил, но не поддерживает картинки в одиночных сюжетах. '
                                     'Рендер не запущен, картинка сохранена; сначала обновите воркер.', parse_mode=None)
                return
            if item['script'].get('video_sha256') and 'news-story-manual-video-v1' not in capabilities:
                await message.answer('Воркер ответил, но не поддерживает видеофрагмент в одиночном сюжете. '
                                     'Рендер не запущен, черновик сохранён; сначала обновите воркер.', parse_mode=None)
                return
            if item['script'].get('image_regions') and 'news-image-focus-v1' not in capabilities:
                await message.answer('Воркер ответил, но не поддерживает выбранные области картинки. '
                                     'Обновите воркер; рендер не запущен.', parse_mode=None)
                return
        result = await owner_drafts.approve(db.db, message.from_user.id, draft_id, parts[2])
    except ValueError:
        await message.answer('Подтверждение берите кнопкой в карточке черновика.', parse_mode=None)
        return
    except Exception as exc:
        logger.warning('Не удалось подтвердить постановку: %s', type(exc).__name__)
        await message.answer('Состояние не подтверждено. Проверь карточку; не повторяй вслепую.', parse_mode=None)
        return
    labels = {
        'queued': 'Проверенный сценарий поставлен в общую очередь. Видео придёт только владельцу.',
        'already_queued': 'Эта версия уже сохранялась; повторный рендер не запущен.',
        'stale': 'Версия изменилась. Открой черновик в списке сценариев заново.',
        'not_ready': 'Черновик не готов к подтверждению. Обнови его карточку.',
        'trial_busy': 'Предыдущая проба ещё не завершена или её результат неизвестен. Проверь состояние.',
        'changed': 'Состояние изменилось. Обнови карточку черновика.',
        'approval_uncertain': 'Постановка не подтверждена. Проверь состояние; не повторяй вслепую.',
    }
    await message.answer(labels.get(result, 'Состояние не подтверждено.'), parse_mode=None)
    item = await owner_drafts.get(db.db, message.from_user.id, draft_id)
    # While the queue is being checked there is nothing to press, and the
    # result line above is the answer; only a retryable stage gets a card.
    if item and item.get('stage') in ('draft_ready', 'needs_source', 'editor_failed'):
        from handlers.owner_news_actions import card
        await card(message, message.from_user.id, 'draft', item)


async def show_program(message, program_id: int, *, program: dict | None = None, panel_mode: bool = False) -> None:
    panel_mode = panel_mode or getattr(message, 'panel_mode', False)
    if program is None:
        program = await owner_program.get(db.db, message.from_user.id, program_id)
    if not program:
        await message.answer('Выпуск не найден.', parse_mode=None)
        return
    stage = program.get('stage')
    if stage != 'ready_for_review':
        labels = {
            'approving': 'Подтверждение обрабатывается; состояние — в карточке выпуска.',
            'approval_uncertain': 'Постановка не подтверждена. Проверь состояние; не повторяй вслепую.',
            'queued': 'Выпуск поставлен в очередь. Состояние рендера и доставки — в карточке.',
            'already_queued': 'Этот выпуск уже сохранялся; повторного рендера нет.',
        }
        await message.answer(labels.get(stage, 'Неизвестное состояние выпуска.'), parse_mode=None)
        return
    await message.answer('Проверь все сюжеты и картинки ниже. Если исправишь один из черновиков, '
                         'собери выпуск заново в панели. Пока рендера нет. После подтверждения все сюжеты '
                         'будут созданы заново; расходуется квота озвучки.', parse_mode=None)
    for index, story in enumerate(program['stories'], 1):
        await message.answer(f'Сюжет {index} из {len(program["stories"])}', parse_mode=None)
        editable = owner_drafts.editable_message(story['draft_id'], story['url'], story['script'])
        await message.answer(editable.split('\n', 1)[1] if panel_mode else editable,
                             parse_mode=None, disable_web_page_preview=True)
        image = story['script'].get('image_b64')
        if image:
            from news_prototype.news_image_codec import decode_thumbnail
            label = {'video_thumbnail': 'превью видео', 'article_image': 'изображение из метаданных статьи',
                     'manual': 'присланное изображение'}.get(story.get('image_kind'), 'картинка поста')
            await message.answer_photo(types.BufferedInputFile(decode_thumbnail(image), filename='source.jpg'),
                                       caption=f'Сюжет {index}: {label}. '
                                               'Правки — в карточке этого черновика.', parse_mode=None)
            regions = story['script'].get('image_regions')
            if regions:
                from news_prototype.news_image_codec import review_preview
                await message.answer_photo(types.BufferedInputFile(review_preview(image, regions), filename='focus.jpg'),
                                           caption=f'Сюжет {index}: автоматически выбранные области. '
                                                   'Правки — в карточке этого черновика.', parse_mode=None)
        else:
            await message.answer('Этот сюжет будет без картинки.', parse_mode=None)
    from handlers.owner_news_actions import card
    await card(message, message.from_user.id, 'program', program,
               text='Если все сюжеты проверены, подтверди один общий рендер кнопкой ниже:')


@router.message(Command('news_program'))
async def create_program(message: types.Message) -> None:
    if not authorized(message):
        return
    if not await owner_trial.drafts_enabled(db.db):
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
                             'Собери выпуск кнопкой «Собрать выпуск» в панели новостей.', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось подтвердить выпуск: %s', type(exc).__name__)
        await message.answer('Состояние не подтверждено. Обнови карточку выпуска.', parse_mode=None)


@router.message(Command('news_programs'))
async def list_programs(message: types.Message) -> None:
    if not authorized(message):
        return
    if not await owner_trial.drafts_enabled(db.db):
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        programs = await db.db.news_owner_programs.find({'owner_id': message.from_user.id}).sort(
            'created_at', -1).limit(5).to_list(length=5)
        if not programs:
            await message.answer('Выпусков пока нет.', parse_mode=None)
            return
        lines = [f'{item["program_id"]} — {item.get("stage", "unknown")}' for item in programs]
        await message.answer('Последние выпуски:\n' + '\n'.join(lines)
                             + '\nОткрыть нужный — кнопкой в разделе «Выпуски».', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось получить список выпусков: %s', type(exc).__name__)
        await message.answer('Не удалось получить список выпусков.', parse_mode=None)


@router.message(Command('news_program_status'))
async def get_program(message: types.Message) -> None:
    if not authorized(message):
        return
    if not await owner_trial.drafts_enabled(db.db):
        await message.answer('Подготовка черновиков выключена.', parse_mode=None)
        return
    try:
        parts = (message.text or '').split()
        if len(parts) != 2:
            raise ValueError
        await show_program(message, _id(parts[1]))
    except ValueError:
        await message.answer('Нужен номер выпуска — проще открыть его кнопкой в разделе «Выпуски».', parse_mode=None)
    except Exception as exc:
        logger.warning('Не удалось получить выпуск: %s', type(exc).__name__)
        await message.answer('Не удалось получить выпуск.', parse_mode=None)


@router.message(Command('news_program_approve'))
async def approve_program(message: types.Message) -> None:
    if not authorized(message):
        return
    if not await owner_trial.drafts_enabled(db.db):
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
        await message.answer('Не удалось проверить данные выпуска. Открой его карточку в разделе «Выпуски».', parse_mode=None)
        return
    except Exception as exc:
        logger.warning('Не удалось подтвердить выпуск: %s', type(exc).__name__)
        await message.answer('Состояние не подтверждено. Проверь карточку выпуска; не повторяй вслепую.', parse_mode=None)
        return
    labels = {
        'queued': 'Выпуск поставлен в общую очередь. Готовый ролик придёт только владельцу.',
        'already_queued': 'Этот выпуск уже сохранялся; повторный рендер не запущен.',
        'stale': 'Версия выпуска не совпадает. Обнови карточку выпуска.',
        'stale_draft': 'Черновик изменился. Собери выпуск заново с актуальными номерами.',
        'not_ready': 'Выпуск не готов к подтверждению. Обнови его карточку.',
        'trial_busy': 'Предыдущая проба ещё не завершена или её результат неизвестен. Проверь состояние.',
        'changed': 'Состояние изменилось. Обнови карточку выпуска.',
        'approval_uncertain': 'Постановка не подтверждена. Проверь состояние; не повторяй вслепую.',
    }
    await message.answer(labels.get(result, 'Состояние не подтверждено.'), parse_mode=None)
    program = await owner_program.get(db.db, message.from_user.id, program_id)
    if program and program.get('stage') == 'ready_for_review':
        from handlers.owner_news_actions import card
        await card(message, message.from_user.id, 'program', program)

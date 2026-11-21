"""One bounded request to the bot's existing Gemini text model.

No retry, fallback, publishing or TTS. A successful schema/quote check does not
prove facts or semantic accuracy; every result remains pending human review.
"""
from __future__ import annotations
import json
import re
import aiohttp
from news_prototype import story_editor


class EditorialError(Exception):
    """Safe diagnostic code, never provider text or submitted content."""
    def __init__(self, message, *, code='unknown', http_status=None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None


# Only exact, hard-coded parser errors may leave the validator. Never expose
# exception text from a provider, input, model response, or unknown exception.
_BULLETIN_VALIDATION = {
    'Редактор не вернул JSON': 'invalid_json',
    'Не все новости включены в вестник': 'missing_sections',
    'Изменён порядок или номер новости': 'section_order',
    'Цитата не найдена в соответствующем источнике': 'evidence',
    'В каждой новости нужен факт с цитатой': 'evidence',
    'В выпуске нужны оба ведущих': 'speakers',
    'Сценарий слишком длинный: нужно сократить до пяти минут': 'too_long',
    'Слишком много реплик в вестнике': 'too_long',
}


def bulletin_validation_code(error):
    return _BULLETIN_VALIDATION.get(str(error), 'validation') if type(error) is ValueError else 'validation'


# Story validation errors are a fixed set of parser messages, so they can be
# reported to the owner without leaking model text or source content.
_STORY_VALIDATION = {
    'Повреждён ответ редактора': 'invalid_response',
    'Редактор не вернул JSON': 'invalid_json',
    'Неверный формат редактора': 'invalid_response',
    'Неизвестное решение редактора': 'invalid_response',
    'Неверное число реплик': 'too_long',
    'Неверный тип реплики': 'invalid_response',
    'Неверная цитата': 'evidence',
    'Цитата не найдена в исходном материале': 'evidence',
    'Комментарий не должен притворяться фактом': 'evidence',
    'Нужен факт и оба ведущих': 'speakers',
    'В сценарии нужны реплики': 'invalid_response',
    'Слишком много реплик': 'too_long',
    'Неверная реплика': 'invalid_response',
    'Неверный ведущий или текст реплики': 'invalid_response',
    'Пустая, повреждённая или слишком длинная реплика': 'too_long',
    'Неверный текст строки': 'invalid_response',
}


def story_validation_code(error):
    return _STORY_VALIDATION.get(str(error), 'validation') if type(error) is ValueError else 'validation'


def _strip_fence(text: str) -> str:
    """Unwrap one Markdown code block, which some Gemini replies add anyway.

    The response schema asks for bare JSON, but a fenced answer is the same
    document with decoration. Only a single whole-message fence is unwrapped;
    anything else is returned untouched so the parser still fails closed.
    """
    if not isinstance(text, str):
        return text
    match = re.fullmatch(r'\s*```(?:json|JSON)?\s*\n?(.*?)\n?\s*```\s*', text, re.S)
    if not match:
        return text
    body = match.group(1).strip()
    return body or text


async def _request(system: str, material: str, key: str, model: str, *, output_tokens: int, timeout: int) -> str:
    if not key or not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,100}', model):
        raise EditorialError('Редактор не настроен', code='configuration')
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
    # Gemini 3 thinks by default (HIGH), and thinking tokens share the
    # maxOutputTokens budget with the visible answer. On a long Russian system
    # prompt with strict JSON the model can burn all 4096 tokens on thoughts and
    # come back with finishReason=MAX_TOKENS -- an empty draft after a paid
    # request. LOW keeps reasoning while leaving room for the actual JSON.
    config = {'responseMimeType': 'application/json', 'temperature': 0.3, 'maxOutputTokens': output_tokens}
    # thinkingLevel only exists on Gemini 3.x; 2.5 uses thinkingBudget and would
    # reject the field with HTTP 400, so a pinned older model stays usable.
    if re.match(r'gemini-3', model):
        config['thinkingConfig'] = {'thinkingLevel': 'low'}
    payload = {'systemInstruction': {'parts': [{'text': system}]},
               'contents': [{'role': 'user', 'parts': [{'text': material}]}],
               'generationConfig': config}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.post(url, json=payload, headers={'x-goog-api-key':key}, allow_redirects=False) as response:
                if response.status != 200:
                    raise EditorialError('Редактор временно недоступен', code='http', http_status=response.status)
                data = bytearray()
                async for chunk in response.content.iter_chunked(4096):
                    data.extend(chunk)
                    if len(data) > 131_072:
                        raise EditorialError('Слишком большой ответ редактора', code='response_too_large')
        content = json.loads(data)
        if not isinstance(content, dict):
            raise EditorialError('Некорректный ответ редактора', code='invalid_response')
        candidates = content.get('candidates')
        if not isinstance(candidates, list) or not candidates:
            # Gemini returns no candidates at all when a request is refused
            # before generation. That is a stable, repeatable refusal, so it
            # must never be reported as a broken response format.
            if content.get('promptFeedback') or content.get('usageMetadata'):
                raise EditorialError('Редактор отказался от запроса', code='refused')
            raise EditorialError('Редактор вернул пустой ответ', code='empty_answer')
        result = candidates[0]
        if not isinstance(result, dict):
            raise EditorialError('Некорректный ответ редактора', code='invalid_response')
        block = result.get('finishReason')
        if block == 'SAFETY' or result.get('blockReason') or (
                isinstance(result.get('promptFeedback'), dict)
                and result['promptFeedback'].get('blockReason')):
            raise EditorialError('Редактор отказался от запроса', code='refused')
        if block != 'STOP':
            # MAX_TOKENS is the one truncation the owner can act on: it means
            # the visible answer never fit, which is a budget problem rather
            # than a provider outage. Keep it distinct from other stops.
            code = 'max_tokens' if block == 'MAX_TOKENS' else 'incomplete_response'
            raise EditorialError('Редактор вернул неполный ответ', code=code)
        parts = (result.get('content') or {}).get('parts')
        if not isinstance(parts, list):
            raise EditorialError('Некорректный ответ редактора', code='invalid_response')
        text_parts = [item['text'] for item in parts
                      if isinstance(item, dict) and isinstance(item.get('text'), str) and not item.get('thought')]
        if not text_parts:
            # A STOP without visible text means the whole budget went to
            # reasoning or the answer was filtered; that is not a format bug.
            raise EditorialError('Редактор вернул пустой ответ', code='empty_answer')
        # Gemini may split one JSON answer into several consecutive parts.
        # The answer is one document, so they are joined instead of rejected.
        return ''.join(text_parts)
    except EditorialError:
        raise
    except TimeoutError:
        raise EditorialError('Время ожидания редактора истекло', code='timeout') from None
    except aiohttp.ClientError:
        raise EditorialError('Не удалось связаться с редактором', code='network') from None
    except (ValueError, KeyError, TypeError, IndexError):
        raise EditorialError('Некорректный ответ редактора', code='invalid_response') from None


async def draft(candidate: dict, key: str, model: str) -> dict:
    system, material = story_editor.prompt_for(candidate)
    # The JSON draft plus evidence quotes is a few thousand characters; 4096
    # leaves too little room once Gemini 3 spends part of the budget thinking.
    text = _strip_fence(await _request(system, material, key, model, output_tokens=8192, timeout=60))
    try:
        return story_editor.parse_draft(text, candidate)
    except (ValueError, TypeError) as exc:
        raise EditorialError('Не удалось подготовить сценарий', code=story_validation_code(exc)) from None


async def bulletin(stories: list[dict], key: str, model: str) -> dict:
    """Generate exactly one short bulletin; no render, auto-retry or delivery."""
    from news_prototype import bulletin_editor
    system, material = bulletin_editor.prompt_for(stories)
    text = _strip_fence(await _request(system, material, key, model, output_tokens=8192, timeout=90))
    try:
        return bulletin_editor.parse_draft(text, stories)
    except (ValueError, TypeError) as exc:
        raise EditorialError('Не удалось проверить сценарий вестника', code=bulletin_validation_code(exc)) from None

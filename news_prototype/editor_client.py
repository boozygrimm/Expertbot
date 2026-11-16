"""One bounded request to the bot's existing Gemini text model for a news draft.

No retry, fallback, publishing or TTS. A successful schema/quote check does not
prove facts or semantic accuracy; every result remains pending human review.
"""
from __future__ import annotations
import json
import re
import aiohttp
from news_prototype import story_editor


class EditorialError(Exception): pass


async def draft(candidate: dict, key: str, model: str) -> dict:
    system, material = story_editor.prompt_for(candidate)
    if not key or not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,100}', model):
        raise EditorialError('Редактор не настроен')
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
    payload = {'systemInstruction': {'parts': [{'text': system}]},
               'contents': [{'role': 'user', 'parts': [{'text': material}]}],
               'generationConfig': {'responseMimeType': 'application/json', 'temperature': 0.3,
                                    'maxOutputTokens': 4096}}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as session:
            async with session.post(url, json=payload, headers={'x-goog-api-key':key}, allow_redirects=False) as response:
                if response.status != 200:
                    raise EditorialError('Редактор временно недоступен')
                data = bytearray()
                async for chunk in response.content.iter_chunked(4096):
                    data.extend(chunk)
                    if len(data) > 131_072:
                        raise EditorialError('Слишком большой ответ редактора')
        content = json.loads(data)
        result = content['candidates'][0]
        if result.get('finishReason') != 'STOP':
            raise EditorialError('Редактор вернул неполный ответ')
        parts = result['content']['parts']
        text_parts = [item['text'] for item in parts if isinstance(item, dict) and isinstance(item.get('text'), str) and not item.get('thought')]
        if len(text_parts) != 1:
            raise EditorialError('Некорректный ответ редактора')
        return story_editor.parse_draft(text_parts[0], candidate)
    except EditorialError:
        raise
    except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError, IndexError):
        raise EditorialError('Не удалось подготовить сценарий') from None

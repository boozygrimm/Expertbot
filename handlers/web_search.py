import asyncio
import html
import logging
import re

import aiohttp
from aiogram import Router
from ddgs import DDGS
from ai_logic import generate_with_retry

router = Router()
logger = logging.getLogger(__name__)
WIKIPEDIA_API = 'https://ru.wikipedia.org/w/api.php'
WIKIPEDIA_HEADERS = {
    'User-Agent': 'Expertbot/1.0 (https://github.com/boozygrimm/Expertbot; Telegram bot)'
}


def _fetch_ddgs_raw(query: str) -> list:
    """Primary search. Preserve failures so they aren't reported as no results."""
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=5))


async def _fetch_wikipedia(query: str) -> list:
    """Free, limited fallback for encyclopedic queries, not a news search."""
    params = {
        'action': 'query', 'list': 'search', 'srsearch': query,
        'srlimit': 5, 'format': 'json', 'formatversion': 2,
    }
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout, headers=WIKIPEDIA_HEADERS) as session:
        async with session.get(WIKIPEDIA_API, params=params) as response:
            response.raise_for_status()
            data = await response.json()
    results = []
    for item in data.get('query', {}).get('search', []):
        page_id = item.get('pageid')
        if not isinstance(page_id, int):
            continue
        snippet = html.unescape(re.sub(r'<[^>]*>', '', item.get('snippet', '')))
        results.append({'title': item.get('title', ''), 'body': snippet,
                        'href': f'https://ru.wikipedia.org/?curid={page_id}'})
    return results


async def smart_web_search(query: str) -> str:
    """Use the main search first, falling back to Wikipedia when it fails."""
    primary_failed = False
    try:
        raw_results = await asyncio.to_thread(_fetch_ddgs_raw, query)
    except Exception as exc:
        # Avoid logging user queries or third-party response bodies.
        logger.warning('Основной поиск недоступен: %s', type(exc).__name__)
        primary_failed = True
        raw_results = []

    fallback = not raw_results
    if fallback:
        try:
            raw_results = await _fetch_wikipedia(query)
        except Exception as exc:
            logger.warning('Резервный поиск в Википедии недоступен: %s', type(exc).__name__)
            return '❌ Поиск сейчас недоступен. Попробуй позже.'
        if not raw_results:
            if primary_failed:
                return '❌ Основной поиск недоступен, а в Википедии ничего не нашлось. Попробуй позже.'
            return '❌ По этому запросу ничего не нашлось.'

    context = ''
    sources = []
    for idx, res in enumerate(raw_results[:5], 1):
        title = str(res.get('title') or '')[:200]
        body = str(res.get('body') or '')[:700]
        url = str(res.get('href') or '')
        context += f'[{idx}] {title}\nТекст: {body}\n\n'
        if url.startswith(('https://', 'http://')) and len(url) <= 350 and not any(c.isspace() for c in url):
            sources.append(f'[{idx}] {url}')

    prompt = (
        f'Пользователь попросил найти информацию по запросу: «{query}».\n'
        f'Вот найденные данные:\n{context}\n'
        'Изучи данные и дай понятный и точный ответ. Не придумывай факты. '
        'Если данных недостаточно для ответа, скажи об этом.'
    )
    try:
        ai_answer = await generate_with_retry(prompt)
    except Exception as exc:
        logger.warning('Не удалось обработать результаты поиска: %s', type(exc).__name__)
        ai_answer = None
    prefix = ('⚠️ Основной поиск не дал результатов; ниже данные из Википедии. '
              'Это не поиск актуальных новостей.\n\n') if fallback else ''
    if not ai_answer:
        ai_answer = 'Не смог обработать результаты с помощью ИИ. Вот найденные фрагменты:\n\n' + context
    source_text = '\n\nИсточники:\n' + '\n'.join(sources) if sources else ''
    # Keep the response within Telegram's 4096-character limit.
    remaining = max(0, 4000 - len(prefix) - len(source_text))
    return prefix + ai_answer[:remaining] + source_text

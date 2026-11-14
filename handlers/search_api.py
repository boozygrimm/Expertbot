"""Search APIs: Tavily first, keyless Firecrawl only as fallback.

No retries are performed: a timed-out request may still consume quota. Never log
request URLs, API keys, or provider error bodies.
"""
import os
import re
from datetime import datetime, timedelta, timezone

import aiohttp

TAVILY_URL = 'https://api.tavily.com/search'
FIRECRAWL_URL = 'https://api.firecrawl.dev/v2/search'


class SearchProviderError(Exception):
    pass


def keys_configured():
    return bool(os.getenv('TAVILY_API_KEY', '').strip())


def search_mode(query):
    """Explicitly requested news and recency; never invent a date range."""
    lower = query.lower().replace('ё', 'е')
    news = bool(re.search(r'новост|событи[яй]|news|headlines', lower))
    historic = bool(re.search(r'истори|архив|прошл|историческ|history|historical|archive', lower))
    # News is normally a request for recent coverage, unless explicitly historical.
    days = None
    if news and not historic:
        days = 1 if re.search(r'сегодня|today', lower) else 30
    return news, days


def _date(value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        pass
    # Firecrawl news may report relative ages such as "12 hours ago". Use the
    # oldest end of that rounded interval for conservative freshness filtering.
    match = re.fullmatch(r'(\d{1,3})\s+(minute|hour|day|week)s?\s+ago', value.lower())
    if match:
        amount = int(match.group(1)) + 1
        units = {'minute': 'minutes', 'hour': 'hours', 'day': 'days', 'week': 'weeks'}
        return datetime.now(timezone.utc) - timedelta(**{units[match.group(2)]: amount})
    try:
        return datetime.strptime(value, '%b %d, %Y').replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _normalize(items, *, days):
    now = datetime.now(timezone.utc)
    result = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get('href') or ''
        title = item.get('title') or ''
        if not isinstance(url, str) or not url.startswith(('https://', 'http://')) or len(url) > 350 or any(c.isspace() for c in url):
            continue
        if not isinstance(title, str) or not title.strip() or url in seen:
            continue
        date = _date(item.get('date'))
        if days is not None and (not date or date < now - timedelta(days=days) or date > now + timedelta(days=1)):
            continue
        seen.add(url)
        raw_date = item.get('date') or ''
        relative = isinstance(raw_date, str) and raw_date.lower().strip().endswith(' ago')
        result.append({'title': title, 'body': str(item.get('body') or ''), 'href': url,
                       'date': raw_date.strip() if date and relative else date.strftime('%Y-%m-%d') if date else ''})
        if len(result) == 5:
            break
    return result


async def fetch_tavily(query, *, news, days):
    key = os.getenv('TAVILY_API_KEY', '').strip()
    if not key:
        raise SearchProviderError('not configured')
    payload = {'query': query, 'search_depth': 'basic', 'auto_parameters': False,
               'max_results': 5, 'topic': 'news' if news else 'general',
               'include_answer': False, 'include_raw_content': False}
    if days is not None:
        payload.update(time_range='day' if days == 1 else 'month', include_published_date=True,
                       filter_by_published_date=True)
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(TAVILY_URL, json=payload,
                                headers={'Authorization': f'Bearer {key}'}, allow_redirects=False) as response:
            if response.status != 200:
                raise SearchProviderError(f'HTTP {response.status}')
            data = await response.json()
    if not isinstance(data, dict) or not isinstance(data.get('results'), list):
        raise SearchProviderError('invalid response')
    items = [{'title': x.get('title'), 'body': x.get('content'), 'href': x.get('url'),
              'date': x.get('published_date')} for x in data['results'] if isinstance(x, dict)]
    return _normalize(items, days=days)


async def fetch_firecrawl(query, *, news, days):
    # Keyless Firecrawl has a shared per-IP limit. Never send an API key or use
    # additional scraping; preserve the user's free-only preference.
    bucket = 'news' if news else 'web'
    payload = {'query': query[:500], 'limit': 5, 'sources': [{'type': bucket}]}
    # Firecrawl's tbs filter applies only to web, not its news source. News dates
    # are instead checked locally; undated or unparseable results are excluded.
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(FIRECRAWL_URL, json=payload, allow_redirects=False) as response:
            if response.status != 200:
                raise SearchProviderError(f'HTTP {response.status}')
            data = await response.json()
    if not isinstance(data, dict) or data.get('success') is not True or not isinstance(data.get('data'), dict):
        raise SearchProviderError('invalid response')
    source_items = data['data'].get(bucket, [])
    if not isinstance(source_items, list):
        raise SearchProviderError('invalid response')
    items = [{'title': x.get('title'), 'body': x.get('snippet') or x.get('description'),
              'href': x.get('url'), 'date': x.get('date')} for x in source_items if isinstance(x, dict)]
    return _normalize(items, days=days)

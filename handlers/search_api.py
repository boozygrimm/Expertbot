"""Search APIs: Tavily first, keyless Firecrawl only as fallback.

No retries are performed: a timed-out request may still consume quota. Never log
request URLs, API keys, or provider error bodies.
"""
import logging
import os
import re
import ipaddress
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from datetime import datetime, timedelta, timezone

import aiohttp

TAVILY_URL = 'https://api.tavily.com/search'
FIRECRAWL_URL = 'https://api.firecrawl.dev/v2/search'

logger = logging.getLogger(__name__)

# Recency the user asked for, in days. Only what the query states is used.
DAY_PATTERN = (r'сутк|24\s*час|сегодня|вчерашн|вчера|за\s+(?:последн\w*\s+)?день'
               r'|today|yesterday|(?:last|past)\s+(?:24\s*hours?|day)')
WEEK_PATTERN = r'недел|7\s*дн|week'
# Tavily accepts day/week/month/year as its relative window.
TIME_RANGE = {1: 'day', 7: 'week', 30: 'month'}

_RELATIVE_UNITS = {'minute': 'minutes', 'hour': 'hours', 'day': 'days', 'week': 'weeks'}
# Absolute formats providers are seen to return besides ISO 8601.
_DATE_ONLY_FORMATS = ('%d %b %Y', '%d %B %Y', '%b %d, %Y', '%B %d, %Y', '%d.%m.%Y', '%Y/%m/%d')


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
        if re.search(DAY_PATTERN, lower):
            days = 1
        elif re.search(WEEK_PATTERN, lower):
            days = 7
        else:
            days = 30
    return news, days


# Words that describe the request rather than its subject. When a recency window
# is in force the provider already filters by date, so "новости за сутки" left in
# the query text only dilutes the real topic ("курс bitcoin и gram").
_NEWS_WRAPPER = re.compile(
    r'\b(?:последни\w+|свежи\w+|актуальн\w+|latest|recent|breaking)\s+(?:новост\w*|news)\b'
    r'|\b(?:новост\w*|news|headlines)\b'
    r'|\bза\s+(?:сегодня|вчера|сутки|последн\w+\s+(?:день|сутки)|\d{1,2}\s*час\w*|день|недел\w*|месяц)\b'
    r'|\b(?:for\s+)?(?:the\s+)?(?:last|past)\s+(?:24\s+hours?|\d+\s+(?:hours?|days?|weeks?)|day|week|month)\b'
    r'|\b(?:сегодня|вчера|today|yesterday|включая|including|а\s+также|также)\b',
    re.I)


def news_terms(query):
    """Drop the news/recency wrapper from a query, keeping its subject.

    Returns the original query when nothing else is left in it, so a bare
    «новости» is still searched as is.
    """
    cleaned = ' '.join(_NEWS_WRAPPER.sub(' ', query or '').split())
    return cleaned or (query or '').strip()


def _end_of_day(moment):
    """A timestamp without a time covers its whole day, so its end is the bound.

    Providers commonly report only a calendar date. Reading "2026-11-21" as
    midnight UTC would put an article published yesterday outside a 24-hour
    window even though the provider accepted it as fresh.
    """
    return moment.replace(hour=23, minute=59, second=59, microsecond=999999)


def _parse(text):
    """Return (moment, date_only) for one provider timestamp, else None."""
    lowered = text.lower()
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        date_only = ':' not in text and 'T' not in text and ' ' not in text
        return parsed, date_only
    except ValueError:
        pass
    # Firecrawl news may report relative ages such as "12 hours ago". Use the
    # oldest end of that rounded interval for conservative freshness filtering.
    match = re.fullmatch(r'(an?|\d{1,3})\s+(minute|hour|day|week)s?\s+ago', lowered)
    if match:
        amount = int(match.group(1)) if match.group(1).isdigit() else 1
        return datetime.now(timezone.utc) - timedelta(**{_RELATIVE_UNITS[match.group(2)]: amount + 1}), False
    if lowered in ('yesterday', 'вчера'):
        # A bare day, like a date without a time: it may be any moment of it.
        return datetime.now(timezone.utc) - timedelta(days=1), True
    try:
        parsed = parsedate_to_datetime(text)
        return parsed, ':' not in text
    except (TypeError, ValueError):
        pass
    for fmt in _DATE_ONLY_FORMATS:
        try:
            return datetime.strptime(text, fmt), True
        except ValueError:
            continue
    return None


def _date(value):
    """Parse a provider timestamp into UTC. None means "no usable date"."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # Epoch seconds; values above ~5000 years old are milliseconds.
        seconds = float(value) / 1000 if abs(value) > 1e11 else float(value)
        try:
            return datetime.fromtimestamp(seconds, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = _parse(value.strip())
    if not parsed:
        return None
    moment, date_only = parsed
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)
    return _end_of_day(moment) if date_only else moment


def _normalize(items, *, days, source='поиск'):
    """Keep usable items inside the requested window and report what was dropped.

    A dropped item is invisible to the user, so the drop reasons are logged:
    "no suitable news" is otherwise indistinguishable from a broken provider.
    """
    now = datetime.now(timezone.utc)
    result = []
    seen = set()
    dropped = {'некорректный адрес или заголовок': 0, 'без даты': 0, 'вне окна': 0}
    unparsed = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get('href') or ''
        title = item.get('title') or ''
        if not isinstance(url, str) or not url.startswith(('https://', 'http://')) or len(url) > 350 or any(c.isspace() for c in url):
            dropped['некорректный адрес или заголовок'] += 1
            continue
        if not isinstance(title, str) or not title.strip() or url in seen:
            dropped['некорректный адрес или заголовок'] += 1
            continue
        raw_date = item.get('date')
        date = _date(raw_date)
        if days is not None:
            if not date:
                if isinstance(raw_date, (str, int, float)) and str(raw_date).strip():
                    unparsed += 1
                dropped['без даты'] += 1
                continue
            if date < now - timedelta(days=days) or date > now + timedelta(days=1):
                dropped['вне окна'] += 1
                continue
        seen.add(url)
        relative = isinstance(raw_date, str) and raw_date.lower().strip().endswith(' ago')
        result.append({'title': title, 'body': str(item.get('body') or ''), 'href': url,
                       'date': raw_date.strip() if date and relative else date.strftime('%Y-%m-%d') if date else ''})
        if len(result) == 5:
            break
    if any(dropped.values()) or unparsed:
        reasons = ', '.join(f'{reason} — {count}' for reason, count in dropped.items() if count)
        if unparsed:
            reasons += f', дат не распознано — {unparsed}'
        logger.info('%s: %d от провайдера, %d оставлено (окно %s дн.), отброшено%s: %s',
                    source, len(items), len(result), days,
                    ' до первых пяти пригодных' if len(result) == 5 else '', reasons)
    return result


def _image_candidates(data):
    """Take only described public HTTPS image URLs; never fetch them on Render."""
    images = data.get('images', []) if isinstance(data, dict) else []
    if not isinstance(images, list):
        return []
    candidates = []
    for entry in images[:20]:
        if not isinstance(entry, dict):
            continue
        url, description = entry.get('url'), entry.get('description')
        if not isinstance(url, str) or not isinstance(description, str):
            continue
        if not description.strip() or len(url) > 2048 or any(c.isspace() or ord(c) < 32 for c in url):
            continue
        try:
            parts = urlsplit(url)
            host = parts.hostname
            if parts.scheme != 'https' or not host or parts.username or parts.password or parts.port not in (None, 443):
                continue
            if host.lower() == 'localhost' or host.lower().endswith(('.localhost', '.local', '.internal')) or '.' not in host:
                continue
            try:
                if not ipaddress.ip_address(host).is_global:
                    continue
            except ValueError:
                pass
        except ValueError:
            continue
        candidates.append({'url': url, 'description': description.strip()[:300]})
        if len(candidates) == 5:
            break
    return candidates


async def fetch_tavily(query, *, news, days, include_images=False):
    key = os.getenv('TAVILY_API_KEY', '').strip()
    if not key:
        raise SearchProviderError('not configured')
    payload = {'query': query, 'search_depth': 'basic', 'auto_parameters': False,
               'max_results': 5, 'topic': 'news' if news else 'general',
               'include_answer': False, 'include_raw_content': False}
    if include_images:
        payload.update(include_images=True, include_image_descriptions=True, safe_search=True)
    if days is not None:
        payload.update(time_range=TIME_RANGE.get(days, 'month'), include_published_date=True,
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
    results = _normalize(items, days=days, source='Tavily')
    return (results, _image_candidates(data)) if include_images else results


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
    return _normalize(items, days=days, source='Firecrawl')

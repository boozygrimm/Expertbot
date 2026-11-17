"""Isolated, opt-in RSS/Atom discovery; not scheduled or connected to the bot.

External feed entries are untrusted candidates, not verified news or prompts.
Only the exact feeds in FEEDS are fetched; item URLs are recorded, never fetched.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import hashlib
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit
import xml.etree.ElementTree as ET

# Candidate feeds, not a claim that they are reachable or suitable in production.
FEEDS = {
    'openai': ('OpenAI', 'https://openai.com/news/rss.xml'),
    'deepmind': ('Google DeepMind', 'https://deepmind.google/blog/rss.xml'),
    'huggingface': ('Hugging Face', 'https://huggingface.co/blog/feed.xml'),
    'cointelegraph': ('Cointelegraph', 'https://cointelegraph.com/rss'),
    'decrypt': ('Decrypt', 'https://decrypt.co/feed'),
}
MAX_BYTES = 2_000_000
MAX_ITEMS = 100
ATOM = '{http://www.w3.org/2005/Atom}'


class FeedError(Exception):
    """Safe error without provider body, URLs with secrets, or user content."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag.lower() in ('script', 'style'):
            self.hidden += 1
        elif not self.hidden:
            self.parts.append(' ')
    def handle_endtag(self, tag):
        if tag.lower() in ('script', 'style') and self.hidden:
            self.hidden -= 1
        elif not self.hidden:
            self.parts.append(' ')
    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def clean_text(text: str, limit: int) -> str:
    parser = _Text()
    parser.feed(text or '')
    return re.sub(r'\s+', ' ', ''.join(parser.parts)).strip()[:limit]


def safe_link(url: str) -> str | None:
    """Conservative public-looking link for display and dedup, not a fetch guard."""
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 or ord(c) == 127 for c in url):
        return None
    try:
        parts = urlsplit(url)
        if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password
                or parts.hostname.lower() == 'localhost' or '.' not in parts.hostname):
            return None
        _ = parts.port
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or '/', parts.query, ''))
    except ValueError:
        return None


def parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
    if date is None or date.tzinfo is None:
        return None
    return date.astimezone(timezone.utc)


def _text(item: ET.Element, *paths: str) -> str:
    for path in paths:
        node = item.find(path)
        if node is not None:
            result = ''.join(node.itertext()).strip()
            if result:
                return result
    return ''


def parse_feed(data: bytes, source_id: str, *, since: datetime, now: datetime,
               max_items: int = MAX_ITEMS) -> list[dict]:
    if source_id not in FEEDS or len(data) > MAX_BYTES:
        raise FeedError('Неизвестный или слишком большой источник')
    if since.tzinfo is None or now.tzinfo is None or since > now:
        raise ValueError('Нужен корректный интервал с часовым поясом')
    if re.search(br'<!\s*(?:DOCTYPE|ENTITY)', data, flags=re.I) or b'\x00' in data:
        raise FeedError('Небезопасный формат ленты')
    try:
        root = ET.fromstring(data)
    except (ET.ParseError, ValueError):
        raise FeedError('Не удалось прочитать ленту') from None
    if root.tag == 'rss':
        items = root.findall('./channel/item')
    elif root.tag == ATOM + 'feed':
        items = root.findall(ATOM + 'entry')
    else:
        raise FeedError('Неподдерживаемый формат ленты')
    # Never silently advance the source cursor past entries we did not inspect.
    if type(max_items) is not int or not 1 <= max_items <= 5000 or len(items) > max_items:
        raise FeedError('Слишком много записей в ленте')
    name, _ = FEEDS[source_id]
    result, seen = [], set()
    for item in items:
        title = clean_text(_text(item, 'title', ATOM+'title'), 300)
        link = _text(item, 'link')
        if not link:
            for node in item.findall(ATOM+'link'):
                if node.get('rel', 'alternate') == 'alternate' and node.get('href'):
                    link = node.get('href'); break
        link = safe_link(link)
        published = parse_date(_text(item, 'pubDate', ATOM+'published', ATOM+'updated'))
        if not title or not link or not published or not since <= published <= now + timedelta(minutes=5) or link in seen:
            continue
        seen.add(link)
        description = clean_text(_text(item, 'description', ATOM+'summary', ATOM+'content'), 1200)
        result.append({'id': hashlib.sha256(link.encode()).hexdigest(), 'source_id': source_id,
                       'source': name, 'title': title, 'summary': description, 'url': link,
                       'published_at': published.isoformat(), 'collected_at': now.astimezone(timezone.utc).isoformat(),
                       'status': 'candidate'})
    return result


def fetch_feed(source_id: str, *, since: datetime, now: datetime) -> list[dict]:
    if source_id not in FEEDS:
        raise FeedError('Неизвестный источник')
    url = FEEDS[source_id][1]
    request = urllib.request.Request(url, headers={'User-Agent': 'Expertbot-NewsTrial/1.0',
                                                   'Accept': 'application/rss+xml, application/atom+xml, application/xml, text/xml'})
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=15) as response:
            data = response.read(MAX_BYTES + 1)
    except (OSError, urllib.error.URLError):
        raise FeedError('Источник недоступен; лента пропущена') from None
    return parse_feed(data, source_id, since=since, now=now)

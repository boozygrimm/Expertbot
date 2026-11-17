"""Bounded HTTPS article extraction for a manually supplied news link.

No cookies, credentials, browser session, proxy, paywall bypass or subresources.
Each DNS answer is checked and the validated addresses are used for the actual
connection. Redirects are revalidated. Extracted text is untrusted source data.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit
import aiohttp
from aiohttp.helpers import is_ip_address

MAX_BYTES = 1_500_000
MAX_REDIRECTS = 3
MAX_TEXT = 6000


class ArticleError(Exception):
    pass


def validate_url(url: str) -> str:
    if not isinstance(url, str) or not url or len(url) > 2048 or any(ord(c) < 33 or ord(c) == 127 for c in url) or '\\' in url:
        raise ArticleError('Неверный адрес')
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() != 'https' or parts.username is not None or parts.password is not None or not parts.hostname or parts.port not in (None, 443):
            raise ValueError
        host = parts.hostname.rstrip('.').encode('idna').decode('ascii').lower()
        if len(host) > 253 or '.' not in host or host.endswith(('.local', '.localhost', '.internal')):
            raise ValueError
        if not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in host.split('.')):
            raise ValueError
        # aiohttp treats abbreviated or zero-padded numeric hosts as IPs and
        # bypasses the resolver, even when ipaddress rejects their syntax.
        if is_ip_address(host):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError
        return urlunsplit(('https', host, parts.path or '/', parts.query, ''))
    except (UnicodeError, ValueError):
        raise ArticleError('Неверный адрес') from None


def is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return address.is_global and not (address.is_multicast or address.is_reserved or address.is_unspecified)
    except ValueError:
        return False


class PublicResolver(aiohttp.abc.AbstractResolver):
    def __init__(self):
        self.delegate = aiohttp.resolver.DefaultResolver()

    async def resolve(self, host, port=0, family=socket.AF_UNSPEC):
        answers = await self.delegate.resolve(host, port, family)
        if not answers or any(not is_public_ip(item.get('host', '')) for item in answers):
            raise OSError('DNS не прошёл проверку')
        return answers

    async def close(self):
        await self.delegate.close()


class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.in_article = 0
        self.in_title = 0
        self.in_para = 0
        self.buffer = []
        self.title = []
        self.paragraphs = []
        self.article_paragraphs = []
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        if tag in ('script', 'style', 'nav', 'footer', 'header', 'aside', 'form', 'noscript'):
            self.hidden += 1
        if self.hidden:
            return
        if tag == 'meta':
            name = (attrs.get('property') or attrs.get('name') or '').lower()
            if name in ('og:title', 'og:description', 'description', 'og:image', 'og:image:url',
                        'og:image:secure_url', 'twitter:image', 'twitter:image:src') and attrs.get('content'):
                self.meta.setdefault(name, attrs['content'])
        elif tag == 'title':
            self.in_title += 1
        elif tag == 'article':
            self.in_article += 1
        elif tag == 'p':
            self.in_para += 1
            if self.in_para == 1:
                self.buffer = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ('script', 'style', 'nav', 'footer', 'header', 'aside', 'form', 'noscript') and self.hidden:
            self.hidden -= 1
            return
        if self.hidden:
            return
        if tag == 'title' and self.in_title:
            self.in_title -= 1
        elif tag == 'p' and self.in_para:
            self.in_para -= 1
            if self.in_para == 0:
                value = re.sub(r'\s+', ' ', ' '.join(self.buffer)).strip()
                if len(value) >= 30:
                    self.paragraphs.append(value)
                    if self.in_article:
                        self.article_paragraphs.append(value)
                self.buffer = []
        elif tag == 'article' and self.in_article:
            self.in_article -= 1

    def handle_data(self, data):
        if self.hidden:
            return
        if self.in_title:
            self.title.append(data)
        if self.in_para:
            self.buffer.append(data)


def extract_article_info(data: bytes, charset: str | None = None) -> tuple[str, str, str | None]:
    if len(data) > MAX_BYTES:
        raise ArticleError('Страница слишком большая')
    try:
        text = data.decode(charset or 'utf-8', errors='replace')
    except (LookupError, TypeError):
        raise ArticleError('Неизвестная кодировка') from None
    parser = ArticleParser()
    try:
        parser.feed(text)
    except (ValueError, RecursionError):
        raise ArticleError('Не удалось прочитать страницу') from None
    title = re.sub(r'\s+', ' ', parser.meta.get('og:title') or ' '.join(parser.title)).strip()[:300]
    paragraphs = parser.article_paragraphs or parser.paragraphs
    unique = list(dict.fromkeys(paragraphs))
    body = '\n'.join(unique)[:MAX_TEXT]
    if len(body.strip()) < 80:
        raise ArticleError('Не найден достаточно полный текст страницы')
    image = next((parser.meta.get(key) for key in ('og:image:secure_url', 'og:image',
                  'og:image:url', 'twitter:image', 'twitter:image:src') if parser.meta.get(key)), None)
    return title, body, image


def extract_article(data: bytes, charset: str | None = None) -> tuple[str, str]:
    title, body, _ = extract_article_info(data, charset)
    return title, body


async def fetch_article(url: str) -> dict:
    current = validate_url(url)
    # Telegram's landing page is not the post itself. Only a validated public
    # post link is rewritten to the official HTML widget, then matched by ID.
    from news_prototype.telegram_post import widget_for, extract_post_info
    telegram = widget_for(current)
    if telegram:
        current, post = telegram
    resolver = PublicResolver()
    timeout = aiohttp.ClientTimeout(total=25, connect=8, sock_read=10)
    try:
        connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, trust_env=False, auto_decompress=False,
                                         cookie_jar=aiohttp.DummyCookieJar(), headers={'User-Agent':'Expertbot-NewsTrial/1.0', 'Accept':'text/html,application/xhtml+xml',
                                                  'Accept-Encoding':'identity'}) as session:
            for hop in range(MAX_REDIRECTS + 1):
                async with session.get(current, allow_redirects=False) as response:
                    if response.status in (301, 302, 303, 307, 308):
                        if telegram:
                            # Never accept a different destination as this post.
                            raise ArticleError('Публичный пост недоступен')
                        location = response.headers.get('Location')
                        if not location or hop == MAX_REDIRECTS:
                            raise ArticleError('Слишком много перенаправлений')
                        current = validate_url(urljoin(current, location))
                        continue
                    if response.status != 200:
                        raise ArticleError('Страница недоступна')
                    if response.content_type not in ('text/html', 'application/xhtml+xml'):
                        raise ArticleError('Это не HTML-страница')
                    if response.headers.get('Content-Encoding', 'identity').lower() not in ('', 'identity'):
                        raise ArticleError('Неподдерживаемое сжатие страницы')
                    if response.content_length is not None and response.content_length > MAX_BYTES:
                        raise ArticleError('Страница слишком большая')
                    data = bytearray()
                    async for chunk in response.content.iter_chunked(16384):
                        data.extend(chunk)
                        if len(data) > MAX_BYTES:
                            raise ArticleError('Страница слишком большая')
                    video_status = None
                    if telegram:
                        title, body, media = extract_post_info(bytes(data), post, response.charset)
                        # Inspect only the exact same widget; never persist a
                        # signed CDN URL or download video during source review.
                        from news_prototype import telegram_public_source
                        try:
                            candidate = telegram_public_source.parse_widget(current, bytes(data))
                            if candidate.caption_complete and candidate.caption == body:
                                video_status = candidate.video_status
                        except telegram_public_source.SourceError:
                            pass
                    else:
                        title, body, image = extract_article_info(bytes(data), response.charset)
                        media = None
                        if image:
                            try:
                                media = {'url': validate_url(urljoin(current, image)), 'kind': 'article_image'}
                            except (ArticleError, ValueError):
                                pass
                    result = {'title': title, 'summary': body, 'resolved_url': current}
                    if media:
                        result['media'] = media
                    if video_status is not None:
                        result['telegram_video_status'] = video_status
                    return result
        raise ArticleError('Не удалось прочитать страницу')
    except ArticleError:
        raise
    except (aiohttp.ClientError, TimeoutError, OSError, ValueError):
        raise ArticleError('Не удалось безопасно получить страницу') from None
    finally:
        await resolver.close()

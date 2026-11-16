"""Read the exact public Telegram post from its unauthenticated web widget.

This does not use a Telegram account or follow links in the post. It may expose
one exact-post Telegram CDN image/thumbnail URL for a separate bounded fetch.
The widget markup is external and may change; ambiguous matches fail closed.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from news_prototype.article_fetch import ArticleError, MAX_BYTES, MAX_TEXT, validate_url

HOSTS = frozenset(('t.me', 'www.t.me', 'telegram.me', 'www.telegram.me'))
VOID_TAGS = frozenset(('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                       'link', 'meta', 'param', 'source', 'track', 'wbr'))


def widget_for(url: str) -> tuple[str, str] | None:
    """Return a canonical embed URL and expected post ID; reject non-post links."""
    parts = urlsplit(url)
    if parts.hostname not in HOSTS:
        return None
    segments = parts.path.strip('/').split('/')
    if len(segments) == 3 and segments[0].lower() == 's':
        segments = segments[1:]
    if (len(segments) != 2 or not re.fullmatch(r'[A-Za-z0-9_]{1,32}', segments[0])
            or not re.fullmatch(r'[1-9][0-9]{0,19}', segments[1])):
        raise ArticleError('Нужна ссылка на отдельный пост публичного Telegram-канала')
    channel, number = segments
    if channel.lower() in ('c', 's', 'joinchat', 'share', 'addstickers', 'proxy'):
        raise ArticleError('Это не ссылка на публичный пост')
    post = f'{channel}/{number}'
    return f'https://t.me/{post}?embed=1', post


def media_url(value: str) -> str:
    """Accept only Telegram's public image CDN; the fetcher still checks DNS."""
    url = validate_url(value)
    parts = urlsplit(url)
    if not re.fullmatch(r'cdn[0-9]{1,3}\.telesco\.pe', parts.hostname or '') or not parts.path.startswith('/file/'):
        raise ArticleError('Недопустимый адрес изображения')
    return url


def _media_from_style(style: str) -> str | None:
    match = re.search(r'''(?:^|;)\s*background-image\s*:\s*url\(\s*(["'])(https://[^"']+)\1\s*\)\s*(?:;|$)''', style, re.I)
    if not match:
        return None
    try:
        return media_url(match.group(2))
    except ArticleError:
        return None


class WidgetParser(HTMLParser):
    def __init__(self, post: str):
        super().__init__(convert_charrefs=True)
        self.post = post.casefold()
        self.stack = []
        self.matches = 0
        self.text_nodes = 0
        self.parts = []
        self.media = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        classes = set((attrs.get('class') or '').split())
        target, suppressed, body, media_owner = self.stack[-1][1:] if self.stack else (False, False, False, False)
        if 'tgme_widget_message' in classes:
            target = (attrs.get('data-post') or '').casefold() == self.post
            body = False
            media_owner = False
            if target:
                self.matches += 1
        suppressed = suppressed or tag in ('script', 'style', 'noscript', 'template') or 'tgme_widget_message_reply' in classes
        if (target and not suppressed and 'tgme_widget_message_text' in classes
                and 'js-message_text' in classes):
            self.text_nodes += 1
            body = True
        if target and body and not suppressed:
            if tag == 'br':
                self.parts.append('\n')
            elif tag == 'img' and attrs.get('alt'):
                # Telegram uses emoji images in message bodies.
                self.parts.append(attrs['alt'])
        photo = 'tgme_widget_message_photo_wrap' in classes
        video = 'tgme_widget_message_video_player' in classes
        if target and not suppressed and (photo or video):
            try:
                match = widget_for(validate_url(attrs.get('href') or ''))
                media_owner = bool(match and match[1].casefold() == self.post)
            except (ArticleError, ValueError):
                media_owner = False
        if target and not suppressed and media_owner and (photo or 'tgme_widget_message_video_thumb' in classes):
            value = _media_from_style(attrs.get('style') or '')
            if value:
                self.media.append((value, 'photo' if photo else 'video_thumbnail'))
        if tag not in VOID_TAGS:
            self.stack.append((tag, target, suppressed, body, media_owner))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        # Tolerate unclosed inline elements while restoring the proper parent.
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.stack:
            _, target, suppressed, body, _ = self.stack[-1]
            if target and body and not suppressed:
                self.parts.append(data)


def extract_post_info(data: bytes, post: str, charset: str | None = None) -> tuple[str, str, dict | None]:
    if len(data) > MAX_BYTES:
        raise ArticleError('Страница слишком большая')
    try:
        html = data.decode(charset or 'utf-8', errors='replace')
        parser = WidgetParser(post)
        parser.feed(html)
        parser.close()
    except (LookupError, TypeError, ValueError, RecursionError):
        raise ArticleError('Не удалось прочитать пост') from None
    if parser.matches != 1 or parser.text_nodes != 1:
        raise ArticleError('Публичный пост с текстом не найден однозначно')
    body = '\n'.join(re.sub(r'[\t\r\f\v ]+', ' ', line).strip()
                     for line in ''.join(parser.parts).split('\n')).strip()[:MAX_TEXT]
    if not body:
        raise ArticleError('В публичном посте нет доступного текста')
    # An album can include neighbouring posts: never borrow their media. Prefer
    # a photo over a video thumbnail, keeping the first item of the exact post.
    media = next(({'url': url, 'kind': kind} for url, kind in parser.media if kind == 'photo'), None)
    if media is None:
        media = next(({'url': url, 'kind': kind} for url, kind in parser.media if kind == 'video_thumbnail'), None)
    return f'Пост Telegram-канала @{post.split("/", 1)[0]}', body, media


def extract_post(data: bytes, post: str, charset: str | None = None) -> tuple[str, str]:
    title, body, _ = extract_post_info(data, post, charset)
    return title, body

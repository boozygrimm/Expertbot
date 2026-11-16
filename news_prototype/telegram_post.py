"""Read the exact public Telegram post from its unauthenticated web widget.

This does not use a Telegram account, follow links in the post or download media.
The widget markup is an external interface and may change without notice; if the
requested post or its text cannot be identified unambiguously, fail closed.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from news_prototype.article_fetch import ArticleError, MAX_BYTES, MAX_TEXT

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


class WidgetParser(HTMLParser):
    def __init__(self, post: str):
        super().__init__(convert_charrefs=True)
        self.post = post.casefold()
        self.stack = []
        self.matches = 0
        self.text_nodes = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        classes = set((attrs.get('class') or '').split())
        target, suppressed, body = self.stack[-1][1:] if self.stack else (False, False, False)
        if 'tgme_widget_message' in classes:
            target = (attrs.get('data-post') or '').casefold() == self.post
            body = False
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
        if tag not in VOID_TAGS:
            self.stack.append((tag, target, suppressed, body))

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
            _, target, suppressed, body = self.stack[-1]
            if target and body and not suppressed:
                self.parts.append(data)


def extract_post(data: bytes, post: str, charset: str | None = None) -> tuple[str, str]:
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
    return f'Пост Telegram-канала @{post.split("/", 1)[0]}', body

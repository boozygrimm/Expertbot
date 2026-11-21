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


GROUPED_MESSAGE = ('Пост входит в альбом (видео и/или несколько картинок), и Telegram '
                   'не отдал его текст отдельной карточкой. Ссылка вида '
                   'https://t.me/канал/НОМЕР?single открывает именно этот элемент; '
                   'если сообщение всё равно не читается, пришли полный текст источника.')


class GroupedPost(ArticleError):
    """The post belongs to an album, so its own card was not rendered."""
VOID_TAGS = frozenset(('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                       'link', 'meta', 'param', 'source', 'track', 'wbr'))


def single_widget_for(url: str) -> tuple[str, str] | None:
    """Embed just this post, not the album around it.

    Telegram renders a grouped post (a video or photo album) as one card whose
    ``data-post`` belongs to the *first* item only, so any later item of the
    album looks like "no text" on the plain embed. ``single=1`` is the same
    switch the web preview uses for "open this exact message"; yt-dlp relies on
    it too. It stays the same post, so no extra trust is involved.
    """
    widget = widget_for(url)
    if widget is None:
        return None
    return widget[0] + '&single=1', widget[1]


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
    """Read exactly one public card, plus the album it belongs to.

    Telegram renders an album as a single card owned by its first item: that
    card holds the caption and the group markup, while every other item is
    only an anchor with its own picture. Both facts are read here, so a later
    item is never mistaken for a post without text.
    """

    def __init__(self, post: str):
        super().__init__(convert_charrefs=True)
        self.post = post.casefold()
        self.stack = []
        self.matches = 0
        self.text_nodes = 0
        self.other_posts = 0
        self.saw_grouped = False
        self.grouped = False
        self.grouped_owner = None      # card that owns the album markup
        self.album = []                # album items, in rendered order
        self.texts = {}                # card -> text parts
        self.media = []                # (url, kind, owning card)
        self._card = None              # card being read right now
        self._text_card = None         # card whose body is being collected

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        classes = set((attrs.get('class') or '').split())
        suppressed, in_text, card, media_ok = (self.stack[-1][1:] if self.stack
                                               else (False, False, None, False))
        if 'tgme_widget_message' in classes and attrs.get('data-post'):
            card = (attrs['data-post'] or '').casefold()
            in_text = media_ok = False
            if card == self.post:
                self.matches += 1
            else:
                self.other_posts += 1
        self._card = card
        target = card == self.post
        if not target and not suppressed and ('tgme_widget_message_grouped_wrap' in classes
                                              or 'tgme_widget_message_video_player' in classes):
            # Album/multi-media markup rendered in place of the requested card.
            self.saw_grouped = True
        suppressed = suppressed or tag in ('script', 'style', 'noscript', 'template') or 'tgme_widget_message_reply' in classes
        if (not suppressed and 'tgme_widget_message_text' in classes
                and 'js-message_text' in classes):
            in_text = True
            if target:
                self.text_nodes += 1
            self._text_card = card
            self.texts.setdefault(card, [])
        if suppressed:
            in_text = False
        if in_text and target:
            if tag == 'br':
                self.texts.setdefault(card, []).append('\n')
            elif tag == 'img' and attrs.get('alt'):
                # Telegram uses emoji images in message bodies.
                self.texts.setdefault(card, []).append(attrs['alt'])
        if not suppressed and ('tgme_widget_message_grouped' in classes
                               or 'grouped_media_wrap' in classes):
            self.grouped = True
            # Only the card that carries the group markup may donate its text.
            if self.grouped_owner is None and card is not None:
                self.grouped_owner = card
        photo = 'tgme_widget_message_photo_wrap' in classes
        video = 'tgme_widget_message_video_player' in classes
        if not suppressed and (photo or video or 'tgme_widget_message_video_thumb' in classes):
            media_ok = self._media(attrs, classes, card, target, media_ok, photo, video)
        if tag not in VOID_TAGS:
            self.stack.append((tag, suppressed, in_text, card, media_ok))

    def _media(self, attrs, classes, card, target, inherited, photo, video):
        """Record one picture; return whether its children may still count."""
        href = attrs.get('href')
        try:
            match = widget_for(validate_url(href)) if isinstance(href, str) else None
        except (ArticleError, ValueError):
            match = None
        item = match[1].casefold() if match else None
        if target:
            # Inside the requested card only its own links count, so a reply or
            # a quoted neighbour can never donate media. A video player is a
            # wrapper, so its thumbnail may sit on a child element.
            if item != self.post and not (item is None and inherited):
                return inherited
            owner, inherited = self.post, True
        elif 'grouped_media_wrap' in classes and item:
            owner = item
            if item not in self.album:
                self.album.append(item)
        else:
            return inherited
        value = _media_from_style(attrs.get('style') or '')
        entry = (value, 'photo' if photo else 'video_thumbnail', owner)
        if value and entry not in self.media:
            self.media.append(entry)
        return inherited

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
            _, suppressed, in_text, card, _media_ok = self.stack[-1]
            if in_text and not suppressed and card is not None:
                self.texts.setdefault(card, []).append(data)


def _album_post(parser: 'WidgetParser', post: str) -> tuple[str, str, dict | None]:
    """Use the album caption and the requested item's own picture.

    The caption belongs to the group and sits on the card that owns the album
    markup, so every item of the group shares it; the picture stays
    item-specific because each anchor carries its own style.
    """
    wanted = post.casefold()
    captions = parser.texts.get(parser.grouped_owner) or []
    body = '\n'.join(re.sub(r'[\t\r\f\v ]+', ' ', line).strip()
                     for line in ''.join(captions).split('\n')).strip()[:MAX_TEXT]
    if not body:
        # No caption on the album's own card: nothing to borrow, so fail closed
        # instead of reading the requested item's captionless card as text.
        raise ArticleError('В публичном посте нет доступного текста')
    media = next(({'url': url, 'kind': kind} for url, kind, owner in parser.media
                  if owner == wanted and kind == 'photo'), None)
    if media is None:
        media = next(({'url': url, 'kind': kind} for url, kind, owner in parser.media
                      if owner == wanted and kind == 'video_thumbnail'), None)
    return f'Пост Telegram-канала @{post.split("/", 1)[0]}', body, media


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
    if parser.matches == 1 and parser.text_nodes == 1:
        pass
    elif parser.grouped and post.casefold() in parser.album:
        # Telegram stores one caption per album and attaches it to the album's
        # first item. Any other item of that album has no text node at all, so
        # the group's caption is this post's text too. The album membership is
        # taken from the same widget, never guessed from neighbouring posts.
        return _album_post(parser, post)
    elif parser.matches != 1:
        # The widget rendered some post, just not this one, and that post
        # carries album/multi-media markup: this post is another item of it.
        if parser.matches == 0 and parser.other_posts and parser.saw_grouped:
            raise GroupedPost(GROUPED_MESSAGE)
        raise ArticleError('Публичный пост с текстом не найден однозначно')
    else:
        raise ArticleError('В публичном посте нет текста, доступного без входа в аккаунт')
    body = '\n'.join(re.sub(r'[\t\r\f\v ]+', ' ', line).strip()
                     for line in ''.join(parser.texts.get(parser.post, [])).split('\n')).strip()[:MAX_TEXT]
    if not body:
        raise ArticleError('В публичном посте нет доступного текста')
    # An album can include neighbouring posts: never borrow their media. Prefer
    # a photo over a video thumbnail, keeping the first item of the exact post.
    media = next(({'url': url, 'kind': kind} for url, kind, owner in parser.media
                  if owner == parser.post and kind == 'photo'), None)
    if media is None:
        media = next(({'url': url, 'kind': kind} for url, kind, owner in parser.media
                      if owner == parser.post and kind == 'video_thumbnail'), None)
    return f'Пост Telegram-канала @{post.split("/", 1)[0]}', body, media


def extract_post(data: bytes, post: str, charset: str | None = None) -> tuple[str, str]:
    title, body, _ = extract_post_info(data, post, charset)
    return title, body


def grouped_post_error() -> str:
    """Owner-facing text for GroupedPost; safe to show, contains no source data."""
    return GROUPED_MESSAGE


def safe_channel_name(value: object) -> str | None:
    """Conservatively accept a short display name for speech, never a URL/command."""
    import unicodedata
    if not isinstance(value, str) or len(value) > 300:
        return None
    # Decorative emoji are discarded; control and format characters are not
    # accepted, including bidi controls and newlines.
    if any(unicodedata.category(c)[0] == 'C' for c in value):
        return None
    value = ''.join(c for c in value if unicodedata.category(c)[0] != 'S')
    value = re.sub(r'\s+', ' ', value).strip()
    if (not 1 <= len(value) <= 60 or not any(c.isalpha() for c in value)
            or not all(c.isalnum() or c in " '-.,!&()" or unicodedata.category(c)[0] == 'M' for c in value)
            or 'http' in value.casefold() or 'www.' in value.casefold()):
        return None
    return value


def channel_opening(url: str, name: object) -> str | None:
    """Form a source introduction only for a validated public Telegram post."""
    try:
        clean = safe_channel_name(name)
        if clean and widget_for(validate_url(url)):
            return f'В телеграм-канале «{clean}» поделились новостью.'
    except (ArticleError, TypeError, ValueError):
        pass
    return None


class ChannelNameParser(HTMLParser):
    """Read only the exact post's owner header, never a forward or reply."""
    def __init__(self, post: str):
        super().__init__(convert_charrefs=True)
        self.post = post.casefold()
        self.channel = post.split('/', 1)[0].casefold()
        self.stack = []
        self.matches = 0
        self.owners = 0
        self.valid = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower(); attrs = dict(attrs)
        classes = set((attrs.get('class') or '').split())
        target, suppressed, author, owner = self.stack[-1][1:] if self.stack else (False, False, False, False)
        if 'tgme_widget_message' in classes:
            target = not suppressed and (attrs.get('data-post') or '').casefold() == self.post
            author = owner = False
            if target: self.matches += 1
        suppressed = (suppressed or tag in ('script', 'style', 'noscript', 'template')
                      or 'tgme_widget_message_reply' in classes
                      or 'tgme_widget_message_forwarded_from' in classes)
        if target and not suppressed and 'tgme_widget_message_author' in classes:
            author = True
        if target and not suppressed and author and tag == 'a' and 'tgme_widget_message_owner_name' in classes:
            self.owners += 1
            try:
                parts = urlsplit(attrs.get('href') or '')
                owner = (parts.scheme == 'https' and parts.hostname in HOSTS and not parts.username
                         and not parts.password and parts.port in (None, 443) and not parts.query
                         and not parts.fragment and parts.path.strip('/').casefold() == self.channel)
            except ValueError:
                owner = False
            self.valid = owner
        if tag not in VOID_TAGS:
            self.stack.append((tag, target, suppressed, author, owner))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag.lower():
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.stack:
            _, target, suppressed, _, owner = self.stack[-1]
            if target and not suppressed and owner:
                self.parts.append(data)


def extract_channel_name(data: bytes, post: str, charset: str | None = None) -> str | None:
    """Return a validated display name or None; a missing name never blocks the post."""
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        return None
    try:
        parser = ChannelNameParser(post)
        parser.feed(data.decode(charset or 'utf-8', errors='replace'))
        parser.close()
        if parser.matches != 1 or parser.owners != 1 or not parser.valid:
            return None
        return safe_channel_name(''.join(parser.parts))
    except (LookupError, TypeError, ValueError, RecursionError, IndexError):
        return None

"""Bounded public Telegram post and video acquisition, independent of workers.

The exact post caption and video candidate come from the same public widget.
No login, provider, queue, database, publication or implicit network call occurs
on import. Video download is a separate opt-in call into a new private folder.
This is a reusable building block, not production pipeline integration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from html.parser import HTMLParser
import http.client
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.parse import urlsplit

from news_prototype import telegram_video_source_probe as probe

MAX_TEXT = 6000
MAX_HTML = probe.MAX_HTML
MAX_VIDEO = probe.MAX_VIDEO
MAX_SECONDS = 600
MAX_PIXELS = 4096 * 4096
DOWNLOAD_SECONDS = 120
POST_HOSTS = frozenset(('t.me', 'www.t.me', 'telegram.me', 'www.telegram.me'))
VOID = probe.VOID


class SourceError(Exception):
    """A fixed, sanitized acquisition failure; never includes a signed URL."""


@dataclass(frozen=True)
class Post:
    url: str
    post_id: str
    caption: str
    video_status: str  # available | none | ambiguous
    video_url: str | None = field(default=None, repr=False)
    caption_complete: bool = True


@dataclass(frozen=True)
class Video:
    path: Path
    sha256: str
    size: int
    duration: float
    width: int
    height: int
    has_audio: bool


def canonical_post(url: str) -> tuple[str, str]:
    """Accept only one public channel post, discard untrusted query parameters."""
    if (not isinstance(url, str) or not url or len(url) > 2048
            or any(ord(c) < 33 or ord(c) == 127 for c in url) or '\\' in url):
        raise SourceError('Неверная ссылка на пост')
    try:
        parts = urlsplit(url)
        if (parts.scheme != 'https' or parts.username is not None or parts.password is not None
                or parts.port not in (None, 443) or (parts.hostname or '').lower() not in POST_HOSTS):
            raise ValueError
        segments = parts.path.strip('/').split('/')
        if len(segments) == 3 and segments[0].lower() == 's':
            segments = segments[1:]
        if (len(segments) != 2 or not re.fullmatch(r'[A-Za-z0-9_]{1,32}', segments[0])
                or not re.fullmatch(r'[1-9][0-9]{0,19}', segments[1])
                or segments[0].lower() in ('c', 's', 'joinchat', 'share', 'addstickers', 'proxy')):
            raise ValueError
        post_id = '/'.join(segments)
        return f'https://t.me/{post_id}', post_id
    except ValueError:
        raise SourceError('Нужна ссылка на отдельный публичный пост') from None


class CaptionParser(HTMLParser):
    def __init__(self, post_id: str):
        super().__init__(convert_charrefs=True)
        self.post_id = post_id.casefold()
        self.stack = []
        self.matches = 0
        self.bodies = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        classes = set((attrs.get('class') or '').split())
        target, suppressed, body = self.stack[-1][1:] if self.stack else (False, False, False)
        if 'tgme_widget_message' in classes:
            target = (attrs.get('data-post') or '').casefold() == self.post_id
            body = False
            if target:
                self.matches += 1
        suppressed = suppressed or tag in ('script', 'style', 'noscript', 'template') or 'tgme_widget_message_reply' in classes
        if target and not suppressed and {'tgme_widget_message_text', 'js-message_text'} <= classes:
            self.bodies += 1
            body = True
        if target and body and not suppressed:
            if tag == 'br':
                self.parts.append('\n')
            elif tag == 'img' and attrs.get('alt'):
                self.parts.append(attrs['alt'])
        if tag not in VOID:
            self.stack.append((tag, target, suppressed, body))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag.lower():
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.stack:
            _, target, suppressed, body = self.stack[-1]
            if target and not suppressed and body:
                self.parts.append(data)


def parse_widget(url: str, html: bytes) -> Post:
    canonical, post_id = canonical_post(url)
    if not isinstance(html, bytes) or len(html) > MAX_HTML:
        raise SourceError('Страница поста слишком большая или повреждена')
    try:
        text = html.decode('utf-8', errors='replace')
        caption_parser = CaptionParser(post_id)
        video_parser = probe.VideoParser(post_id)
        for parser in (caption_parser, video_parser):
            parser.feed(text)
            parser.close()
        if caption_parser.matches != 1 or caption_parser.bodies != 1 or video_parser.matches != 1:
            raise ValueError
        caption = '\n'.join(re.sub(r'[\t\r\f\v ]+', ' ', line).strip()
                            for line in ''.join(caption_parser.parts).split('\n')).strip()
        # A prefix is sufficient for display, but never enough to assert that
        # a later post is unchanged. Record truncation before slicing.
        caption_complete = len(caption) <= MAX_TEXT
        caption = caption[:MAX_TEXT]
        caption = ''.join(c for c in caption if (ord(c) >= 32 and ord(c) != 127) or c == '\n')
        if not caption:
            raise ValueError
    except (ValueError, TypeError, RecursionError):
        raise SourceError('Пост с подписью не найден однозначно') from None
    candidates = list(dict.fromkeys(video_parser.videos))
    if video_parser.video_tags == 0:
        return Post(canonical, post_id, caption, 'none', caption_complete=caption_complete)
    if video_parser.video_tags != 1 or len(candidates) != 1:
        return Post(canonical, post_id, caption, 'ambiguous', caption_complete=caption_complete)
    return Post(canonical, post_id, caption, 'available', candidates[0], caption_complete)


def fetch_post(url: str) -> Post:
    """Fetch one public widget with pinned vetted DNS and without redirects."""
    canonical, post_id = canonical_post(url)
    conn = None
    try:
        conn, response = probe.get_response('t.me', f'/{post_id}?embed=1')
        if (response.getheader('Content-Encoding') or 'identity').lower() not in ('', 'identity'):
            raise SourceError('Неподдерживаемое сжатие страницы')
        header = response.getheader('Content-Length')
        if header is not None and (not header.isdigit() or int(header) > MAX_HTML):
            raise SourceError('Страница поста слишком большая')
        html = response.read(MAX_HTML + 1)
        if header is not None and len(html) != int(header):
            raise SourceError('Страница поста получена не полностью')
        return parse_widget(canonical, html)
    except SourceError:
        raise
    except (probe.ProbeError, OSError, http.client.HTTPException, ValueError, TimeoutError):
        raise SourceError('Не удалось безопасно получить пост Telegram') from None
    finally:
        if conn is not None:
            conn.close()


def media_env() -> dict[str, str]:
    """Never pass inherited application credentials to media subprocesses."""
    return {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'TZ') if key in os.environ}


def inspect(path: Path, ffprobe: str, ffmpeg: str) -> tuple[float, int, int, bool]:
    """Check bounded local media and decode the complete selected streams."""
    base = ['-nostdin']  # ffprobe has no -nostdin option, passed only to ffmpeg below
    whitelist = ['-protocol_whitelist', 'file,pipe', '-format_whitelist', 'mov,mp4,m4a,3gp,3g2,mj2']
    try:
        result = subprocess.run([ffprobe, '-v', 'error', *whitelist, '-show_entries',
                                 'format=duration,format_name:stream=codec_type,width,height', '-of', 'json', str(path)],
                                check=True, timeout=45, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env=media_env())
        if len(result.stdout) > 200_000:
            raise ValueError
        info = json.loads(result.stdout)
        duration = float(info['format']['duration'])
        formats = info['format']['format_name'].split(',')
        videos = [s for s in info['streams'] if s.get('codec_type') == 'video']
        audios = [s for s in info['streams'] if s.get('codec_type') == 'audio']
        if len(videos) != 1 or 'mp4' not in formats or not math.isfinite(duration) or not 0 < duration <= MAX_SECONDS:
            raise ValueError
        width, height = int(videos[0]['width']), int(videos[0]['height'])
        if not 0 < width <= 4096 or not 0 < height <= 4096 or width * height > MAX_PIXELS:
            raise ValueError
        subprocess.run([ffmpeg, *base, '-v', 'error', '-xerror', '-threads', '2', *whitelist, '-i', str(path),
                        '-map', '0:v:0', '-map', '0:a:0?', '-sn', '-dn', '-f', 'null', '-'],
                       check=True, timeout=240, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       env=media_env())
        return duration, width, height, bool(audios)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, IndexError, AttributeError, OverflowError):
        raise SourceError('Видео не прошло локальную проверку') from None


def download_video(post: Post, folder: Path, *, ffmpeg: str | None = None, ffprobe: str | None = None) -> Video:
    """Fetch and fully verify one exact-post video; never follow redirects."""
    if not isinstance(post, Post) or post.video_status != 'available' or not post.video_url:
        raise SourceError('Однозначное видео поста недоступно')
    canonical, post_id = canonical_post(post.url)
    if canonical != post.url or post_id != post.post_id:
        raise SourceError('Неверная привязка видео к посту')
    ffmpeg = ffmpeg or shutil.which('ffmpeg')
    ffprobe = ffprobe or shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise SourceError('Для проверки нужны ffmpeg и ffprobe')
    try:
        host, target = probe.safe_video_url(post.video_url)
    except probe.ProbeError:
        raise SourceError('Недопустимый адрес видео') from None
    folder = Path(folder)
    folder.mkdir(mode=0o700, parents=False, exist_ok=False)
    partial, output = folder / 'source.partial.mp4', folder / 'source.mp4'
    conn = None
    try:
        conn, response = probe.get_response(host, target)
        if (response.getheader('Content-Encoding') or 'identity').lower() not in ('', 'identity'):
            raise SourceError('Неподдерживаемое сжатие видео')
        length = response.getheader('Content-Length')
        if length is not None and (not length.isdigit() or not 0 < int(length) <= MAX_VIDEO):
            raise SourceError('Размер видео превышает ограничение или неверен')
        digest, size = hashlib.sha256(), 0
        deadline = time.monotonic() + DOWNLOAD_SECONDS
        with partial.open('xb') as out:
            while True:
                if time.monotonic() > deadline:
                    raise SourceError('Истекло время загрузки видео')
                chunk = response.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_VIDEO:
                    raise SourceError('Видео превышает ограничение размера')
                digest.update(chunk)
                out.write(chunk)
        if length is not None and size != int(length):
            raise SourceError('Видео скачано не полностью')
        with partial.open('rb') as file:
            header = file.read(16)
        if size < 16 or header[4:8] != b'ftyp':
            raise SourceError('Источник не является MP4')
        duration, width, height, has_audio = inspect(partial, ffprobe, ffmpeg)
        partial.replace(output)
        return Video(output, digest.hexdigest(), size, duration, width, height, has_audio)
    except SourceError:
        raise
    except (probe.ProbeError, OSError, http.client.HTTPException, ValueError, TimeoutError):
        raise SourceError('Не удалось безопасно получить видео поста') from None
    finally:
        if conn is not None:
            conn.close()
        if partial.exists() and not partial.is_symlink():
            partial.unlink()


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description='Прочитать точный публичный пост Telegram; опционально проверить его видео.')
    parser.add_argument('url', help='Ссылка на публичный пост')
    parser.add_argument('--video-folder', type=Path, help='Новая папка для проверки видео; без параметра видео не скачивается')
    args = parser.parse_args()
    try:
        post = fetch_post(args.url)
        print('Пост:', post.url)
        print('Подпись:\n' + post.caption)
        if post.video_status != 'available':
            print('Видео недоступно или неоднозначно; можно использовать отдельно проверенную картинку или нейтральный экран.')
            return 0
        if args.video_folder is None:
            print('Видео найдено; не скачивалось. Для проверки укажи --video-folder с новой папкой.')
            return 0
        video = download_video(post, args.video_folder)
        print(f'Проверено видео: {video.duration:.1f} с, {video.width}×{video.height}, {video.size} байт, '
              f'аудиодорожка: {"есть" if video.has_audio else "нет"}.')
        print('SHA-256:', video.sha256)
        print('Файл:', video.path)
        print('Содержание и права использования нужно проверить отдельно; рендер и отправка не запускались.')
        return 0
    except FileExistsError:
        print('Папка уже существует; не перезаписываю её.')
    except SourceError as exc:
        print('Не удалось подготовить источник:', str(exc))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())

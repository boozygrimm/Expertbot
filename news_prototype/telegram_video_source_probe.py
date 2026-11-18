"""One-off, bounded probe of the source video of one public Telegram post.

Uses only the Python standard library. No cookies, account login, bot settings,
providers, rendering or database changes. Redirects and untrusted hosts fail
closed; DNS is checked and the vetted address is used for the connection.
"""
from __future__ import annotations

from html.parser import HTMLParser
import http.client
import ipaddress
import json
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import urllib.parse

POST = 'SenseiDurova/7266'
FOLDER = Path('/data/video-prototype/telegram-video-probe-SenseiDurova-7266')
MAX_HTML = 1_500_000
MAX_VIDEO = 40 * 1024 * 1024
VOID = frozenset(('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                  'link', 'meta', 'param', 'source', 'track', 'wbr'))


class ProbeError(Exception):
    pass


def safe_video_url(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) < 33 or ord(c) == 127 for c in value) or '\\' in value:
        raise ProbeError('Недопустимый адрес видео')
    try:
        parts = urllib.parse.urlsplit(value)
        host = (parts.hostname or '').lower()
        if (parts.scheme != 'https' or parts.username is not None or parts.password is not None
                or parts.port not in (None, 443) or not re.fullmatch(r'cdn[0-9]{1,3}\.telesco\.pe', host)
                or not parts.path.startswith('/file/') or parts.fragment):
            raise ValueError
        target = urllib.parse.urlunsplit(('', '', parts.path, parts.query, ''))
        return host, target
    except ValueError:
        raise ProbeError('Недопустимый адрес видео') from None


def is_post_link(value: str, expected: str) -> bool:
    try:
        parts = urllib.parse.urlsplit(value)
        return (parts.scheme == 'https' and parts.username is None and parts.password is None
                and parts.port in (None, 443) and (parts.hostname or '').lower() in ('t.me', 'www.t.me', 'telegram.me', 'www.telegram.me')
                and parts.path.strip('/').casefold() == expected.casefold())
    except (TypeError, ValueError):
        return False


class VideoParser(HTMLParser):
    def __init__(self, post: str):
        super().__init__(convert_charrefs=True)
        self.post = post
        self.stack = []
        self.matches = 0
        self.videos = []
        self.video_tags = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        classes = set((attrs.get('class') or '').split())
        target, suppressed, owner, in_video = self.stack[-1][1:] if self.stack else (False, False, False, False)
        if 'tgme_widget_message' in classes:
            target = (attrs.get('data-post') or '').casefold() == self.post.casefold()
            owner = in_video = False
            if target:
                self.matches += 1
        suppressed = suppressed or tag in ('script', 'style', 'noscript', 'template') or 'tgme_widget_message_reply' in classes
        if target and not suppressed and 'tgme_widget_message_video_player' in classes:
            owner = is_post_link(attrs.get('href'), self.post)
        if target and not suppressed and tag == 'video':
            self.video_tags += 1
            in_video = True
        if target and not suppressed and owner and (tag == 'video' or (tag == 'source' and in_video)):
            src = attrs.get('src')
            if src:
                try:
                    safe_video_url(src)
                    self.videos.append(src)
                except ProbeError:
                    pass
        if tag not in VOID:
            self.stack.append((tag, target, suppressed, owner, in_video))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag.lower():
                del self.stack[i:]
                break


def extract_video(html: bytes, post: str) -> tuple[str, int]:
    if len(html) > MAX_HTML:
        raise ProbeError('Страница слишком большая')
    parser = VideoParser(post)
    parser.feed(html.decode('utf-8', errors='replace'))
    parser.close()
    candidates = list(dict.fromkeys(parser.videos))
    if parser.matches != 1 or len(candidates) != 1:
        raise ProbeError(f'Исходное видео не найдено однозначно (постов: {parser.matches}, видеоэлементов: {parser.video_tags}, адресов: {len(candidates)})')
    return candidates[0], parser.video_tags


def vetted_address(host: str) -> str:
    answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not answers:
        raise ProbeError('DNS не вернул адрес')
    addresses = []
    for item in answers:
        address = ipaddress.ip_address(item[4][0])
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if not address.is_global or address.is_multicast or address.is_reserved or address.is_unspecified:
            raise ProbeError('DNS не прошёл проверку публичного адреса')
        addresses.append(str(address))
    return addresses[0]


class VettedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str):
        super().__init__(host, port=443, timeout=30, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, self.port), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def get_response(host: str, target: str):
    connection = VettedHTTPSConnection(host, vetted_address(host))
    try:
        connection.request('GET', target, headers={'User-Agent': 'Expertbot-public-post-probe/1.0', 'Accept-Encoding': 'identity'})
        response = connection.getresponse()
        if response.status != 200:
            raise ProbeError(f'Источник недоступен (HTTP {response.status})')
        return connection, response
    except BaseException:
        connection.close()
        raise


def safe_env():
    import os
    env = os.environ.copy()
    for name in ('BOT_TOKEN', 'TELEGRAM_TOKEN', 'VIDEO_WORKER_KEY', 'STORAGE_CHANNEL_ID',
                 'GEMINI_TTS_KEYS', 'GEMINI_KEYS', 'GEMINI_API_KEYS', 'GEMINI_API_KEY'):
        env.pop(name, None)
    return env


def inspect_video(path: Path) -> tuple[float, int, int] | None:
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        return None
    result = subprocess.run([ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe',
                             '-format_whitelist', 'mov,mp4,m4a,3gp,3g2,mj2',
                             '-show_entries', 'format=duration:stream=codec_type,width,height',
                             '-of', 'json', str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            check=True, timeout=45, env=safe_env())
    if len(result.stdout) > 200_000:
        raise ProbeError('Слишком большие метаданные')
    try:
        data = json.loads(result.stdout)
        videos = [s for s in data.get('streams', []) if s.get('codec_type') == 'video']
        duration = float(data['format']['duration'])
        if len(videos) != 1:
            raise ValueError
        width, height = int(videos[0]['width']), int(videos[0]['height'])
        if not 0 < duration <= 600 or not 0 < width <= 7680 or not 0 < height <= 4320:
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ProbeError('Не удалось проверить видеопоток') from None
    return duration, width, height


def probe(folder: Path) -> None:
    if folder.exists() or folder.is_symlink():
        raise FileExistsError
    conn, response = get_response('t.me', '/' + POST + '?embed=1')
    try:
        html = response.read(MAX_HTML + 1)
    finally:
        conn.close()
    source, _ = extract_video(html, POST)
    host, target = safe_video_url(source)
    conn, response = get_response(host, target)
    try:
        header = response.getheader('Content-Length')
        if header is not None:
            if not header.isdigit() or int(header) <= 0 or int(header) > MAX_VIDEO:
                raise ProbeError('Видео превышает лимит пробы 40 МиБ или неверен его размер')
            declared = int(header)
        else:
            declared = None
        folder.mkdir(mode=0o700, parents=False, exist_ok=False)
        partial = folder / 'source.partial.mp4'
        size = 0
        with partial.open('xb') as out:
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_VIDEO:
                    raise ProbeError('Видео превышает лимит пробы 40 МиБ')
                out.write(chunk)
        if declared is not None and size != declared:
            raise ProbeError('Видео скачалось не полностью')
    finally:
        conn.close()
    with partial.open('rb') as file:
        header = file.read(16)
    if size < 16 or header[4:8] != b'ftyp':
        raise ProbeError('Полученный файл не похож на MP4')
    metadata = inspect_video(partial)
    video = folder / 'source.mp4'
    partial.rename(video)
    if metadata is None:
        print(f'Получен MP4 ({size} байт), но ffprobe не найден; видеопоток ещё не проверен.')
    else:
        duration, width, height = metadata
        print(f'Видео получено и проверено: {duration:.1f} с, {width}×{height}, {size} байт.')
    print(f'Файл сохранён: {video}')
    print('Новая озвучка, рендер, отправка и изменения бота не выполнялись.')


def main() -> int:
    try:
        probe(FOLDER)
        return 0
    except FileExistsError:
        print('Папка пробы уже существует. Не повторяй скачивание вслепую.')
    except ProbeError as exc:
        print('Видео не получено:', str(exc))
    except (OSError, http.client.HTTPException, subprocess.SubprocessError, ValueError):
        print('Проба остановлена: ошибка сети, файлов или проверки видео.')
    print('Пришли вывод; если осталась частичная загрузка, не повторяй запуск вслепую.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())

"""One-off bounded probe of one public Telegram post's source video.

No cookies, account login, bot settings, provider calls, rendering or database
changes. Downloads only the exact post's public video to a new private folder;
refuses redirects, unknown hosts, oversized files and ambiguous markup.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
import subprocess

import aiohttp
from news_prototype import article_fetch, telegram_post
from news_prototype.podcast_audio_trial import normalized_env

POST_URL = 'https://t.me/SenseiDurova/7266'
FOLDER = Path('/data/video-prototype/telegram-video-probe-SenseiDurova-7266')
MAX_VIDEO = 40 * 1024 * 1024


class VideoParser(telegram_post.WidgetParser):
    def __init__(self, post: str):
        super().__init__(post)
        self.videos = []

    def handle_starttag(self, tag, attrs):
        attrs_map = dict(attrs)
        if self.stack:
            _, target, suppressed, _, media_owner = self.stack[-1]
            inside_video = any(item[0] == 'video' for item in self.stack)
            if (target and not suppressed and media_owner and
                    (tag.lower() == 'video' or (tag.lower() == 'source' and inside_video))):
                src = attrs_map.get('src')
                if src:
                    try:
                        self.videos.append(telegram_post.media_url(src))
                    except article_fetch.ArticleError:
                        pass
        super().handle_starttag(tag, attrs)


def extract_video(html: bytes, post: str) -> str:
    if len(html) > article_fetch.MAX_BYTES:
        raise ValueError('Страница слишком большая')
    parser = VideoParser(post)
    parser.feed(html.decode('utf-8', errors='replace'))
    parser.close()
    candidates = list(dict.fromkeys(parser.videos))
    if parser.matches != 1 or len(candidates) != 1:
        raise ValueError('Исходное видео в публичном посте не найдено однозначно')
    return candidates[0]


def inspect_video(path: Path) -> tuple[float, int, int]:
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        raise ValueError('Не найден ffprobe для проверки видеоданных')
    result = subprocess.run([ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe',
                             '-format_whitelist', 'mov,mp4,m4a,3gp,3g2,mj2',
                             '-show_entries', 'format=duration:stream=codec_type,width,height',
                             '-of', 'json', str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            check=True, timeout=45, env=normalized_env())
    if len(result.stdout) > 200_000:
        raise ValueError('Слишком большие метаданные')
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
        raise ValueError('Не удалось проверить видеопоток') from None
    return duration, width, height


async def probe(url: str, folder: Path) -> None:
    item = telegram_post.widget_for(article_fetch.validate_url(url))
    if not item:
        raise ValueError('Нужен публичный пост Telegram')
    embed, post = item
    timeout = aiohttp.ClientTimeout(total=120, connect=15, sock_read=30)
    connector = aiohttp.TCPConnector(resolver=article_fetch.PublicResolver())
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, trust_env=False, auto_decompress=False,
                                     headers={'User-Agent': 'Expertbot-public-post-probe/1.0',
                                              'Accept-Encoding': 'identity'}) as session:
        async with session.get(embed, allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError(f'Пост недоступен (HTTP {response.status})')
            chunks, total = [], 0
            async for chunk in response.content.iter_chunked(65536):
                total += len(chunk)
                if total > article_fetch.MAX_BYTES:
                    raise ValueError('Страница слишком большая')
                chunks.append(chunk)
            html = b''.join(chunks)
        source = extract_video(html, post)
        async with session.get(source, allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError(f'Видео недоступно (HTTP {response.status})')
            declared = response.content_length
            if declared is not None and (declared <= 0 or declared > MAX_VIDEO):
                raise ValueError('Видео превышает лимит пробы 40 МиБ')
            folder.mkdir(mode=0o700, parents=False, exist_ok=False)
            partial = folder / 'source.partial.mp4'
            size = 0
            with partial.open('xb') as out:
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > MAX_VIDEO:
                        raise ValueError('Видео превышает лимит пробы 40 МиБ')
                    out.write(chunk)
            if declared is not None and size != declared:
                raise ValueError('Видео скачалось не полностью')
    with partial.open('rb') as file:
        header = file.read(16)
    if size < 16 or header[4:8] != b'ftyp':
        raise ValueError('Полученный файл не похож на MP4')
    duration, width, height = inspect_video(partial)
    video = folder / 'source.mp4'
    partial.rename(video)
    print(f'Видео получено и проверено: {duration:.1f} с, {width}×{height}, {size} байт.')
    print(f'Файл сохранён: {video}')
    print('Новая озвучка, рендер, отправка и изменения бота не выполнялись.')


def main() -> int:
    try:
        asyncio.run(probe(POST_URL, FOLDER))
        return 0
    except FileExistsError:
        print('Папка пробы уже существует. Не повторяй скачивание вслепую.')
    except (ValueError, article_fetch.ArticleError) as exc:
        print('Видео не получено:', str(exc))
    except (OSError, aiohttp.ClientError, asyncio.TimeoutError, subprocess.SubprocessError):
        print('Проба остановлена: ошибка сети, файлов или проверки видео.')
    print('Пришли вывод; если осталась частичная загрузка, не повторяй запуск вслепую.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())

"""Fetch bounded public images with validated DNS, without credentials."""
from __future__ import annotations

import asyncio
from urllib.parse import urljoin
import aiohttp
from news_prototype.article_fetch import ArticleError, PublicResolver, validate_url
from news_prototype.news_image_codec import MAX_INPUT_BYTES, make_thumbnail
from news_prototype.telegram_post import media_url

MAX_IMAGE_REDIRECTS = 3


async def fetch_thumbnail(url: str, *, article: bool = False) -> str:
    # Telegram stays restricted to the exact approved CDN, without redirects.
    current = validate_url(url) if article else media_url(url)
    resolver = PublicResolver()
    timeout = aiohttp.ClientTimeout(total=20, connect=8, sock_read=10)
    try:
        connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, trust_env=False,
                auto_decompress=False, cookie_jar=aiohttp.DummyCookieJar(),
                headers={'User-Agent': 'Expertbot-NewsTrial/1.0', 'Accept': 'image/jpeg,image/png,image/webp',
                         'Accept-Encoding': 'identity'}) as session:
            for hop in range(MAX_IMAGE_REDIRECTS + 1):
                async with session.get(current, allow_redirects=False) as response:
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.headers.get('Location')
                        if not article or not location or hop == MAX_IMAGE_REDIRECTS:
                            raise ArticleError('Перенаправление изображения недоступно')
                        current = validate_url(urljoin(current, location))
                        continue
                    if response.status != 200 or response.content_type not in ('image/jpeg', 'image/png', 'image/webp'):
                        raise ArticleError('Изображение недоступно')
                    if response.headers.get('Content-Encoding', 'identity').lower() not in ('', 'identity'):
                        raise ArticleError('Неподдерживаемое сжатие')
                    if response.content_length is not None and response.content_length > MAX_INPUT_BYTES:
                        raise ArticleError('Изображение слишком большое')
                    data = bytearray()
                    async for chunk in response.content.iter_chunked(16384):
                        data.extend(chunk)
                        if len(data) > MAX_INPUT_BYTES:
                            raise ArticleError('Изображение слишком большое')
                    return await asyncio.to_thread(make_thumbnail, bytes(data))
        raise ArticleError('Изображение недоступно')
    except ArticleError:
        raise
    except (aiohttp.ClientError, TimeoutError, OSError, ValueError):
        raise ArticleError('Не удалось безопасно получить изображение') from None
    finally:
        await resolver.close()

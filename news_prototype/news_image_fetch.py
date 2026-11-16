"""Fetch only a validated Telegram CDN image, without redirects or credentials."""
from __future__ import annotations

import asyncio
import aiohttp
from news_prototype.article_fetch import ArticleError, PublicResolver
from news_prototype.news_image_codec import MAX_INPUT_BYTES, make_thumbnail
from news_prototype.telegram_post import media_url


async def fetch_thumbnail(url: str) -> str:
    url = media_url(url)
    resolver = PublicResolver()
    timeout = aiohttp.ClientTimeout(total=20, connect=8, sock_read=10)
    try:
        connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, trust_env=False,
                auto_decompress=False, cookie_jar=aiohttp.DummyCookieJar(),
                headers={'User-Agent': 'Expertbot-NewsTrial/1.0', 'Accept': 'image/jpeg,image/png,image/webp',
                         'Accept-Encoding': 'identity'}) as session:
            async with session.get(url, allow_redirects=False) as response:
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
    except ArticleError:
        raise
    except (aiohttp.ClientError, TimeoutError, OSError, ValueError):
        raise ArticleError('Не удалось безопасно получить изображение') from None
    finally:
        await resolver.close()

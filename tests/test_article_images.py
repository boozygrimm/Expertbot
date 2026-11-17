"""Public article image discovery, bounded downloads and owner review path."""
import unittest
from unittest.mock import AsyncMock, patch
from mongomock_motor import AsyncMongoMockClient
from news_prototype import article_fetch, news_image_fetch, news_image_codec, owner_drafts
from tests.test_news_images import picture_bytes
from tests.test_owner_drafts import SOURCE, DRAFT


class Content:
    def __init__(self, data): self.data = data
    async def iter_chunked(self, size):
        for start in range(0, len(self.data), size):
            yield self.data[start:start + size]


class Response:
    def __init__(self, body=b'', status=200, mime='text/html', headers=None):
        self.status, self.content_type, self.headers = status, mime, headers or {}
        self.content = Content(body)
        self.content_length = len(body)
        self.charset = 'utf-8'
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False


class Session:
    def __init__(self, responses): self.responses, self.calls = list(responses), []
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    def get(self, url, allow_redirects):
        self.calls.append((url, allow_redirects))
        return self.responses.pop(0)


def html(image):
    return (f'<html><head><meta property="og:image" content="{image}"></head>'
            f'<body><article><p>{SOURCE}</p></article></body></html>').encode()


class ArticleImagesTests(unittest.IsolatedAsyncioTestCase):
    async def test_article_metadata_to_validated_thumbnail_and_draft(self):
        # Both network boundaries are faked; never fetch a real image or call Gemini.
        session = Session([Response(html('/photos/cover.png'))])
        with patch.object(article_fetch.aiohttp, 'ClientSession', return_value=session):
            article = await article_fetch.fetch_article('https://example.com/news/story')
        self.assertEqual(article['media'], {'kind': 'article_image', 'url': 'https://example.com/photos/cover.png'})
        image_session = Session([Response(picture_bytes(), mime='image/png')])
        with patch.object(news_image_fetch.aiohttp, 'ClientSession', return_value=image_session):
            db = AsyncMongoMockClient().test
            await owner_drafts.create(db, 123, 10, 'https://example.com/news/story', '')
            await owner_drafts.process_new(db, 123, 10, AsyncMock(return_value=article),
                AsyncMock(return_value=DRAFT), news_image_fetch.fetch_thumbnail)
        item = await owner_drafts.get(db, 123, 10)
        self.assertEqual(item['stage'], 'draft_ready')
        self.assertEqual(item['image_kind'], 'article_image')
        self.assertTrue(news_image_codec.decode_thumbnail(item['script']['image_b64']))
        self.assertEqual(image_session.calls, [('https://example.com/photos/cover.png', False)])
        self.assertEqual(await db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_unsafe_or_absent_metadata_does_not_break_article(self):
        for image in ('http://example.com/a.jpg', 'https://127.0.0.1/private',
                      '//user:password@example.com/a', 'data:image/jpeg;base64,AAAA'):
            with self.subTest(image=image):
                session = Session([Response(html(image))])
                with patch.object(article_fetch.aiohttp, 'ClientSession', return_value=session):
                    article = await article_fetch.fetch_article('https://example.com/news')
                self.assertNotIn('media', article)
                self.assertIn('summary', article)

    async def test_safe_redirects_and_block_private_redirect(self):
        first = Response(status=302, headers={'Location': 'https://cdn.example.net/image.png'})
        session = Session([first, Response(picture_bytes(), mime='image/png')])
        with patch.object(news_image_fetch.aiohttp, 'ClientSession', return_value=session):
            result = await news_image_fetch.fetch_thumbnail('https://example.com/image', article=True)
        self.assertTrue(news_image_codec.decode_thumbnail(result))
        self.assertEqual(session.calls, [('https://example.com/image', False), ('https://cdn.example.net/image.png', False)])
        for url in ('https://127.0.0.1/private', 'http://example.com/image', 'https://[::1]/'):
            session = Session([Response(status=302, headers={'Location': url})])
            with patch.object(news_image_fetch.aiohttp, 'ClientSession', return_value=session):
                with self.assertRaises(article_fetch.ArticleError):
                    await news_image_fetch.fetch_thumbnail('https://example.com/image', article=True)
            self.assertEqual(len(session.calls), 1)

    async def test_mime_limits_and_redirect_budget(self):
        responses = [Response(b'not-image', mime='text/html'),
                     Response(b'X' * (news_image_fetch.MAX_INPUT_BYTES + 1), mime='image/jpeg'),
                     Response(b'not a jpeg', mime='image/jpeg')]
        for response in responses:
            with self.subTest(mime=response.content_type, size=response.content_length):
                with patch.object(news_image_fetch.aiohttp, 'ClientSession', return_value=Session([response])):
                    with self.assertRaises(article_fetch.ArticleError):
                        await news_image_fetch.fetch_thumbnail('https://example.com/a', article=True)
        session = Session([Response(status=302, headers={'Location': '/next'}) for _ in range(4)])
        with patch.object(news_image_fetch.aiohttp, 'ClientSession', return_value=session):
            with self.assertRaises(article_fetch.ArticleError):
                await news_image_fetch.fetch_thumbnail('https://example.com/a', article=True)
        self.assertEqual(len(session.calls), 4)

    async def test_dns_answers_must_all_be_public(self):
        resolver = article_fetch.PublicResolver()
        try:
            resolver.delegate.resolve = AsyncMock(return_value=[{'host': '8.8.8.8'}, {'host': '10.1.2.3'}])
            with self.assertRaises(OSError):
                await resolver.resolve('images.example.com', 443)
        finally:
            await resolver.close()


if __name__ == '__main__': unittest.main()

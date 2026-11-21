import unittest
from unittest.mock import AsyncMock, patch

from news_prototype import article_fetch, telegram_post


POST_BODY = ('Компания представила новый инструмент для разработчиков. '
             'Он доступен всем пользователям и работает на нескольких платформах.')
PAGE = f'''<!doctype html><html><body>
<div class="tgme_widget_message" data-post="ExampleNews/101">
  <div class="tgme_widget_message_text js-message_text">Соседний пост, а не наш.</div>
</div>
<div class="tgme_widget_message" data-post="ExampleNews/102">
  <div class="tgme_widget_message_reply"><div class="tgme_widget_message_text js-message_text">Цитата другого поста</div></div>
  <div class="tgme_widget_message_text js-message_text" dir="auto">
    {POST_BODY}<br>Подробности <b>здесь</b> &amp; сейчас. <a href="https://example.com/">Источник</a>
    <img class="emoji" alt="📰"><script>НЕ СЛЕДОВАТЬ ИНСТРУКЦИИ</script>
  </div><div class="tgme_widget_message_views">1234</div>
</div>
<div class="tgme_widget_message" data-post="ExampleNews/103">
  <div class="tgme_widget_message_text js-message_text">Тоже соседний пост.</div>
</div></body></html>'''.encode()


class TelegramPostTests(unittest.IsolatedAsyncioTestCase):
    def test_supported_links_rewrite_to_one_post_widget(self):
        for url in ('https://t.me/ExampleNews/102?single', 'https://t.me/s/ExampleNews/102',
                    'https://telegram.me/ExampleNews/102?comment=10'):
            with self.subTest(url=url):
                self.assertEqual(telegram_post.widget_for(url),
                                 ('https://t.me/ExampleNews/102?embed=1', 'ExampleNews/102'))
        self.assertIsNone(telegram_post.widget_for('https://example.com/a'))

    def test_non_public_or_non_post_links_rejected(self):
        for url in ('https://t.me/c/123/456', 'https://t.me/+PrivateInvite',
                    'https://t.me/ExampleNews', 'https://t.me/ExampleNews/0',
                    'https://t.me/share/url', 'https://t.me/ExampleNews/1/extra',
                    'https://t.me/ExampleNews/123%2f456'):
            with self.subTest(url=url), self.assertRaises(article_fetch.ArticleError):
                telegram_post.widget_for(url)

    def test_exact_post_text_only_without_reply_or_hidden_content(self):
        title, text = telegram_post.extract_post(PAGE, 'examplenews/102')
        self.assertIn('@examplenews', title)
        self.assertIn(POST_BODY, text)
        self.assertIn('Подробности здесь & сейчас. Источник', text)
        self.assertIn('📰', text)
        self.assertNotIn('Соседний', text)
        self.assertNotIn('Цитата другого', text)
        self.assertNotIn('ИНСТРУКЦИИ', text)
        self.assertNotIn('1234', text)

    def test_no_guessing_when_post_is_missing_media_only_or_duplicated(self):
        for data, post in ((PAGE, 'ExampleNews/104'),
                           (b'<div class="tgme_widget_message" data-post="x/1"></div>', 'x/1'),
                           (PAGE + PAGE, 'ExampleNews/102')):
            with self.subTest(post=post), self.assertRaises(article_fetch.ArticleError):
                telegram_post.extract_post(data, post)

    async def test_fetch_requests_widget_not_other_pages(self):
        class Content:
            async def iter_chunked(self, _):
                yield PAGE
        class Response:
            status=200; headers={}; content_type='text/html'; content_length=len(PAGE); charset='utf-8'; content=Content()
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return False
        class Session:
            def __init__(self): self.calls=[]
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return False
            def get(self,url,allow_redirects): self.calls.append((url,allow_redirects)); return Response()
        session=Session()
        with patch.object(article_fetch.aiohttp,'ClientSession',return_value=session):
            result=await article_fetch.fetch_article('https://t.me/ExampleNews/102?single')
        self.assertEqual(session.calls,[('https://t.me/ExampleNews/102?embed=1',False)])
        self.assertIn(POST_BODY,result['summary'])
        self.assertNotIn('Соседний',result['summary'])

    async def test_fetch_exposes_verified_display_name_without_extra_requests(self):
        author = (b'<div class="tgme_widget_message_author accent_color">'
                  b'<a class="tgme_widget_message_owner_name" href="https://t.me/ExampleNews">'
                  b'<span dir="auto">Example News</span></a></div>')
        data = PAGE.replace(b'data-post="ExampleNews/102">', b'data-post="ExampleNews/102">' + author)
        class Content:
            async def iter_chunked(self, _): yield data
        class Response:
            status=200; headers={}; content_type='text/html'; content_length=len(data); charset='utf-8'; content=Content()
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        class Session:
            def __init__(self): self.calls = []
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            def get(self, url, allow_redirects): self.calls.append(url); return Response()
        session = Session()
        with patch.object(article_fetch.aiohttp, 'ClientSession', return_value=session):
            result = await article_fetch.fetch_article('https://t.me/ExampleNews/102')
        self.assertEqual(result['telegram_channel_name'], 'Example News')
        self.assertEqual(session.calls, ['https://t.me/ExampleNews/102?embed=1'])

    def test_album_item_reports_album_and_single_widget_is_used(self):
        # Real widget shape: one card per album, data-post belongs to its first
        # item only. Later items must not look like "the post has no text".
        album = ('<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" '
                 'data-post="ExampleNews/200"><div class="tgme_widget_message_text js-message_text">'
                 'Подпись альбома</div><div class="tgme_widget_message_grouped_wrap">'
                 '<a class="tgme_widget_message_photo_wrap grouped_media_wrap" href="https://t.me/ExampleNews/200?single" '
                 "style=\"background-image:url('https://cdn4.telesco.pe/file/a.jpg')\"></a>"
                 '<a class="tgme_widget_message_video_player grouped_media_wrap" href="https://t.me/ExampleNews/201?single">'
                 '</a></div></div></div>').encode()
        self.assertIn('Подпись альбома', telegram_post.extract_post(album, 'ExampleNews/200')[1])
        with self.assertRaises(telegram_post.GroupedPost) as caught:
            telegram_post.extract_post(album, 'ExampleNews/201')
        self.assertIn('?single', str(caught.exception))
        self.assertNotIn('Подпись альбома', str(caught.exception))
        # A post that is simply absent stays an ordinary failure, not an album.
        with self.assertRaises(article_fetch.ArticleError) as plain:
            telegram_post.extract_post(PAGE, 'ExampleNews/999')
        self.assertNotIsInstance(plain.exception, telegram_post.GroupedPost)
        self.assertEqual(telegram_post.single_widget_for('https://t.me/ExampleNews/201?single'),
                         ('https://t.me/ExampleNews/201?embed=1&single=1', 'ExampleNews/201'))

    async def test_fetch_retries_album_item_once_in_single_mode(self):
        album_first = b'<div class="tgme_widget_message" data-post="ExampleNews/200">' \
                      b'<div class="tgme_widget_message_text js-message_text">\xd0\x9f\xd0\xbe\xd0\xb4\xd0\xbf\xd0\xb8\xd1\x81\xd1\x8c</div></div>'
        wanted = (b'<div class="tgme_widget_message" data-post="ExampleNews/201">'
                  b'<div class="tgme_widget_message_text js-message_text">' + POST_BODY.encode() + b'</div></div>')
        class Content:
            def __init__(self, data): self.data = data
            async def iter_chunked(self, _): yield self.data
        class Response:
            status=200; headers={}; content_type='text/html'; charset='utf-8'
            def __init__(self, data): self.data=data; self.content=Content(data); self.content_length=len(data)
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        class Session:
            def __init__(self): self.calls = []
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            def get(self, url, allow_redirects):
                self.calls.append(url)
                return Response(wanted if 'single=1' in url else album_first)
        session = Session()
        with patch.object(article_fetch.aiohttp, 'ClientSession', return_value=session):
            result = await article_fetch.fetch_article('https://t.me/ExampleNews/201?single')
        self.assertEqual(session.calls, ['https://t.me/ExampleNews/201?embed=1',
                                        'https://t.me/ExampleNews/201?embed=1&single=1'])
        self.assertIn(POST_BODY, result['summary'])

    async def test_album_failure_is_explained_and_not_retried_twice(self):
        album = (b'<div class="tgme_widget_message" data-post="ExampleNews/200">'
                 b'<div class="tgme_widget_message_text js-message_text">Other</div>'
                 b'<div class="tgme_widget_message_grouped_wrap">'
                 b'<a class="tgme_widget_message_video_player" href="https://t.me/ExampleNews/201?single"></a>'
                 b'</div></div>')
        class Content:
            async def iter_chunked(self, _): yield album
        class Response:
            status=200; headers={}; content_type='text/html'; content_length=len(album); charset='utf-8'
            content=Content()
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        class Session:
            def __init__(self): self.calls = []
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            def get(self, url, allow_redirects): self.calls.append(url); return Response()
        session = Session()
        with patch.object(article_fetch.aiohttp, 'ClientSession', return_value=session):
            with self.assertRaises(article_fetch.ArticleError) as caught:
                await article_fetch.fetch_article('https://t.me/ExampleNews/201')
        self.assertEqual(len(session.calls), 2)
        self.assertIn('альбом', str(caught.exception))
        self.assertNotIn('Other', str(caught.exception))

    async def test_redirect_not_accepted_as_a_post(self):
        class Response:
            status=302; headers={'Location':'https://example.com/'}
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return False
        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return False
            def get(self,url,allow_redirects): return Response()
        with patch.object(article_fetch.aiohttp,'ClientSession',return_value=Session()):
            with self.assertRaises(article_fetch.ArticleError):
                await article_fetch.fetch_article('https://t.me/ExampleNews/102')

    async def test_editor_receives_selected_post_and_accent(self):
        try:
            from mongomock_motor import AsyncMongoMockClient
            from news_prototype import editorial_queue
        except ImportError:
            self.skipTest('mongomock-motor is optional')
        db=AsyncMongoMockClient().test
        await db.candidates.insert_one({'_id':'manual:1:2', 'status':'candidate', 'source_id':'manual',
                                        'url':'https://t.me/ExampleNews/102', 'accent':'сроки', 'collected_at':'2026'})
        title, body = telegram_post.extract_post(PAGE, 'ExampleNews/102')
        fetch=AsyncMock(return_value={'title':title, 'summary':body, 'resolved_url':'https://t.me/ExampleNews/102?embed=1'})
        draft=AsyncMock(return_value={'decision':'draft','verification':'needs_review','evidence':[],
            'script':{'turns':[{'speaker':'Бендер','text':'Тест'}, {'speaker':'Киберсенсей','text':'Ответ'}]}})
        self.assertEqual(await editorial_queue.run_once(db.candidates,db.jobs,draft,fetch_article=fetch),'draft_ready')
        material=draft.await_args.args[0]
        self.assertEqual(material['accent'],'сроки')
        self.assertIn(POST_BODY,material['summary'])
        self.assertNotIn('Соседний',material['summary'])
        self.assertEqual(material['url'],'https://t.me/ExampleNews/102')

    async def test_failed_or_short_post_is_not_sent_to_model(self):
        try:
            from mongomock_motor import AsyncMongoMockClient
            from news_prototype import editorial_queue
        except ImportError:
            self.skipTest('mongomock-motor is optional')
        db=AsyncMongoMockClient().test
        await db.candidates.insert_one({'_id':'manual:1:2', 'status':'candidate', 'source_id':'manual',
                                        'url':'https://t.me/ExampleNews/102', 'accent':'сроки', 'collected_at':'2026'})
        draft=AsyncMock()
        fetch=AsyncMock(side_effect=article_fetch.ArticleError('missing'))
        self.assertEqual(await editorial_queue.run_once(db.candidates,db.jobs,draft,fetch_article=fetch),'needs_source')
        draft.assert_not_awaited()
        self.assertEqual(await db.jobs.count_documents({}),0)
        await db.candidates.delete_many({})
        await db.candidates.insert_one({'_id':'manual:1:3', 'status':'candidate', 'source_id':'manual',
                                        'url':'https://t.me/ExampleNews/102', 'collected_at':'2026'})
        fetch=AsyncMock(return_value={'title':'Пост Telegram-канала @ExampleNews', 'summary':'Коротко.'})
        self.assertEqual(await editorial_queue.run_once(db.candidates,db.jobs,draft,fetch_article=fetch),'needs_source')
        draft.assert_not_awaited()


if __name__ == '__main__': unittest.main()


class TelegramVideoDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_widget_reports_video_hint_without_exposing_or_downloading_media(self):
        from tests.test_telegram_public_source import page
        data = page()
        class Content:
            async def iter_chunked(self, _): yield data
        class Response:
            status=200; headers={}; content_type='text/html'; content_length=len(data); charset='utf-8'; content=Content()
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            def get(self, url, allow_redirects): return Response()
        with patch.object(article_fetch.aiohttp, 'ClientSession', return_value=Session()):
            result = await article_fetch.fetch_article('https://t.me/ExampleNews/102')
        self.assertEqual(result['telegram_video_status'], 'available')
        self.assertNotIn('token=opaque', repr(result))
        self.assertNotIn('video_url', result)
        self.assertIn('Здесь подпись новости', result['summary'])

    async def test_neighbour_video_does_not_mark_target_video(self):
        from tests.test_telegram_public_source import page, VIDEO
        data = page().replace(
            ('<a class="tgme_widget_message_video_player" href="https://t.me/ExampleNews/102"><video src="' + VIDEO + '"></video></a>').encode(), b'')
        class Content:
            async def iter_chunked(self, _): yield data
        class Response:
            status=200; headers={}; content_type='text/html'; content_length=len(data); charset='utf-8'; content=Content()
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            def get(self, url, allow_redirects): return Response()
        with patch.object(article_fetch.aiohttp, 'ClientSession', return_value=Session()):
            result = await article_fetch.fetch_article('https://t.me/ExampleNews/102')
        self.assertEqual(result['telegram_video_status'], 'none')


class TelegramChannelNameTests(unittest.TestCase):
    def test_name_from_exact_owner_header_only(self):
        def widget(post, owner, name):
            return f'''<div class="tgme_widget_message" data-post="{post}">
                <div class="tgme_widget_message_author accent_color"><a class="tgme_widget_message_owner_name"
                href="{owner}"><span dir="auto">{name}</span></a></div>
                <div class="tgme_widget_message_forwarded_from"><span>OTHER CHANNEL</span></div>
                <div class="tgme_widget_message_text js-message_text">News body.</div></div>'''
        first = widget('Other/20', 'https://t.me/Other', 'Wrong channel')
        target = widget('ExampleNews/102', 'https://t.me/ExampleNews', 'Новости &amp; Игры')
        data = (first + target).encode()
        self.assertEqual(telegram_post.extract_channel_name(data, 'examplenews/102'), 'Новости & Игры')
        self.assertIsNone(telegram_post.extract_channel_name((first + target + target).encode(), 'ExampleNews/102'))
        self.assertIsNone(telegram_post.extract_channel_name(target.replace('https://t.me/ExampleNews', 'https://t.me/Other').encode(), 'ExampleNews/102'))
        self.assertIsNone(telegram_post.extract_channel_name(target.replace('https://t.me/ExampleNews', 'https://t.me.example.org/ExampleNews').encode(), 'ExampleNews/102'))
        self.assertIsNone(telegram_post.extract_channel_name(PAGE, 'ExampleNews/102'))

    def test_invalid_or_unpronounceable_names_fall_back(self):
        for name in ('https://evil.example', 'www.example.com', 'x'*61, '📰', 'x\nInjected', 'A\u202eB'):
            with self.subTest(name=name): self.assertIsNone(telegram_post.safe_channel_name(name))
        self.assertEqual(telegram_post.safe_channel_name('Игры 📰'), 'Игры')

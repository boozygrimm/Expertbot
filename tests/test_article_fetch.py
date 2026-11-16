import unittest
from unittest.mock import AsyncMock, patch
from news_prototype import article_fetch as article


class ArticleFetchTests(unittest.IsolatedAsyncioTestCase):
    def test_url_policy(self):
        bad = ['http://example.com/', 'https://localhost/', 'https://site.local/',
               'https://127.0.0.1/', 'https://127.1/', 'https://127.000.0.1/',
               'https://0127.0.0.1/', 'https://[::1]/', 'https://u:p@example.com/',
               'https://@example.com/', 'https://example.com:8443/', 'file:///etc/passwd',
               'https://example.com\\@127.0.0.1/', 'https://example.com/\nheader']
        for url in bad:
            with self.subTest(url=url), self.assertRaises(article.ArticleError): article.validate_url(url)
        self.assertEqual(article.validate_url('https://Example.COM:443/a?x=1#fragment'), 'https://example.com/a?x=1')

    def test_global_ip_validation(self):
        for ip in ('127.0.0.1','10.2.3.4','172.16.0.1','169.254.169.254','::1','fc00::1',
                   '::ffff:127.0.0.1','100.64.0.1','224.0.0.1'):
            self.assertFalse(article.is_public_ip(ip),ip)
        self.assertTrue(article.is_public_ip('8.8.8.8'))

    async def test_dns_answers_are_all_checked_and_pinned(self):
        resolver = article.PublicResolver()
        try:
            resolver.delegate.resolve = AsyncMock(return_value=[{'host':'8.8.8.8'}, {'host':'127.0.0.1'}])
            with self.assertRaises(OSError): await resolver.resolve('example.com',443)
            resolver.delegate.resolve.return_value=[{'host':'8.8.8.8'}]
            self.assertEqual((await resolver.resolve('example.com',443))[0]['host'],'8.8.8.8')
        finally:
            await resolver.close()

    def test_extract_excludes_instructions_hidden_in_script_and_prefers_article(self):
        html = ('<html><head><title>Новость</title></head><body>'
                '<p>Реклама и навигация, не относится к статье и не должна быть в результате.</p>'
                '<article><script>секретная инструкция</script><p>' + 'Реальный абзац новости. ' * 8 + '</p></article>'
                '</body></html>').encode()
        title, body = article.extract_article(html)
        self.assertEqual(title,'Новость'); self.assertIn('Реальный абзац',body)
        self.assertNotIn('инструкция',body); self.assertNotIn('Реклама',body)
        with self.assertRaises(article.ArticleError): article.extract_article(b'<html><p>tiny</p></html>')

    async def test_redirect_to_local_address_never_requested(self):
        class Response:
            status=302; headers={'Location':'https://127.0.0.1/private'}
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return False
        class Session:
            def __init__(self): self.calls=[]
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return False
            def get(self,url,allow_redirects): self.calls.append((url,allow_redirects)); return Response()
        session=Session()
        with patch.object(article.aiohttp,'ClientSession',return_value=session):
            with self.assertRaises(article.ArticleError): await article.fetch_article('https://example.com/story')
        self.assertEqual(session.calls,[('https://example.com/story',False)])


if __name__ == '__main__': unittest.main()

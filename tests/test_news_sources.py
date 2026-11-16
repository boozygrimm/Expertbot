from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
from news_prototype import news_sources as feeds

NOW = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=1)
RSS = b'''<?xml version="1.0"?><rss><channel>
<item><title> New &amp; useful </title><link>https://example.com/a#top</link><pubDate>Fri, 02 Jan 2026 09:00:00 +0000</pubDate><description>&lt;b&gt;Fact&lt;/b&gt; &lt;script&gt;bad&lt;/script&gt;</description></item>
<item><title>Duplicate</title><link>https://example.com/a</link><pubDate>Fri, 02 Jan 2026 09:00:00 +0000</pubDate></item>
<item><title>Old</title><link>https://example.com/old</link><pubDate>Mon, 01 Dec 2025 09:00:00 +0000</pubDate></item>
<item><title>Unknown</title><link>https://example.com/b</link></item>
<item><title>Bad url</title><link>javascript:alert</link><pubDate>Fri, 02 Jan 2026 09:00:00 +0000</pubDate></item>
</channel></rss>'''
ATOM = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Title</title><link href="https://example.com/atom"/><published>2026-01-02T11:00:00Z</published><summary>Data</summary></entry></feed>'''


class NewsSourcesTests(unittest.TestCase):
    def test_rss_is_bounded_dated_deduplicated_and_text_only(self):
        items = feeds.parse_feed(RSS, 'openai', since=SINCE, now=NOW)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['title'], 'New & useful')
        self.assertEqual(items[0]['summary'], 'Fact')
        self.assertEqual(items[0]['url'], 'https://example.com/a')
        self.assertEqual(items[0]['status'], 'candidate')

    def test_atom(self):
        self.assertEqual(feeds.parse_feed(ATOM, 'deepmind', since=SINCE, now=NOW)[0]['url'], 'https://example.com/atom')

    def test_rejects_doctype_oversized_and_malformed(self):
        for data in (b'<!DOCTYPE rss [<!ENTITY e "hello">]><rss/>', b'x' * (feeds.MAX_BYTES + 1), b'<rss>'):
            with self.assertRaises(feeds.FeedError):
                feeds.parse_feed(data, 'openai', since=SINCE, now=NOW)

    def test_too_many_items_fail_instead_of_silent_truncation(self):
        data = b'<rss><channel>' + b'<item/>' * (feeds.MAX_ITEMS + 1) + b'</channel></rss>'
        with self.assertRaises(feeds.FeedError):
            feeds.parse_feed(data, 'openai', since=SINCE, now=NOW)

    def test_missing_timezone_and_future_dates_are_not_news(self):
        self.assertIsNone(feeds.parse_date('2026-01-02T11:00:00'))
        data = ATOM.replace(b'11:00:00Z', b'13:00:00Z')
        self.assertEqual(feeds.parse_feed(data, 'openai', since=SINCE, now=NOW), [])

    def test_fetch_rejects_unknown_feed_without_network(self):
        with patch('urllib.request.build_opener') as opener:
            with self.assertRaises(feeds.FeedError):
                feeds.fetch_feed('http://localhost', since=SINCE, now=NOW)
            opener.assert_not_called()

    def test_urls_are_never_automatically_fetched(self):
        for value in ('file:///etc/passwd', 'https://u:p@example.com/a', 'https://localhost/a', 'https://x/a\n'):
            self.assertIsNone(feeds.safe_link(value))
        self.assertEqual(feeds.safe_link('https://example.org/p#frag'), 'https://example.org/p')


if __name__ == '__main__': unittest.main()

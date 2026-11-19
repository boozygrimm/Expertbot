"""Safe spoken attribution for the general-news workflow.

Uses the actual post identity or resolved article host, not an untrusted feed
name, extracted content, or a model-generated claim of original authorship.
"""
from urllib.parse import urlsplit

from news_prototype import article_fetch
from news_prototype.telegram_post import channel_opening, widget_for


def opening(item: dict) -> str | None:
    if not item.get('general_candidate_id'):
        return None
    try:
        url = article_fetch.validate_url(item.get('resolved_url') or item['url'])
        widget = widget_for(url)
        if widget:
            existing = channel_opening(url, item.get('telegram_channel_name'))
            if existing:
                return existing
            # widget_for already strictly validates the channel and exact post.
            channel = widget[1].split('/')[0]
            return f'В телеграм-канале «{channel}» поделились новостью.'
        host = urlsplit(url).hostname or ''
        if host.startswith('www.'):
            host = host[4:]
        if not host or len(host) > 120:
            return 'Информация опубликована на сайте.'
        return f'На сайте «{host}» поделились новостью.'
    except (article_fetch.ArticleError, KeyError, TypeError, ValueError):
        # An invalid resolved address must not turn into an invented name.
        return None

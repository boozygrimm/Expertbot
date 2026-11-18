from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops

from news_prototype import bulletin_fallback, bulletin_wide_screen
from video_prototype import news_jobs


def test_cards_are_distinct_deterministic_and_copies():
    cards = [bulletin_fallback.card(i) for i in range(len(bulletin_fallback.CARDS))]
    assert all(c.size == bulletin_fallback.SIZE for c in cards)
    assert all(ImageChops.difference(cards[0], c).getbbox() for c in cards[1:])
    assert bulletin_fallback.card(4).tobytes() == cards[0].tobytes()
    cards[0].paste('red', (0, 0, 100, 100))
    assert bulletin_fallback.card(0).tobytes() == bulletin_fallback.card(4).tobytes()


def test_fallback_only_when_requested_and_no_source_image():
    from tests.test_bulletin_pipeline import payload
    transparent = (Image.new('RGBA', bulletin_wide_screen.SIZE),) * 2
    compose = bulletin_wide_screen.compose
    plain = compose(speaker_layers=transparent)
    fallback = compose(speaker_layers=transparent, fallback_position=0)
    assert ImageChops.difference(plain, fallback).getbbox()
    image = payload()['sections'][0]['image_b64']
    assert compose(image, speaker_layers=transparent).tobytes() == compose(
        image, speaker_layers=transparent, fallback_position=0).tobytes()


def test_worker_capability_requires_card_art():
    assert news_jobs.bulletin_assets_ready()
    with patch.object(bulletin_fallback, 'ART', Path('/nonexistent/bulletin-card.png')):
        assert not news_jobs.bulletin_assets_ready()

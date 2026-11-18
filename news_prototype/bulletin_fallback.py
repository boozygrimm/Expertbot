"""Clearly fictional studio cards for stories without approved imagery.

Deterministic by story position, so the owner previews the same card the
worker renders. No network, provider, or automatically chosen factual image.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / 'design' / 'bulletin_fallback_hedgehog.png'
FONT = ROOT / 'video_prototype' / 'assets' / 'sign-font.ttf'
SIZE = (456, 213)
CARDS = (
    ('ПРОДАМ', 'ГАРАЖ'),
    ('ЁЖ', 'НА СВЯЗИ'),
    ('КАТАНА', 'НА ГОТОВЕ'),
    ('ШТАБ', 'ЁЖНЫХ НОВОСТЕЙ'),
)


@lru_cache(maxsize=len(CARDS))
def _card(variant: int) -> Image.Image:
    with Image.open(ART) as source:
        image = ImageOps.fit(source.convert('RGB'), SIZE, Image.Resampling.LANCZOS)
    # Darken the text area but leave the hedgehog unobscured.
    overlay = Image.new('RGBA', SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle((10, 18, 254, 193), radius=12, fill=(5, 17, 30, 196),
                           outline=(50, 197, 202, 180), width=2)
    label = ImageFont.truetype(str(FONT), 13)
    draw.text((22, 27), 'СТУДИЙНАЯ ШУТКА', font=label, fill=(128, 234, 231, 255))
    lines = CARDS[variant]
    for index, line in enumerate(lines):
        size = 37
        while size > 15:
            font = ImageFont.truetype(str(FONT), size)
            if draw.textbbox((0, 0), line, font=font)[2] <= 222:
                break
            size -= 1
        draw.text((22, 65 + 53 * index), line, font=font,
                  fill=(255, 242, 211, 255), stroke_width=1, stroke_fill=(3, 18, 29, 255))
    return Image.alpha_composite(image.convert('RGBA'), overlay).convert('RGB')


def card(position: int) -> Image.Image:
    if type(position) is not int or position < 0:
        raise ValueError('Неверный номер заглушки')
    return _card(position % len(CARDS)).copy()


"""Prototype a slow amber marquee following the angle of the studio desk.

This is an overlay on a flat image, independent of the production sign renderer.
The text is intentionally illustrative, not an actual news headline.
"""
from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from news_prototype import bender_trial as bender

FONT = Path(__file__).resolve().parent.parent / 'video_prototype/assets/sign-font.ttf'
TEXT = 'СТУДИЯ ЗАПУЩЕНА  -  ЁЖ НА СТРАЖЕ ФАКТОВ  -  БЕНДЕР НА СТРАЖЕ ВЕЛИКОЛЕПИЯ  -  '
WIDTH, HEIGHT, SPLIT = 425, 22, 198
# Six source pixels lower than the first trial (about four at output size),
# keeping the centre corner below the top edge of the desk.
LEFT, TOP = 475, 566
SPEED = 32  # native pixels per second
GAP = 75


def prepare(text: str = TEXT) -> tuple[Image.Image, int]:
    if not text or len(text) > 200:
        raise ValueError('Нужен короткий текст бегущей строки')
    font = ImageFont.truetype(str(FONT), 17)
    measure = ImageDraw.Draw(Image.new('L', (1, 1)))
    box = measure.textbbox((0, 0), text, font=font, stroke_width=0)
    width = max(1, box[2] - min(0, box[0]))
    strip = Image.new('RGBA', (width + 4, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(strip)
    y = (HEIGHT - (box[3] - box[1])) // 2 - box[1]
    draw.text((2, y), text, font=font, fill=(255, 205, 109, 255))
    return strip, strip.width + GAP


def offset_at(t: float, period: int) -> int:
    if period <= 0:
        raise ValueError('Неверная длина строки')
    return round(max(0.0, t) * SPEED) % period


def overlay(assets: tuple[Image.Image, int], t: float) -> tuple[Image.Image, tuple[int, int]]:
    text, period = assets
    tile = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    d.rounded_rectangle((0, 0, WIDTH-1, HEIGHT-1), radius=3,
                        fill=(12, 17, 28, 245), outline=(119, 100, 68, 255), width=1)
    d.line((4, 2, WIDTH-5, 2), fill=(181, 142, 72, 130), width=1)
    # Confine the moving glyphs to the inner window, then warp the two halves
    # separately so the strip follows the shallow V of the desk's front.
    clip = Image.new('RGBA', (WIDTH-10, HEIGHT-4), (0, 0, 0, 0))
    x = 7 - offset_at(t, period)
    while x > 0:
        x -= period
    while x < clip.width:
        clip.alpha_composite(text, (x, -2))
        x += period
    tile.alpha_composite(clip, (5, 2))
    warped = Image.new('RGBA', (WIDTH, HEIGHT + 12), (0, 0, 0, 0))
    for x0, x1, start, end in ((0, SPLIT, 10, 4), (SPLIT, WIDTH, 4, 10)):
        part = tile.crop((x0, 0, x1, HEIGHT))
        slope = (end - start) / (x1 - x0)
        moved = part.transform((x1-x0, HEIGHT+12), Image.Transform.AFFINE,
                               (1, 0, 0, -slope, 1, -start),
                               resample=Image.Resampling.BICUBIC)
        warped.alpha_composite(moved, (x0, 0))
    sx, sy = bender.SIZE[0] / bender.REF_SIZE[0], bender.SIZE[1] / bender.REF_SIZE[1]
    resized = warped.resize((round(WIDTH*sx), round((HEIGHT+12)*sy)), Image.Resampling.LANCZOS)
    return resized, (round(LEFT*sx), round(TOP*sy))


def apply(frame: Image.Image, assets: tuple[Image.Image, int], t: float) -> Image.Image:
    layer, position = overlay(assets, t)
    result = frame.convert('RGBA')
    result.alpha_composite(layer, position)
    return result.convert('RGB')

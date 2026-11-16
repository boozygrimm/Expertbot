"""Small, local animation of the existing framed hedgehog; no new character art."""
from __future__ import annotations

import math
from PIL import Image, ImageDraw, ImageFilter
from news_prototype import bender_trial as bender

HERE = bender.HERE


def prepare() -> tuple[Image.Image, Image.Image, Image.Image]:
    with Image.open(HERE / 'studio_mockup_soft.png') as source:
        base = source.convert('RGBA')
    if base.size != bender.REF_SIZE:
        raise ValueError('Неверный размер макета студии')

    # Paint tiny closed eyelids on a copy, using adjacent face colours. Work at
    # 4x resolution to retain the soft, painted look of the original portrait.
    scale = 4
    left, top, right, bottom = 1016, 220, 1052, 237
    original = base.crop((left, top, right, bottom)).resize(((right-left)*scale, (bottom-top)*scale), Image.Resampling.LANCZOS)
    art = original.copy()
    eyes = Image.new('L', art.size, 0)
    mask = ImageDraw.Draw(eyes)
    draw = ImageDraw.Draw(art)
    for x, y, rx, ry, skin in [(1025, 229, 3.1, 4.0, (220, 190, 161)),
                                (1042, 229, 4.3, 4.5, (225, 195, 169))]:
        x, y = x - left, y - top
        box = tuple(round(v * scale) for v in (x-rx-1, y-ry-1, x+rx+1, y+ry+1))
        mask.ellipse(box, fill=255)
        draw.ellipse(box, fill=skin)
        draw.arc(tuple(round(v * scale) for v in (x-rx, y-1.0, x+rx, y+2.5)),
                 start=10, end=170, fill=(66, 42, 34, 255), width=4)
    eyes = eyes.filter(ImageFilter.GaussianBlur(2.5))
    closed = base.copy()
    closed.paste(Image.composite(art, original, eyes).resize((right-left, bottom-top), Image.Resampling.LANCZOS), (left, top))

    # Feathered head-only region, leaving the frame, sword and body stationary.
    head_mask = Image.new('L', bender.REF_SIZE, 0)
    ImageDraw.Draw(head_mask).ellipse((1011, 189, 1080, 250), fill=255)
    head_mask = head_mask.filter(ImageFilter.GaussianBlur(3))
    return base, closed, head_mask


def gesture(t: float, duration: float) -> tuple[float, float]:
    """One gentle nod with two short blinks, comfortably away from clip ends."""
    if duration < 3:
        return 0.0, 0.0
    start = min(2.0, duration * .2)
    elapsed = t - start
    if elapsed < 0 or elapsed > 2.3:
        return 0.0, 0.0
    # Smooth nod forward and back, no movement outside the portrait.
    nod = math.sin(math.pi * elapsed / 2.3) ** 2
    def blink(center: float) -> float:
        distance = abs(elapsed - center)
        return max(0.0, min(1.0, (0.16 - distance) / 0.06))
    return nod, max(blink(.55), blink(1.65))


def overlay(assets: tuple[Image.Image, Image.Image, Image.Image], nod: float, blink: float) -> Image.Image:
    base, closed, mask = assets
    nod = max(0.0, min(1.0, nod))
    blink = max(0.0, min(1.0, blink))
    if nod == 0 and blink == 0:
        return Image.new('RGBA', bender.SIZE, (0, 0, 0, 0))
    face = Image.blend(base, closed, blink)
    # Rotate around the neck, blending only a local feathered head region.
    face = face.rotate(-2.5 * nod, Image.Resampling.BICUBIC, center=(1041, 248))
    strength = min(1.0, max(nod, blink) / .08)
    face.putalpha(mask.point(lambda value: round(value * strength)))
    return face.resize(bender.SIZE, Image.Resampling.LANCZOS)

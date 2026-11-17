"""Isolated animation layer experiment for the selected bulletin concept.

AI references are clipped to small facial regions. The experimental bulletin
renderer uses amplitude cues, not phoneme lip-sync.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from news_prototype import bender_trial, bulletin_wide_screen

ROOT = Path(__file__).resolve().parent.parent / 'design' / 'animation'
SIZE = bulletin_wide_screen.SIZE
BENDER_MOUTH = (246, 254, 303, 287)
# Mouth placement in the *old* soft-teeth scene, measured after its standard
# 1365x768 -> 960x540 resize; map both states identically into the new pose.
OLD_BENDER_MOUTH = (280, 223, 349, 262)
BENDER_EYES = (248, 209, 325, 249)
SENSEI_MOUTH = (599, 253, 621, 269)
SENSEI_REFERENCE_BOX = (555, 190, 670, 335)


def _full_reference(filename: str) -> Image.Image:
    with Image.open(ROOT / filename) as image:
        return ImageOps.fit(image.convert('RGB'), SIZE, Image.Resampling.LANCZOS)


def _old_mouth_reference(filename: str) -> Image.Image:
    """Adapt an old-scene mouth region to the selected studio's smaller head."""
    old = bender_trial.load_image(Path(bender_trial.HERE) / filename)
    x0, y0, x1, y1 = BENDER_MOUTH
    ox0, oy0, ox1, oy1 = OLD_BENDER_MOUTH
    sx, sy = (ox1 - ox0) / (x1 - x0), (oy1 - oy0) / (y1 - y0)
    # Sample beyond the box as well: the feathered mask extends a few pixels
    # outside it and must not blend against an empty black image.
    return old.transform(SIZE, Image.Transform.AFFINE,
                         (sx, 0, ox0 - x0 * sx, 0, sy, oy0 - y0 * sy),
                         Image.Resampling.BICUBIC)


def _sensei_reference() -> Image.Image:
    x0, y0, x1, y1 = SENSEI_REFERENCE_BOX
    with Image.open(ROOT / 'sensei_talking_crop.png') as image:
        image = ImageOps.fit(image.convert('RGB'), (x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
    result = Image.new('RGB', SIZE)
    result.paste(image, (x0, y0))
    return result


def _replace_face(layer: Image.Image, reference: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    """Replace only a feathered face region, preserving the existing alpha."""
    if layer.mode != 'RGBA' or layer.size != SIZE or reference.size != SIZE:
        raise ValueError('Неверный слой анимации')
    mask = Image.new('L', SIZE, 0)
    ImageDraw.Draw(mask).ellipse(box, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(.8))
    rgb = Image.composite(reference.convert('RGB'), layer.convert('RGB'), mask)
    rgb.putalpha(layer.getchannel('A'))
    return rgb


def is_blink(t: float) -> bool:
    """Periodic short blink for the full bulletin, including long releases."""
    return t >= 1.8 and (t - 1.8) % 3.5 < .17


@lru_cache(maxsize=1)
def frames() -> dict[tuple[bool, bool, bool], tuple[Image.Image, Image.Image]]:
    """Keyed by Bender speaking, Bender blinking, and sensei speaking."""
    bender = bulletin_wide_screen._speaker_layer(bulletin_wide_screen.LAYERS[0])
    sensei = bulletin_wide_screen._speaker_layer(bulletin_wide_screen.LAYERS[1])
    closed = _old_mouth_reference('bender_soft_closed.png')
    opened = _old_mouth_reference('studio_mockup_soft.png')
    blink = _full_reference('bender_blink_reference.png')
    sensei_open = _sensei_reference()
    result = {}
    for speaking in (False, True):
        for blinking in (False, True):
            body = _replace_face(bender, opened if speaking else closed, BENDER_MOUTH)
            if blinking:
                body = _replace_face(body, blink, BENDER_EYES)
            for sensei_speaking in (False, True):
                head = _replace_face(sensei, sensei_open, SENSEI_MOUTH) if sensei_speaking else sensei.copy()
                result[(speaking, blinking, sensei_speaking)] = (body, head)
    return result

"""Experimental bulletin screen composited behind the flattened presenters.

The still is not layered artwork. The foreground is protected by a traced,
anti-aliased mask specific to the current studio asset. This module is isolated
from the production renderer until visuals and the complete workflow are tested.
"""
from __future__ import annotations

from io import BytesIO
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from news_prototype import bender_trial
from news_prototype.news_image_codec import decode_thumbnail

REFERENCE = (1365, 768)
# A screen mounted across the rear windows, behind both hosts.
SCREEN = (300, 120, 970, 427)
# Foreground silhouettes in the original 1365x768 studio artwork.
BENDER = [(244, 545), (243, 499), (258, 465), (279, 430), (320, 402),
          (335, 385), (374, 360), (383, 334), (383, 296), (382, 247),
          (385, 202), (394, 183), (409, 173), (430, 168), (437, 108),
          (449, 108), (455, 165), (477, 174), (495, 190), (503, 213),
          (506, 239), (525, 251), (532, 264), (530, 301), (518, 315),
          (507, 321), (509, 339), (541, 360), (564, 383), (590, 418),
          (616, 467), (627, 523), (628, 548)]
SENSEI = [(699, 548), (702, 497), (712, 459), (729, 423), (753, 393),
          (783, 375), (807, 365), (804, 347), (798, 333), (800, 310),
          (797, 293), (800, 274), (800, 250), (811, 230), (829, 217),
          (846, 210), (873, 210), (899, 221), (919, 235), (931, 257),
          (936, 280), (946, 289), (947, 318), (937, 328), (929, 346),
          (938, 364), (968, 372), (997, 385), (1020, 404), (1035, 435),
          (1051, 482), (1058, 524), (1054, 550)]


def _scaled_points(points, scale=3):
    sx = bender_trial.SIZE[0] * scale / REFERENCE[0]
    sy = bender_trial.SIZE[1] * scale / REFERENCE[1]
    return [(round(x * sx), round(y * sy)) for x, y in points]


@lru_cache(maxsize=1)
def masks() -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Return screen insertion mask and pixel box for the current studio size."""
    scale = 3
    size = (bender_trial.SIZE[0] * scale, bender_trial.SIZE[1] * scale)
    mask = Image.new('L', size, 0)
    draw = ImageDraw.Draw(mask)
    (x0, y0), (x1, y1) = _scaled_points([(SCREEN[0], SCREEN[1]), (SCREEN[2], SCREEN[3])], scale)
    draw.rounded_rectangle((x0, y0, x1, y1), radius=12 * scale, fill=255)
    # A few pixels of dilation keep changed background away from foreground edges.
    for polygon in (BENDER, SENSEI):
        points = _scaled_points(polygon, scale)
        draw.polygon(points, fill=0)
        draw.line(points + [points[0]], fill=0, width=5 * scale, joint='curve')
    mask = mask.filter(ImageFilter.GaussianBlur(scale * .3)).resize(bender_trial.SIZE, Image.Resampling.LANCZOS)
    sx, sy = bender_trial.SIZE[0] / REFERENCE[0], bender_trial.SIZE[1] / REFERENCE[1]
    box = (round(SCREEN[0] * sx), round(SCREEN[1] * sy), round(SCREEN[2] * sx), round(SCREEN[3] * sy))
    return mask, box


def prepare(image_b64: str) -> Image.Image:
    raw = decode_thumbnail(image_b64)
    _, box = masks()
    width, height = box[2] - box[0], box[3] - box[1]
    with Image.open(BytesIO(raw)) as image:
        image = image.convert('RGB')
        background = ImageOps.fit(image, (width, height), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(12))
        foreground = ImageOps.contain(image, (width - 12, height - 12), Image.Resampling.LANCZOS)
        background.paste(foreground, ((width - foreground.width) // 2, (height - foreground.height) // 2))
    ImageDraw.Draw(background).rounded_rectangle((0, 0, width - 1, height - 1), radius=8, outline=(110, 190, 205), width=3)
    panel = Image.new('RGB', bender_trial.SIZE)
    panel.paste(background, box[:2])
    return panel


def apply(frame: Image.Image, panel: Image.Image, opacity: float = 1.0) -> Image.Image:
    if frame.size != bender_trial.SIZE or panel.size != bender_trial.SIZE or not 0 <= opacity <= 1:
        raise ValueError('Неверный экран вестника')
    mask, _ = masks()
    if opacity != 1:
        mask = mask.point(lambda value: round(value * opacity))
    return Image.composite(panel, frame.convert('RGB'), mask)

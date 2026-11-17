"""Isolated screen-placement experiment for the selected studio concept.

No production renderer imports this module. The concept is a single flattened
image; screen coordinates and presenter exclusion masks are specific to it and
must not be reused for the existing animated studio.
"""
from __future__ import annotations

from functools import lru_cache
from collections import deque
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageFilter, ImageOps
from news_prototype.news_image_codec import decode_thumbnail

SIZE = (960, 540)
CONCEPT = Path(__file__).resolve().parent.parent / 'design' / 'bulletin_studio_concept.png'
# Pixel coordinates in the normalized 960x540 concept. The screen excludes
# the steel bezel; the sensei overlaps its right edge in the flattened artwork.
SCREEN = (319, 117, 621, 281)


def base_frame() -> Image.Image:
    with Image.open(CONCEPT) as source:
        return ImageOps.fit(source.convert('RGB'), SIZE, Image.Resampling.LANCZOS)


@lru_cache(maxsize=1)
def screen_mask() -> Image.Image:
    """Select the connected blue screen, not similar colors on the foreground."""
    frame = base_frame()
    pixels = frame.load()
    mask = Image.new('L', SIZE, 0)
    out = mask.load()
    x0, y0, x1, y1 = SCREEN
    seed = ((x0 + x1) // 2, (y0 + y1) // 2)
    pending = deque([seed])
    visited = set()
    while pending:
        x, y = pending.popleft()
        if (x, y) in visited or not (x0 <= x < x1 and y0 <= y < y1):
            continue
        visited.add((x, y))
        r, g, b = pixels[x, y]
        # Calibrated to the blue display of this particular selected image.
        # Flood fill prevents blue clothing elsewhere from becoming background.
        if b < 125 or b - r < 85 or b - g < 70:
            continue
        out[x, y] = 255
        pending.extend(((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)))
    if mask.histogram()[255] < 20000:
        raise ValueError('Экран концепта не распознан; изображение могло измениться')
    # Stay a pixel inside the screen, then soften the edge without bleeding
    # new images over the flattened heads or their animated facial patches.
    return mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(.35))


def apply(frame: Image.Image, image_b64: str) -> Image.Image:
    """Place one validated JPEG inside the screen, behind the traced actors."""
    if frame.size != SIZE:
        raise ValueError('Неверный размер кадра концепта')
    raw = decode_thumbnail(image_b64)
    x0, y0, x1, y1 = SCREEN
    width, height = x1 - x0, y1 - y0
    with Image.open(BytesIO(raw)) as original:
        picture = original.convert('RGB')
        panel = ImageOps.fit(picture, (width, height), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(10))
        fitted = ImageOps.contain(picture, (width, height), Image.Resampling.LANCZOS)
        panel.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
    layer = Image.new('RGB', SIZE)
    layer.paste(panel, (x0, y0))
    return Image.composite(layer, frame.convert('RGB'), screen_mask())

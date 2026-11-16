"""One optional, bounded source-picture cutaway inside the television screen."""
from __future__ import annotations

from io import BytesIO
from PIL import Image, ImageDraw, ImageOps, ImageFilter, ImageEnhance
from news_prototype.news_image_codec import decode_thumbnail

# The ticker moves down while this larger cutaway is visible. The entire source
# remains in frame, including portraits; only the decorative background is cropped.
BOX = (125, 55, 810, 443)
FADE_SECONDS = 0.35
MAX_SECONDS = 6.0


def prepare(image_b64: str) -> Image.Image:
    raw = decode_thumbnail(image_b64)
    width, height = BOX[2] - BOX[0], BOX[3] - BOX[1]
    with Image.open(BytesIO(raw)) as original:
        source = original.convert('RGB')
        background = ImageOps.fit(source, (width, height), Image.Resampling.LANCZOS)
        panel = ImageEnhance.Brightness(background.filter(ImageFilter.GaussianBlur(22))).enhance(.35)
        image = ImageOps.contain(source, (width - 12, height - 12), Image.Resampling.LANCZOS)
    panel.paste(image, ((width - image.width) // 2, (height - image.height) // 2))
    ImageDraw.Draw(panel).rectangle((0, 0, width - 1, height - 1), outline=(102, 165, 176), width=2)
    return panel


def window(dialogue_seconds: float, intro_seconds: float) -> tuple[float, float] | None:
    if dialogue_seconds <= 0:
        return None
    start = intro_seconds + min(3.0, dialogue_seconds * .20)
    duration = min(MAX_SECONDS, dialogue_seconds * .50)
    return start, start + duration


def opacity_at(when: float, limits: tuple[float, float] | None) -> float:
    if limits is None:
        return 0.0
    start, end = limits
    if when <= start or when >= end:
        return 0.0
    fade = min(FADE_SECONDS, (end - start) / 3)
    return max(0.0, min(1.0, (when - start) / fade, (end - when) / fade))


def apply(frame: Image.Image, panel: Image.Image, when: float, limits: tuple[float, float] | None) -> Image.Image:
    opacity = opacity_at(when, limits)
    if opacity <= 0:
        return frame
    result = frame.copy()
    x0, y0, x1, y1 = BOX
    under = result.crop(BOX).convert('RGB')
    result.paste(Image.blend(under, panel, opacity), (x0, y0, x1, y1))
    return result

"""One optional, bounded source-picture cutaway inside the television screen."""
from __future__ import annotations

from io import BytesIO
from PIL import Image, ImageDraw, ImageOps, ImageFilter, ImageEnhance
from news_prototype.news_image_codec import decode_thumbnail

# The ticker moves down while this larger cutaway is visible. The entire source
# remains in frame, including portraits; only the decorative background is cropped.
BOX = (125, 55, 810, 443)
FADE_SECONDS = 0.35
MAX_SECONDS = 12.0
MIN_ZOOM_SECONDS = 7.0


def _panel(source: Image.Image, background: Image.Image) -> Image.Image:
    width, height = background.size
    panel = background.copy()
    image = ImageOps.contain(source, (width - 12, height - 12), Image.Resampling.LANCZOS)
    panel.paste(image, ((width - image.width) // 2, (height - image.height) // 2))
    ImageDraw.Draw(panel).rectangle((0, 0, width - 1, height - 1), outline=(102, 165, 176), width=2)
    return panel


def prepare_views(image_b64: str, regions: list | None = None) -> tuple[Image.Image, ...]:
    """Prepare full and optional model-selected detail views.

    With no valid regions, portrait images use overlapping geometric halves.
    The full image is always shown first and last.
    """
    raw = decode_thumbnail(image_b64)
    width, height = BOX[2] - BOX[0], BOX[3] - BOX[1]
    with Image.open(BytesIO(raw)) as original:
        source = original.convert('RGB')
    background = ImageOps.fit(source, (width, height), Image.Resampling.LANCZOS)
    background = ImageEnhance.Brightness(background.filter(ImageFilter.GaussianBlur(22))).enhance(.35)
    full = _panel(source, background)
    if regions:
        from news_prototype.image_focus import validate_regions
        checked = validate_regions(regions)
        crops = []
        for x1, y1, x2, y2 in checked:
            # Small margin around approximate vision coordinates keeps context.
            left = max(0, (x1 - 30) * source.width // 1000)
            top = max(0, (y1 - 30) * source.height // 1000)
            right = min(source.width, ((x2 + 30) * source.width + 999) // 1000)
            bottom = min(source.height, ((y2 + 30) * source.height + 999) // 1000)
            crops.append(_panel(source.crop((left, top, right, bottom)), background))
        if len(crops) == 1:
            crops.append(crops[0])
        return full, *crops
    if source.width >= source.height * .85 or source.height < 4:
        return (full,)
    top_end = max(1, round(source.height * .55))
    bottom_start = min(source.height - 1, round(source.height * .45))
    top = source.crop((0, 0, source.width, top_end))
    bottom = source.crop((0, bottom_start, source.width, source.height))
    return full, _panel(top, background), _panel(bottom, background)


def prepare(image_b64: str) -> Image.Image:
    """Compatibility helper for callers that only need the full view."""
    return prepare_views(image_b64)[0]


def window(dialogue_seconds: float, intro_seconds: float) -> tuple[float, float] | None:
    if dialogue_seconds <= 0:
        return None
    start = intro_seconds + min(3.0, dialogue_seconds * .20)
    duration = min(MAX_SECONDS, dialogue_seconds * .65)
    return start, start + duration


def opacity_at(when: float, limits: tuple[float, float] | None) -> float:
    if limits is None:
        return 0.0
    start, end = limits
    if when <= start or when >= end:
        return 0.0
    fade = min(FADE_SECONDS, (end - start) / 3)
    return max(0.0, min(1.0, (when - start) / fade, (end - when) / fade))


def view_at(views: tuple[Image.Image, ...], when: float,
            limits: tuple[float, float] | None) -> Image.Image:
    """Full → upper detail → lower detail → full, with smooth dissolves."""
    if len(views) != 3 or limits is None or limits[1] - limits[0] < MIN_ZOOM_SECONDS:
        return views[0]
    progress = (when - limits[0]) / (limits[1] - limits[0])
    full, top, bottom = views
    if progress < .16:
        return full
    if progress < .24:
        return Image.blend(full, top, (progress - .16) / .08)
    if progress < .45:
        return top
    if progress < .53:
        return Image.blend(top, bottom, (progress - .45) / .08)
    if progress < .74:
        return bottom
    if progress < .82:
        return Image.blend(bottom, full, (progress - .74) / .08)
    return full


def apply(frame: Image.Image, panel: Image.Image, when: float, limits: tuple[float, float] | None,
          *, views: tuple[Image.Image, ...] | None = None) -> Image.Image:
    opacity = opacity_at(when, limits)
    if opacity <= 0:
        return frame
    if views is not None:
        panel = view_at(views, when, limits)
    result = frame.copy()
    x0, y0, x1, y1 = BOX
    under = result.crop(BOX).convert('RGB')
    result.paste(Image.blend(under, panel, opacity), (x0, y0, x1, y1))
    return result

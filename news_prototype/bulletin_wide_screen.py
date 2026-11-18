"""Static, isolated layer-compositing experiment for the selected studio art.

The two RGBA figures are manually checked cutouts from the selected frame, not
finished animation rigs. This module is not connected to the deployed renderer.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from news_prototype import bulletin_concept_screen
from news_prototype.news_image_codec import decode_thumbnail

ROOT = Path(__file__).resolve().parent.parent / 'design'
LAYERS = (ROOT / 'layers' / 'bender-concept.png', ROOT / 'layers' / 'sensei-concept.png')
SIZE = (960, 540)
FRAME = (207, 95, 687, 332)
SCREEN = (215, 103, 679, 324)


def _speaker_layer(path: Path) -> Image.Image:
    with Image.open(path) as original:
        if original.size != SIZE or original.mode != 'RGBA':
            raise ValueError('Неверный слой ведущего')
        return original.copy()


def compose(image_b64: str | None = None, *, speaker_layers=None, fallback_position: int | None = None,
            screen_panel: Image.Image | None = None) -> Image.Image:
    """Show the screen behind both separate cutouts, without starting media."""
    image = bulletin_concept_screen.base_frame().convert('RGBA')
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(FRAME, radius=12, fill=(11, 22, 32), outline=(88, 105, 116), width=5)
    draw.rounded_rectangle(SCREEN, radius=7, fill=(18, 53, 94), outline=(47, 99, 134), width=2)
    if screen_panel is not None:
        if image_b64 is not None or not isinstance(screen_panel, Image.Image):
            raise ValueError('Неверная картинка экрана')
        x0, y0, x1, y1 = SCREEN
        size = (x1 - x0 - 8, y1 - y0 - 8)
        source = screen_panel.convert('RGB')
        panel = ImageOps.fit(source, size, Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(12))
        fitted = ImageOps.contain(source, size, Image.Resampling.LANCZOS)
        panel.paste(fitted, ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2))
        image.paste(panel, (x0 + 4, y0 + 4))
    elif image_b64 is not None:
        raw = decode_thumbnail(image_b64)
        x0, y0, x1, y1 = SCREEN
        width, height = x1 - x0 - 8, y1 - y0 - 8
        with Image.open(BytesIO(raw)) as source:
            source = source.convert('RGB')
            panel = ImageOps.fit(source, (width, height), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(12))
            fitted = ImageOps.contain(source, (width, height), Image.Resampling.LANCZOS)
            panel.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
        image.paste(panel, (x0 + 4, y0 + 4))
    elif fallback_position is not None:
        from news_prototype import bulletin_fallback
        x0, y0, _, _ = SCREEN
        image.paste(bulletin_fallback.card(fallback_position), (x0 + 4, y0 + 4))
    layers = speaker_layers if speaker_layers is not None else tuple(_speaker_layer(path) for path in LAYERS)
    if not isinstance(layers, (tuple, list)) or len(layers) != 2:
        raise ValueError('Нужны два слоя ведущих')
    for layer in layers:
        if not isinstance(layer, Image.Image) or layer.mode != 'RGBA' or layer.size != SIZE:
            raise ValueError('Неверный слой ведущего')
        image.alpha_composite(layer)
    return image.convert('RGB')

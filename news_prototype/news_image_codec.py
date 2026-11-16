"""Strict bounded image thumbnail codec shared by bot and worker.

Only an explicitly retrieved public image is accepted; output is a small,
metadata-free JPEG. Decode limits are checked before allocating full pixels.
"""
from __future__ import annotations

import base64
import binascii
from io import BytesIO
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_INPUT_BYTES = 4_000_000
MAX_PIXELS = 12_000_000
MAX_DIMENSION = 800
MAX_JPEG_BYTES = 180_000
MAX_B64_CHARS = ((MAX_JPEG_BYTES + 2) // 3) * 4


def _open(data: bytes, formats: tuple[str, ...], max_pixels: int):
    if not isinstance(data, bytes) or not data or len(data) > MAX_INPUT_BYTES:
        raise ValueError('Изображение слишком большое')
    try:
        image = Image.open(BytesIO(data), formats=formats)
        if (image.width < 1 or image.height < 1 or image.width * image.height > max_pixels
                or getattr(image, 'is_animated', False)):
            image.close()
            raise ValueError('Недопустимый размер или анимация изображения')
        return image
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        raise ValueError('Не удалось прочитать изображение') from None


def make_thumbnail(data: bytes) -> str:
    """Return bounded base64 JPEG without metadata or animation."""
    with _open(data, ('JPEG', 'PNG', 'WEBP'), MAX_PIXELS) as image:
        image.load()
        image = ImageOps.exif_transpose(image)
        if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
            rgba = image.convert('RGBA')
            base = Image.new('RGB', rgba.size, (255, 255, 255))
            base.paste(rgba, mask=rgba.getchannel('A'))
            image = base
        else:
            image = image.convert('RGB')
        image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
        for quality in (82, 70, 55, 40):
            out = BytesIO()
            image.save(out, format='JPEG', quality=quality, optimize=True)
            data = out.getvalue()
            if len(data) <= MAX_JPEG_BYTES:
                return base64.b64encode(data).decode('ascii')
    raise ValueError('Изображение не удалось безопасно уменьшить')


def decode_thumbnail(value: object) -> bytes:
    if not isinstance(value, str) or not value or len(value) > MAX_B64_CHARS:
        raise ValueError('Неверное изображение')
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('Неверное изображение') from None
    if not raw or len(raw) > MAX_JPEG_BYTES or base64.b64encode(raw).decode('ascii') != value:
        raise ValueError('Неверное изображение')
    with _open(raw, ('JPEG',), MAX_DIMENSION * MAX_DIMENSION) as image:
        if image.width > MAX_DIMENSION or image.height > MAX_DIMENSION:
            raise ValueError('Недопустимый размер изображения')
        try:
            image.verify()
        except (OSError, ValueError, SyntaxError):
            raise ValueError('Повреждённое изображение') from None
    return raw

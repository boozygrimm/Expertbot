"""Approximate button press for the remote in the selected bulletin studio.

The original art is flat: this is a small compositing effect, not a hand rig.
Coordinates apply only to the selected 960x540 studio image.
"""
from __future__ import annotations

from functools import lru_cache
from PIL import Image, ImageDraw, ImageFilter

from news_prototype import bulletin_concept_screen

SIZE = bulletin_concept_screen.SIZE


def _smooth(x: float) -> float:
    x = max(0., min(1., x))
    return x * x * (3 - 2 * x)


def press_amount(t: float, click_times: list[float]) -> float:
    """Finger reaches the button at the onset of the sound, then lifts."""
    amount = 0.0
    for at in click_times:
        if at - .16 <= t < at:
            amount = max(amount, _smooth((t - at + .16) / .16))
        elif at <= t < at + .14:
            amount = max(amount, 1 - _smooth((t - at) / .14))
    return amount


@lru_cache(maxsize=1)
def finger_layer() -> Image.Image:
    """Cut out the first knuckle and tip of the foreground mechanical finger."""
    base = bulletin_concept_screen.base_frame().convert('RGBA')
    mask = Image.new('L', SIZE)
    ImageDraw.Draw(mask).polygon([(739, 403), (742, 400), (747, 400), (751, 397),
                                  (755, 396), (758, 399), (757, 403), (754, 407),
                                  (749, 409), (743, 409), (740, 407)], fill=255)
    base.putalpha(mask.filter(ImageFilter.GaussianBlur(.55)))
    return base


def apply(frame: Image.Image, amount: float) -> Image.Image:
    if frame.size != SIZE:
        raise ValueError('Неверный размер кадра')
    if amount <= 0:
        return frame
    amount = min(amount, 1.)
    # The studio is a flattened picture; overlaying a small cutout approximates
    # a press without changing the hosts or the displayed news image.
    result = frame.convert('RGBA')
    shifted = Image.new('RGBA', SIZE)
    shifted.alpha_composite(finger_layer(), dest=(round(-6 * amount), round(-4 * amount)))
    result = Image.alpha_composite(result, shifted)
    # The red button briefly darkens at the peak of the press.
    mask = Image.new('L', SIZE)
    ImageDraw.Draw(mask).rounded_rectangle((731, 391, 739, 400), radius=2, fill=round(90 * amount))
    mask = mask.filter(ImageFilter.GaussianBlur(.7))
    shade = Image.new('RGBA', SIZE, (25, 0, 0, 255))
    result = Image.composite(shade, result, mask)
    return result.convert('RGB')

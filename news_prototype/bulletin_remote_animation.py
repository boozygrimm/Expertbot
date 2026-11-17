"""Approximate button press for the remote in the selected bulletin studio.

The original art is flat: this is a small compositing effect, not a hand rig.
Coordinates apply only to the selected 960x540 studio image.
"""
from __future__ import annotations

from functools import lru_cache
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from news_prototype import bulletin_concept_screen

SIZE = bulletin_concept_screen.SIZE


def _smooth(x: float) -> float:
    x = max(0., min(1., x))
    return x * x * (3 - 2 * x)


def screen_level(t: float, on_click: float, off_click: float) -> float:
    """Fade up after power-on and fade down after power-off."""
    on = _smooth((t - on_click - .13) / .50)
    off = 1 - _smooth((t - off_click - .10) / .53)
    return min(on, off)


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
def television_mask() -> Image.Image:
    """Outer TV picture area, excluding its foreground remote and hand."""
    scale = 4
    mask = Image.new('L', (SIZE[0] * scale, SIZE[1] * scale))
    draw = ImageDraw.Draw(mask)
    def polygon(points, fill):
        draw.polygon([(x * scale, y * scale) for x, y in points], fill=fill)
    polygon([(141, 79), (190, 68), (280, 60), (423, 56), (586, 57), (704, 63),
             (763, 74), (790, 87), (803, 110), (810, 146), (813, 200),
             (813, 354), (807, 414), (795, 445), (768, 460), (675, 470),
             (530, 475), (321, 475), (197, 469), (148, 459), (127, 445),
             (116, 414), (111, 360), (110, 198), (114, 139), (122, 99)], 255)
    # Protect the remote, its finger and arm, which lie in front of the TV.
    polygon([(691, 409), (696, 399), (708, 393), (726, 392), (731, 389),
             (741, 389), (745, 398), (751, 397), (758, 393), (766, 391),
             (776, 393), (786, 398), (796, 404), (806, 411), (819, 421),
             (833, 432), (851, 447), (872, 465), (900, 490), (960, 540),
             (811, 540), (811, 482), (789, 470), (775, 465), (765, 478),
             (737, 478), (727, 469), (719, 466), (714, 456), (707, 452),
             (703, 443), (698, 438), (693, 429)], 0)
    return mask.resize(SIZE, Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(.35))


@lru_cache(maxsize=1)
def unlit_screen() -> Image.Image:
    """Dark glass and the same subtle reflection used by the older animation."""
    image = Image.new('RGB', SIZE, (5, 10, 18))
    ImageDraw.Draw(image).ellipse((130, 48, 800, 340), fill=(8, 15, 25))
    return image


def apply_television(frame: Image.Image, level: float) -> Image.Image:
    """Dim the outer television picture while leaving its frame and remote lit."""
    if frame.size != SIZE or not 0 <= level <= 1:
        raise ValueError('Неверный кадр или яркость телевизора')
    if level >= 1:
        return frame
    original = frame.convert('RGB')
    dimmed = Image.blend(unlit_screen(), original, level)
    return Image.composite(dimmed, original, television_mask())


def power_effect(frame: Image.Image, t: float, on_click: float, off_click: float,
                 click_times: list[float]) -> Image.Image:
    """Adapt the old CRT flash and remote-button glow to the new TV mask."""
    if frame.size != SIZE:
        raise ValueError('Неверный размер кадра')
    flash = max(max(0., 1 - abs(t - center) / span)
                for center, span in ((on_click + .21, .11), (off_click + .54, .12)))
    pulse = max((max(0., 1 - abs(t - at) / .16) for at in click_times), default=0.)
    if flash <= 0 and pulse <= 0:
        return frame
    result = frame.convert('RGBA')
    if flash > 0:
        line = Image.new('RGBA', SIZE)
        draw = ImageDraw.Draw(line)
        draw.rounded_rectangle((155, 269, 802, 273), radius=2,
                               fill=(168, 224, 255, round(155 * flash)))
        # Clip to the glass and exclude the foreground remote.
        line.putalpha(ImageChops.multiply(line.getchannel('A'), television_mask()))
        result = Image.alpha_composite(result, line)
    if pulse > 0:
        glow = Image.new('RGBA', SIZE)
        draw = ImageDraw.Draw(glow)
        x, y = 735, 394
        radius = 3 + round(3 * pulse)
        draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=(255, 50, 25, round(125 * pulse)))
        glow = glow.filter(ImageFilter.GaussianBlur(3))
        ImageDraw.Draw(glow).ellipse((x-2, y-2, x+2, y+2), fill=(255, 180, 110, round(230 * pulse)))
        result = Image.alpha_composite(result, glow)
    return result.convert('RGB')


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

"""Experimental layer animation for the cyber-sensei's hinged skull cap.

The base scene is a flattened still. The hidden background is reconstructed
approximately; the pilot is a separate stylized sprite, not a 3D model.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from news_prototype import bender_trial as bender

HERE = Path(__file__).resolve().parent
NATIVE = bender.REF_SIZE
HINGE = (934, 253)
MAX_ANGLE = -24.0


def rotate_lid(lid: Image.Image, amount: float) -> Image.Image:
    """Rotate a rigid layer around the fixed right-hand hinge; no frame blending."""
    amount = max(0.0, min(1.0, amount))
    return lid.rotate(MAX_ANGLE * amount, resample=Image.Resampling.BICUBIC, center=HINGE)


def prepare() -> tuple[Image.Image, Image.Image, Image.Image, Image.Image]:
    with Image.open(HERE / 'studio_mockup_soft.png') as source:
        base = source.convert('RGBA')
    if base.size != NATIVE:
        raise ValueError('Неверный размер макета студии')
    lid_mask = Image.new('L', NATIVE, 0)
    ImageDraw.Draw(lid_mask).polygon([
        (811, 253), (815, 241), (824, 229), (836, 220), (850, 215),
        (865, 211), (881, 211), (897, 213), (913, 219), (926, 228),
        (935, 239), (940, 253), (929, 255), (913, 253), (897, 250),
        (881, 249), (862, 250), (844, 253), (827, 255),
    ], fill=255)
    lid_mask = lid_mask.filter(ImageFilter.GaussianBlur(.7))
    lid = base.copy()
    lid.putalpha(lid_mask)

    # Reconstruct only the obscured background by extending an area just above
    # the head. The moving hatch and cockpit hide most of this rough patch.
    background = base.copy()
    patch = base.crop((808, 147, 944, 199)).resize((136, 52), Image.Resampling.LANCZOS)
    patch = patch.filter(ImageFilter.GaussianBlur(2.0))
    background.paste(patch, (808, 207))

    with Image.open(HERE / 'fly_cockpit.png') as raw:
        pilot = raw.convert('RGB').resize((94, 38), Image.Resampling.LANCZOS).convert('RGBA')
    pilot_mask = Image.new('L', pilot.size, 0)
    ImageDraw.Draw(pilot_mask).rounded_rectangle((0, 0, 93, 37), radius=12, fill=255)
    # Remove the plain green backdrop from the generated pilot, leaving the
    # dark cavity behind it; fade threshold edges to avoid a hard green fringe.
    key = Image.new('L', pilot.size, 0)
    pixels = pilot.get_flattened_data() if hasattr(pilot, 'get_flattened_data') else pilot.getdata()
    key.putdata([max(0, min(255, round(255 - max(0, g - max(r, b) - 5) * 18)))
                 for r, g, b, _ in pixels])
    pilot.putalpha(ImageChops.multiply(pilot_mask.filter(ImageFilter.GaussianBlur(.65)), key))
    return base, background, lid, pilot


def overlay_at_fraction(assets: tuple[Image.Image, Image.Image, Image.Image, Image.Image], amount: float) -> Image.Image:
    amount = max(0.0, min(1.0, amount))
    if amount <= 0:
        return Image.new('RGBA', bender.SIZE, (0, 0, 0, 0))
    _, background, lid, pilot = assets
    # Draw in native coordinates once, then resize the transparent effect layer.
    # The partial lid mask leaves the sensei's eyes and face untouched.
    original_alpha = lid.getchannel('A')
    effect = Image.new('RGBA', NATIVE, (0, 0, 0, 0))
    bg = background.copy()
    bg.putalpha(original_alpha)
    effect.alpha_composite(bg)

    cavity = Image.new('RGBA', NATIVE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(cavity)
    draw.rounded_rectangle((826, 210, 929, 263), radius=17, fill=(9, 18, 29, 255),
                           outline=(134, 160, 171, 255), width=2)
    cavity.alpha_composite(pilot, dest=(831, 222))
    # The hatch reveals a triangular opening around its fixed right hinge.
    # Clip the cockpit to that opening; the rotated lid remains in front.
    clip = Image.new('L', NATIVE, 0)
    left, right, pivot_y = 815, HINGE[0], HINGE[1]
    left_y = round(pivot_y - (right - left) * math.sin(math.radians(abs(MAX_ANGLE) * amount)))
    bottom = pivot_y + round(10 * amount)
    ImageDraw.Draw(clip).polygon([(left, left_y), (right, pivot_y), (right, bottom), (left, bottom)], fill=255)
    cavity.putalpha(Image.composite(cavity.getchannel('A'), Image.new('L', NATIVE, 0), clip))
    effect.alpha_composite(cavity)
    # A fixed pivot grounds the motion: only the opposite end of the rigid lid
    # swings upward, rather than making the whole cap float vertically.
    hinge = Image.new('RGBA', NATIVE, (0, 0, 0, 0))
    d = ImageDraw.Draw(hinge)
    d.ellipse((929, 248, 939, 258), fill=(45, 62, 72, 255), outline=(170, 192, 199, 255), width=2)
    d.ellipse((932, 251, 936, 255), fill=(121, 156, 168, 255))
    effect.alpha_composite(hinge)
    effect.alpha_composite(rotate_lid(lid, amount))
    # Hide unavoidable cut-out edge differences at the very start/end so the
    # original closed head is reached continuously, rather than popping in.
    if amount < .08:
        alpha = effect.getchannel('A').point(lambda value: round(value * amount / .08))
        effect.putalpha(alpha)
    return effect.resize(bender.SIZE, Image.Resampling.LANCZOS)


def at_fraction(frame: Image.Image, assets: tuple[Image.Image, Image.Image, Image.Image, Image.Image], amount: float) -> Image.Image:
    return Image.alpha_composite(frame.convert('RGBA'), overlay_at_fraction(assets, amount)).convert('RGB')


def openness(t: float, start: float, hold: float = 2.2, motion: float = .6) -> float:
    if t < start or t >= start + 2 * motion + hold:
        return 0.0
    if t < start + motion:
        v = (t - start) / motion
        return v * v * (3 - 2 * v)
    if t < start + motion + hold:
        return 1.0
    v = (t - start - motion - hold) / motion
    return 1 - v * v * (3 - 2 * v)

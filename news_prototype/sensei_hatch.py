"""Experimental layer animation for the cyber-sensei's hinged skull cap.

The base scene is a flattened still. The hidden background is reconstructed
approximately; the pilot is a separate stylized sprite, not a 3D model.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from news_prototype import bender_trial as bender

HERE = Path(__file__).resolve().parent
NATIVE = bender.REF_SIZE


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
    draw.rounded_rectangle((826, 223, 929, 263), radius=17, fill=(9, 18, 29, 255),
                           outline=(134, 160, 171, 255), width=2)
    # Reveal the pilot from bottom to top as the hatch lifts, not by crossfading
    # unrelated full-scene images.
    visible_height = max(1, round(38 * amount))
    pilot_area = pilot.crop((0, 38 - visible_height, 94, 38))
    cavity.alpha_composite(pilot_area, dest=(831, 260 - visible_height))
    clip = Image.new('L', NATIVE, 0)
    ImageDraw.Draw(clip).rounded_rectangle((826, 263 - visible_height, 929, 263), radius=min(9, visible_height // 2), fill=255)
    cavity.putalpha(Image.composite(cavity.getchannel('A'), Image.new('L', NATIVE, 0), clip))
    effect.alpha_composite(cavity)
    effect.alpha_composite(lid, dest=(round(6 * amount), round(-34 * amount)))
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

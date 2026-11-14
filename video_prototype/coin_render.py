"""Isolated trial: a spinning gold coin drops beside Bender's dollar pendant."""
import math
from PIL import Image, ImageDraw

if __package__:
    from .render import SIZE
    from .flask_render import render as base_render
else:
    from render import SIZE
    from flask_render import render as base_render


def smooth(value):
    v = max(0., min(1., value))
    return v * v * (3 - 2 * v)


def coin_pose(seconds):
    if not 2.0 <= seconds < 6.0:
        return None
    opacity = min(smooth((seconds - 2) / .4), smooth((6 - seconds) / .4))
    progress = max(0., min(1., (seconds - 3.0) / 2.3))
    # Gentle acceleration, then hold briefly at the bottom before fading.
    y = 300 + 69 * progress * progress
    return y, opacity


def coin_sprite():
    """Gold coin with an inset rim and dollar engraving, supersampled once."""
    factor = 4
    img = Image.new('RGBA', (16*factor, 16*factor))
    draw = ImageDraw.Draw(img)
    def coords(values):
        return tuple(round(v * factor) for v in values)
    draw.ellipse(coords((1,1,15,15)), fill=(116,64,6,255))
    draw.ellipse(coords((1.5,1.5,14,14)), fill=(244,175,30,255), outline=(255,228,115,255), width=3)
    draw.ellipse(coords((3,3,12.5,12.5)), fill=(227,147,16,255), outline=(169,96,8,255), width=3)
    # Simple engraving stays recognisable without relying on system fonts.
    draw.line([coords((10,5))[:2], coords((6,5))[:2], coords((5,7))[:2],
               coords((10,9))[:2], coords((9,11))[:2], coords((5,11))[:2]],
              fill=(117,69,9,255), width=4, joint='curve')
    draw.line([coords((7.5,4))[:2], coords((7.5,12))[:2]], fill=(117,69,9,255), width=3)
    draw.ellipse(coords((3.5,3.2,5,4.7)), fill=(255,247,194,255))
    return img


COIN = coin_sprite()


def frame_with_coin(frame, seconds):
    pose = coin_pose(seconds)
    if pose is None:
        return frame
    y, opacity = pose
    scale = SIZE / 480
    # Slow, continuous spin; even its thinnest edge remains visible.
    width = max(2, round((3 + 11 * abs(math.cos((seconds - 2) * math.pi * 1.25))) * scale))
    height = round(14 * scale)
    coin = COIN.resize((width, height), Image.Resampling.LANCZOS)
    coin.putalpha(coin.getchannel('A').point(lambda v: round(v * opacity)))
    image = Image.frombytes('RGB', (SIZE, SIZE), frame).convert('RGBA')
    image.alpha_composite(coin, (round(229*scale - width/2), round(y*scale - height/2)))
    return image.convert('RGB').tobytes()


def render(audio, closed, opened, blink, plate, output):
    base_render(audio, closed, opened, blink, plate, output, frame_effect=frame_with_coin)

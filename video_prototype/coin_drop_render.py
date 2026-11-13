"""Trial: a spinning coin falls all the way out of the circular picture."""
import math
from PIL import Image, ImageChops, ImageDraw, ImageFilter

if __package__:
    from .render import SIZE
    from .coin_render import COIN, smooth
    from .mouth_trial_render import render as mouth_render
else:
    from render import SIZE
    from coin_render import COIN, smooth
    from mouth_trial_render import render as mouth_render


def circle_mask():
    # The inner edge of the neon border; hide the coin behind it as it leaves.
    scale = SIZE / 480
    mask = Image.new('L', (SIZE, SIZE))
    ImageDraw.Draw(mask).ellipse(tuple(round(v*scale) for v in (33, 33, 447, 447)), fill=255)
    return mask.filter(ImageFilter.GaussianBlur(.5 * scale))


CIRCLE = circle_mask()


def coin_pose(seconds):
    if not 2.0 <= seconds < 5.6:
        return None
    appear = smooth((seconds - 2) / .4)
    progress = max(0., min(1., (seconds - 2.8) / 2.8))
    y = 300 + 160 * progress * progress
    return y, appear


def frame_with_coin(frame, seconds):
    pose = coin_pose(seconds)
    if pose is None:
        return frame
    y, opacity = pose
    scale = SIZE / 480
    width = max(2, round((3 + 11 * abs(math.cos((seconds - 2) * math.pi * 1.25))) * scale))
    height = round(14 * scale)
    coin = COIN.resize((width, height), Image.Resampling.LANCZOS)
    coin.putalpha(coin.getchannel('A').point(lambda v: round(v * opacity)))
    layer = Image.new('RGBA', (SIZE, SIZE))
    layer.alpha_composite(coin, (round(229*scale - width/2), round(y*scale - height/2)))
    layer.putalpha(ImageChops.multiply(layer.getchannel('A'), CIRCLE))
    image = Image.frombytes('RGB', (SIZE, SIZE), frame).convert('RGBA')
    image.alpha_composite(layer)
    return image.convert('RGB').tobytes()


def render(audio, closed, opened, blink, plate, output):
    mouth_render(audio, closed, opened, blink, plate, output, frame_effect=frame_with_coin)

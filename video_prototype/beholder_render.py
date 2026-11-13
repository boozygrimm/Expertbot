"""Isolated trial: a small monster moves inside the left green flask."""
import math
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFilter

if __package__:
    from .render import SIZE
    from .flask_render import render as render_flask
else:
    from render import SIZE
    from flask_render import render as render_flask

SPRITE = Path(__file__).resolve().parent / 'assets/beholder.png'


def flask_mask():
    scale = SIZE / 480
    points = [(84, 140), (109, 140), (118, 146), (125, 153),
              (128, 161), (126, 168), (121, 173), (82, 173), (75, 168),
              (75, 158), (79, 148)]
    mask = Image.new('L', (SIZE, SIZE))
    ImageDraw.Draw(mask).polygon([(round(x*scale), round(y*scale)) for x, y in points], fill=255)
    return mask.filter(ImageFilter.GaussianBlur(.7))


GREEN_MASK = flask_mask()


def prepare_sprites(path=SPRITE, count=24):
    """Gently bend the tentacles; leave the middle of the creature stable."""
    with Image.open(path) as im:
        source = im.convert('RGBA')
    w, h = source.size
    if source.getchannel('A').getextrema()[0] != 0:
        raise ValueError('Нужен прозрачный спрайт существа')
    xs = [round(w * i / 8) for i in range(9)]
    ys = [round(h * i / 8) for i in range(9)]
    frames = []
    for i in range(count):
        phase = 2 * math.pi * i / count
        def pos(x, y):
            edge = min(1., max(0., (abs(x / w - .5) - .18) / .25))
            dx = edge * 6 * math.sin(phase + y / h * 3)
            dy = edge * 5 * math.sin(phase + x / w * 4)
            return x + dx, y + dy
        mesh = []
        for row in range(8):
            for col in range(8):
                x0, x1, y0, y1 = xs[col], xs[col+1], ys[row], ys[row+1]
                mesh.append(((x0, y0, x1, y1), (*pos(x0, y0), *pos(x0, y1),
                                                   *pos(x1, y1), *pos(x1, y0))))
        warped = source.transform(source.size, Image.Transform.MESH, mesh,
                                  resample=Image.Resampling.BICUBIC)
        width = round(47 * SIZE / 480)
        height = round(width * h / w)
        frames.append(warped.resize((width, height), Image.Resampling.LANCZOS))
    return frames


def reveal(seconds):
    if not 1.5 <= seconds < 7:
        return None
    amount = max(0, min(1, (seconds - 1.5) / 1, (7 - seconds) / 1))
    return amount * amount * (3 - 2 * amount)


def frame_with_beholder(frame, seconds, sprites):
    amount = reveal(seconds)
    if amount is None:
        return frame
    scale = SIZE / 480
    # Two-second loop, with no abrupt jump at the cycle boundary.
    sprite = sprites[int(seconds * len(sprites) / 2) % len(sprites)]
    x = round(78 * scale)
    y = round((139 + 15 * (1 - amount) + .5 * math.sin(seconds * math.pi)) * scale)
    layer = Image.new('RGBA', (SIZE, SIZE))
    layer.alpha_composite(sprite, (x, y))
    opacity = layer.getchannel('A').point(lambda v: round(v * amount * .9))
    layer.putalpha(ImageChops.multiply(opacity, GREEN_MASK))
    image = Image.frombytes('RGB', (SIZE, SIZE), frame).convert('RGBA')
    image.alpha_composite(layer)
    return image.convert('RGB').tobytes()


def render(audio, closed, opened, blink, plate, output, sprite_path=SPRITE):
    sprites = prepare_sprites(sprite_path)
    render_flask(audio, closed, opened, blink, plate, output,
                 frame_effect=lambda frame, seconds: frame_with_beholder(frame, seconds, sprites))

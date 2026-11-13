"""A separate trial: an eye briefly rises in the purple flask and looks right."""
import math
from PIL import Image, ImageChops, ImageDraw

if __package__:
    from .render import SIZE
    from .flask_render import FLASK_MASK, render as render_flask
else:
    from render import SIZE
    from flask_render import FLASK_MASK, render as render_flask


def eye_layer(seconds):
    if not 2.0 <= seconds < 6.5:
        return None
    def smooth(v):
        v = max(0., min(1., v))
        return v * v * (3 - 2 * v)
    show = smooth(min((seconds - 2) / .8, (6.5 - seconds) / .8, 1))
    # Supersample only a small patch, not an entire video frame.
    oversample = 4
    origin_x, origin_y = 180, 108
    patch_width, patch_height = 36, 37
    layer = Image.new('RGBA', (patch_width * oversample, patch_height * oversample))
    draw = ImageDraw.Draw(layer)
    cx, cy = 198, 135 - 11 * show
    rx, ry = 9, 5.5
    alpha = round(235 * show)
    def box(x1, y1, x2, y2):
        return (round((x1-origin_x)*oversample), round((y1-origin_y)*oversample),
                round((x2-origin_x)*oversample), round((y2-origin_y)*oversample))
    draw.ellipse(box(cx-rx, cy-ry, cx+rx, cy+ry), fill=(242, 225, 240, alpha),
                 outline=(132, 73, 165, alpha), width=3)
    # Pupil turns gradually toward Bender on the right and stays there.
    look = smooth((seconds - 3.0) / .8)
    px = cx - 2 + 5 * look
    draw.ellipse(box(px-2.8, cy-3.5, px+2.8, cy+3.5), fill=(95, 43, 132, alpha))
    draw.ellipse(box(px-1.25, cy-2.4, px+1.25, cy+2.4), fill=(15, 12, 32, alpha))
    draw.ellipse(box(px-.8, cy-2.2, px+.2, cy-1.2), fill=(255, 255, 255, alpha))
    scale = SIZE / 480
    layer = layer.resize((round(patch_width*scale), round(patch_height*scale)), Image.Resampling.LANCZOS)
    frame_layer = Image.new('RGBA', (SIZE, SIZE))
    frame_layer.alpha_composite(layer, (round(origin_x*scale), round(origin_y*scale)))
    frame_layer.putalpha(ImageChops.multiply(frame_layer.getchannel('A'), FLASK_MASK))
    return frame_layer


def frame_with_eye(frame, seconds):
    layer = eye_layer(seconds)
    if layer is None:
        return frame
    image = Image.frombytes('RGB', (SIZE, SIZE), frame).convert('RGBA')
    image.alpha_composite(layer)
    return image.convert('RGB').tobytes()


def render(audio, closed, opened, blink, plate, output):
    render_flask(audio, closed, opened, blink, plate, output, frame_effect=frame_with_eye)

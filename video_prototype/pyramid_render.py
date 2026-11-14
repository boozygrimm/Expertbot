"""Separate trial: an illuminated eye-in-pyramid sigil between the flasks."""
import math
from PIL import Image, ImageChops, ImageDraw, ImageFilter

if __package__:
    from .render import SIZE
    from .flask_render import render as render_flask
else:
    from render import SIZE
    from flask_render import render as render_flask

# Fixed 480px scene coordinates. All glow is clipped to this patch, which is
# between the green and purple flask masks, above the sign and hedgehog track.
REGION = (130, 96, 165, 143)


def make_sigil():
    """Draw at high resolution and downsample once for legibility."""
    factor = 4
    w, h = REGION[2] - REGION[0], REGION[3] - REGION[1]
    ink = Image.new('RGBA', (w * factor, h * factor))
    d = ImageDraw.Draw(ink)
    def point(x, y):
        return (round(x*factor), round(y*factor))
    def box(x1, y1, x2, y2):
        return tuple(round(v*factor) for v in (x1,y1,x2,y2))
    # Dark translucency under gold edges separates the sigil from the scene.
    tri = [point(17.5, 5), point(3.5, 33), point(31.5, 33)]
    d.polygon(tri, fill=(38, 24, 3, 158))
    d.line(tri + tri[:1], fill=(255, 209, 103, 255), width=6, joint='curve')
    d.line([point(9, 23), point(26, 23)], fill=(215, 159, 55, 210), width=3)
    d.line([point(6.5, 28), point(28.5, 28)], fill=(215, 159, 55, 210), width=3)
    for x, y in [(13, 24), (22, 29), (11, 29)]:
        d.line([point(x,y), point(x,y+3)], fill=(215,159,55,200), width=2)
    # Eye with pointed corners, bright iris and dark pupil.
    d.polygon([point(10,18), point(13.5,15.5), point(17.5,14.5),
               point(21.5,15.5), point(25,18), point(21.5,20.5),
               point(17.5,21.5), point(13.5,20.5)], fill=(255,235,169,255))
    d.ellipse(box(15, 15.4, 20, 20.7), fill=(160,91,15,255))
    d.ellipse(box(16.6, 16.1, 18.4, 20.1), fill=(30,16,10,255))
    d.ellipse(box(17, 16.1, 17.8, 17), fill=(255,255,230,255))
    # Bounded, faint halo; no glow can bleed into adjacent effects.
    halo = ink.getchannel('A').filter(ImageFilter.GaussianBlur(2.4*factor))
    glow = Image.new('RGBA', ink.size, (255, 177, 40, 0))
    glow.putalpha(halo.point(lambda v: round(v * .45)))
    glow.alpha_composite(ink)
    scale = SIZE / 480
    return glow.resize((round(w*scale), round(h*scale)), Image.Resampling.LANCZOS)


SIGIL = make_sigil()


def visibility(seconds):
    if not 1.5 <= seconds < 7:
        return None
    v = max(0, min(1, (seconds - 1.5) / .9, (7 - seconds) / .9))
    return v * v * (3 - 2 * v)


def frame_with_pyramid(frame, seconds):
    v = visibility(seconds)
    if v is None:
        return frame
    pulse = .87 + .13 * (1 + math.sin(seconds * math.pi)) / 2
    layer = SIGIL.copy()
    layer.putalpha(layer.getchannel('A').point(lambda a: round(a * v * pulse)))
    image = Image.frombytes('RGB', (SIZE, SIZE), frame).convert('RGBA')
    image.alpha_composite(layer, (round(REGION[0]*SIZE/480), round(REGION[1]*SIZE/480)))
    return image.convert('RGB').tobytes()


def render(audio, closed, opened, blink, plate, output):
    render_flask(audio, closed, opened, blink, plate, output, frame_effect=frame_with_pyramid)

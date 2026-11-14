"""Separate trial: Bender looks toward the creature in the green flask."""
from PIL import Image, ImageChops, ImageDraw, ImageFilter

if __package__:
    from .render import FPS, SIZE, blink_at
    from .beholder_render import prepare_sprites, frame_with_beholder
    from .mouth_trial_render import render as base_render
else:
    from render import FPS, SIZE, blink_at
    from beholder_render import prepare_sprites, frame_with_beholder
    from mouth_trial_render import render as base_render

# Eyes are hand-masked for the approved source image, at output size 480.
EYE_BOX = (238, 162, 328, 181)
PUPILS = [
    ((260,170,277,179), (268,171), (259,171),
     [(241,175),(247,173),(259,171),(278,169),(278,183),(271,190),(253,190),(242,184)]),
    ((306,163,321,172), (313,166), (303,166),
     [(281,170),(293,168),(309,165),(325,163),(325,174),(317,183),(298,185),(284,180)]),
]


def eye_backdrop(base):
    """Fill only the former pupils with adjacent eye colour, preserving eyelids."""
    result = base.copy()
    src = base.load()
    for (x1, y1, x2, y2), _, _, _ in PUPILS:
        mask = Image.new('L', base.size)
        ImageDraw.Draw(mask).ellipse((x1, y1-4, x2, y2), fill=255)
        below_lid = Image.new('L', base.size)
        ImageDraw.Draw(below_lid).polygon([(x1,y1+1),(x2,y1-1),(x2,y2+2),(x1,y2+2)], fill=255)
        mask = ImageChops.multiply(mask, below_lid).filter(ImageFilter.GaussianBlur(.5))
        replacement = base.copy()
        pixels = replacement.load()
        for y in range(y1-2, y2+3):
            for x in range(x1-2, x2+3):
                pixels[x, y] = src[x1-7, y+4]
        result = Image.composite(replacement, result, mask)
    return result


def prepare_eye_frames(closed, steps=11, targets=None):
    if SIZE != 480:
        raise ValueError('Этот эксперимент размечен под 480×480')
    with Image.open(closed) as image:
        base = image.convert('RGB').resize((SIZE, SIZE), Image.Resampling.LANCZOS)
    clean = eye_backdrop(base)
    frames = []
    for i in range(steps):
        fraction = i / (steps-1)
        frame = clean.convert('RGBA')
        for pupil_index, (_, center, default_target, polygon) in enumerate(PUPILS):
            target = targets[pupil_index] if targets is not None else default_target
            cx = center[0] + (target[0]-center[0]) * fraction
            cy = center[1] + (target[1]-center[1]) * fraction
            iris = Image.new('RGBA', (SIZE, SIZE))
            draw = ImageDraw.Draw(iris)
            draw.ellipse((cx-5.5,cy-5.5,cx+5.5,cy+5.5), fill=(3,2,5,255))
            draw.ellipse((cx+1,cy-1,cx+2,cy), fill=(225,225,190,220))
            visible = Image.new('L',(SIZE,SIZE))
            ImageDraw.Draw(visible).polygon(polygon, fill=255)
            iris.putalpha(ImageChops.multiply(iris.getchannel('A'), visible).filter(ImageFilter.GaussianBlur(.5)))
            frame.alpha_composite(iris)
        frames.append(frame.convert('RGB').crop(EYE_BOX))
    return frames


def gaze_amount(seconds):
    # The creature is visible from 1.5 to 7.0 seconds in this isolated demo.
    def smooth(x):
        x = min(1., max(0., x))
        return x*x*(3-2*x)
    if seconds < 2.2 or seconds >= 6.3:
        return 0.
    return min(smooth((seconds-2.2)/.5), smooth((6.3-seconds)/.5))


def frame_with_gaze(frame, seconds, eye_frames):
    if blink_at(round(seconds * FPS)):
        return frame
    position = round(gaze_amount(seconds) * (len(eye_frames)-1))
    image = Image.frombytes('RGB',(SIZE,SIZE),frame)
    image.paste(eye_frames[position], EYE_BOX[:2])
    return image.tobytes()


def render(audio, closed, opened, blink, plate, output):
    eyes = prepare_eye_frames(closed)
    creature = prepare_sprites()
    def effect(frame, seconds):
        frame = frame_with_beholder(frame, seconds, creature)
        return frame_with_gaze(frame, seconds, eyes)
    base_render(audio, closed, opened, blink, plate, output, frame_effect=effect)

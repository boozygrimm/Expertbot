"""Experimental cutout arm rig for the approved robot artwork, not production."""
import math
from pathlib import Path
import subprocess

from PIL import Image, ImageChops, ImageDraw, ImageFilter

if __package__:
    from .render import FPS, SIZE, audio_levels, blink_at, mouth_states
else:
    from render import FPS, SIZE, audio_levels, blink_at, mouth_states

CANVAS = 512


def polygon_mask(polygons, *, blur=.6):
    mask = Image.new('L', (CANVAS, CANVAS), 0)
    draw = ImageDraw.Draw(mask)
    for points in polygons:
        draw.polygon(points, fill=255)
    return mask.filter(ImageFilter.GaussianBlur(blur))


def _patch_mask(box, radius, blur):
    mask = Image.new('L', (CANVAS, CANVAS), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
    return mask.filter(ImageFilter.GaussianBlur(blur))


def _image(path):
    with Image.open(path) as image:
        if image.width != image.height or image.width < CANVAS:
            raise ValueError(f'Нужна квадратная картинка не меньше {CANVAS}×{CANVAS}')
        return image.convert('RGB').resize((CANVAS, CANVAS), Image.Resampling.LANCZOS)


def build_layers(frames_dir, raised_image):
    closed = _image(frames_dir / 'closed.png')
    opened = _image(frames_dir / 'open.png')
    blink = _image(frames_dir / 'blink.png')
    raised = _image(raised_image)
    mouth_mask = _patch_mask((259, 194, 376, 279), 10, 5)
    eye_mask = _patch_mask((246, 150, 365, 210), 10, 3)
    originals = {}
    for mouth in (False, True):
        base = Image.composite(opened, closed, mouth_mask) if mouth else closed
        for eyes in (False, True):
            originals[mouth, eyes] = Image.composite(blink, base, eye_mask) if eyes else base

    hand_mask = polygon_mask([
        [(291, 411), (395, 332), (406, 333), (416, 340), (418, 352), (314, 433), (299, 429)],
        [(326, 375), (329, 360), (338, 352), (353, 351), (367, 356), (377, 366),
         (386, 380), (391, 416), (381, 437), (350, 448), (337, 441), (329, 426)],
    ])
    fore_mask = polygon_mask([
        [(377, 378), (470, 380), (473, 410), (456, 434), (410, 445), (382, 437)]
    ])
    circle = Image.new('L', (CANVAS, CANVAS), 0)
    ImageDraw.Draw(circle).ellipse((38, 38, 474, 474), fill=255)
    fore_mask = ImageChops.multiply(fore_mask, circle)

    # Recover the torso behind the old hand from the raised-pose image, only
    # inside the old-arm region. Remaining scenery is the approved original.
    fill_mask = polygon_mask([
        [(291, 411), (395, 332), (406, 333), (416, 340), (418, 352), (314, 433), (299, 429)],
        [(326, 375), (329, 360), (338, 352), (353, 351), (367, 356), (377, 366),
         (386, 380), (430, 381), (466, 385), (472, 409), (457, 432), (410, 445),
         (350, 448), (337, 441), (329, 426)],
    ], blur=0).filter(ImageFilter.MaxFilter(13)).filter(ImageFilter.GaussianBlur(3))
    backgrounds = {key: Image.composite(raised, image, fill_mask).convert('RGBA')
                   for key, image in originals.items()}
    def make_sprite(alpha):
        sprite = closed.convert('RGBA')
        sprite.putalpha(alpha)
        return sprite
    return originals, backgrounds, make_sprite(hand_mask), make_sprite(fore_mask)


def amount_at(seconds):
    """One slow lift, a short hold, then lower; no gesture while speaking claim."""
    if seconds < 1.2 or seconds >= 5.0:
        return 0.0
    if seconds < 2.6:
        t = (seconds - 1.2) / 1.4
    elif seconds < 3.6:
        return 1.0
    else:
        t = (5.0 - seconds) / 1.4
    return t * t * (3 - 2 * t)


def transform_forearm(image, elbow, wrist):
    # Map two original points to elbow and wrist. Keep cross-section thickness
    # approximately constant; only the length along the arm changes.
    x0, y0 = 460, 402
    ex, ey = elbow
    wx, wy = wrist
    u, v = (ex - wx) / 80, (ey - wy) / 80
    norm = math.hypot(u, v)
    b, d = -v / norm, u / norm
    tx, ty = ex - u * x0 - b * y0, ey - v * x0 - d * y0
    det = u * d - b * v
    inverse = (d / det, -b / det, (b * ty - d * tx) / det,
               -v / det, u / det, (v * tx - u * ty) / det)
    return image.transform((CANVAS, CANVAS), Image.Transform.AFFINE, inverse,
                           resample=Image.Resampling.BICUBIC)


def frame(layers, mouth, eyes, amount):
    originals, backgrounds, hand, forearm = layers
    if amount <= 0:
        image = originals[mouth, eyes].convert('RGB')
    else:
        s = max(0.0, min(1.0, amount))
        wrist = (380 + 10 * s, 402 - 147 * s)
        elbow = (460 - 10 * s, 402 - 62 * s)
        fore = transform_forearm(forearm, elbow, wrist)
        moved = hand.rotate(-35 * s, center=(380, 402), resample=Image.Resampling.BICUBIC)
        moved = moved.transform((CANVAS, CANVAS), Image.Transform.AFFINE,
                                (1, 0, -(wrist[0] - 380), 0, 1, -(wrist[1] - 402)),
                                resample=Image.Resampling.BICUBIC)
        image = backgrounds[mouth, eyes].copy()
        image.alpha_composite(fore)
        image.alpha_composite(moved)
        image = image.convert('RGB')
    return image.resize((SIZE, SIZE), Image.Resampling.LANCZOS).tobytes()


def render(audio, frames_dir, raised_image, output):
    levels = audio_levels(audio)
    layers = build_layers(Path(frames_dir), Path(raised_image))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
           '-s', f'{SIZE}x{SIZE}', '-r', str(FPS), '-i', 'pipe:0', '-i', str(audio),
           '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
           '-c:a', 'aac', '-b:a', '128k', '-shortest', '-movflags', '+faststart', str(output)]
    process = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i, opened in enumerate(mouth_states(levels)):
            process.stdin.write(frame(layers, opened, blink_at(i), amount_at(i / FPS)))
    finally:
        process.stdin.close()
        status = process.wait()
    if status:
        raise RuntimeError('FFmpeg не смог создать видео')
    print(f'Пробный кружок с рукой готов: {output}', flush=True)

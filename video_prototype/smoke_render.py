"""Experimental smoke and ember over the existing approved PNGTuber frames."""
import argparse
import math
from pathlib import Path
import subprocess

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

if __package__:
    from .render import FPS, SIZE, audio_levels, blink_at, make_frames, mouth_states
else:
    from render import FPS, SIZE, audio_levels, blink_at, make_frames, mouth_states

# Mask the smoke already present in the approved 2048px illustration. The
# source is a flat picture, so a narrow band of background also deforms.
def make_smoke_mask():
    size = 512
    mask = Image.new('L', (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.line([(349, 57), (360, 85), (375, 103), (390, 120), (400, 143),
               (404, 167), (399, 188), (405, 207), (400, 233), (405, 255),
               (398, 278), (392, 295), (397, 316), (405, 337)],
              fill=235, width=20, joint='curve')
    mask = mask.filter(ImageFilter.GaussianBlur(4))
    # Keep the gold chain fixed even though smoke passes in front of it.
    ImageDraw.Draw(mask).polygon([(380, 244), (408, 239), (414, 259),
                                  (392, 277), (379, 269)], fill=0)
    mask = mask.filter(ImageFilter.GaussianBlur(2))
    return mask.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def make_ember_mask():
    mask = Image.new('L', (SIZE, SIZE), 0)
    x, y = SIZE * 1632 / 2048, SIZE * 1370 / 2048
    ImageDraw.Draw(mask).ellipse((x - 5, y - 5, x + 5, y + 5), fill=255)
    return mask.filter(ImageFilter.GaussianBlur(1.5))


SMOKE_MASK = make_smoke_mask()
EMBER_MASK = make_ember_mask()


def smoke_highlights(original):
    """Approximate a smoke layer using bright pixels inside the smoke corridor.

    The original is flat: this keeps the background fixed but leaves the
    original, stationary smoke visible underneath the moving highlights.
    """
    light = original.convert('L').point(
        lambda value: max(0, min(180, int((value - 50) * 2.4))))
    # The lower smoke overlaps a bright metallic shoulder, which brightness
    # alone cannot separate. Fade it out rather than animate the shoulder.
    fade = Image.new('L', (1, SIZE))
    fade.putdata([max(0, min(255, round((SIZE * 285 / 512 - y) * 255 /
                                         (SIZE * 35 / 512))))
                  for y in range(SIZE)])
    fade = fade.resize((SIZE, SIZE))
    return ImageChops.multiply(ImageChops.multiply(light, SMOKE_MASK), fade)


def frame_with_effects(frame, time_seconds):
    """Move only smoke-like highlights; leave the original background still."""
    original = Image.frombytes('RGB', (SIZE, SIZE), frame)
    highlights = smoke_highlights(original)
    mesh = []
    for top in range(0, SIZE, 8):
        bottom = min(top + 8, SIZE)
        def offset(y):
            phase = time_seconds * 1.7 - y * .045
            return 8 * math.sin(phase), 2 * math.sin(phase * .7)
        dx1, dy1 = offset(top)
        dx2, dy2 = offset(bottom)
        mesh.append(((0, top, SIZE, bottom),
                     (dx1, top + dy1, dx2, bottom + dy2,
                      SIZE + dx2, bottom + dy2, SIZE + dx1, top + dy1)))
    moved = highlights.transform((SIZE, SIZE), Image.Transform.MESH, mesh,
                                 resample=Image.Resampling.BICUBIC)
    # Apply the same corridor at the destination, protecting face and chain.
    moved = ImageChops.multiply(moved, SMOKE_MASK)
    smoke_color = Image.new('RGB', (SIZE, SIZE), (177, 172, 192))
    result = Image.composite(smoke_color, original, moved)
    flicker = .5 + .5 * math.sin(time_seconds * 3.1 + .5 * math.sin(time_seconds * 6.3))
    bright = ImageEnhance.Brightness(result).enhance(1.0 + .7 * flicker)
    return Image.composite(bright, result, EMBER_MASK).tobytes()


def render(audio, closed, opened, blink, output):
    levels = audio_levels(audio)
    frames = make_frames(closed, opened, blink)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
           '-s', f'{SIZE}x{SIZE}', '-r', str(FPS), '-i', 'pipe:0', '-i', str(audio),
           '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
           '-c:a', 'aac', '-b:a', '128k', '-shortest', '-movflags', '+faststart', str(output)]
    process = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i, state in enumerate(mouth_states(levels)):
            frame = frames[state, blink_at(i)]
            process.stdin.write(frame_with_effects(frame, i / FPS))
    finally:
        process.stdin.close()
        status = process.wait()
    if status:
        raise RuntimeError('FFmpeg не смог создать видео')
    print(f'Видео с дымом готово: {output}', flush=True)


def main():
    parser = argparse.ArgumentParser(description='Пробный рендер дыма и тлеющей сигары')
    for name in ('audio', 'closed', 'open', 'blink', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    render(args.audio, args.closed, args.open, args.blink, args.output)


if __name__ == '__main__':
    main()

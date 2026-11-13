"""Experimental smoke and ember over the existing approved PNGTuber frames."""
import argparse
import math
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw, ImageFilter

if __package__:
    from .render import FPS, SIZE, audio_levels, blink_at, make_frames, mouth_states
else:
    from render import FPS, SIZE, audio_levels, blink_at, make_frames, mouth_states

# The approved original is 2048px square; cigar tip is near (1632, 1370).
TIP = (SIZE * 1632 / 2048, SIZE * 1370 / 2048)


def effects(time_seconds):
    """Small drifting smoke wisps and a flickering ember; deterministic per time."""
    layer = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, 'RGBA')
    x0, y0 = TIP
    # A few translucent puffs rise from the tip and drift along the existing
    # smoke in the picture. They fade in/out so the loop has no hard jump.
    # Decorative only: the robot is not performing an actual inhalation.
    for strand in range(5):
        phase = (time_seconds * .34 + strand / 5) % 1.0
        rise = phase * 72
        x = x0 - 2 - rise * .16 + (2.5 + phase * 4) * math.sin(phase * 8 + strand * 1.7)
        y = y0 - 4 - rise
        rx = 3 + phase * 5
        ry = 5 + phase * 7
        alpha = round(125 * math.sin(math.pi * phase) ** 1.3)
        draw.ellipse((x - rx, y - ry, x + rx, y + ry), fill=(198, 209, 223, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(3))
    glow = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow, 'RGBA')
    intensity = .5 + .5 * math.sin(time_seconds * 3.1 + .5 * math.sin(time_seconds * 6.3))
    gd.ellipse((x0 - 5, y0 - 5, x0 + 5, y0 + 5), fill=(255, 90, 4, round(45 + 95 * intensity)))
    glow = glow.filter(ImageFilter.GaussianBlur(2))
    layer = Image.alpha_composite(layer, glow)
    return layer


def frame_with_effects(frame, time_seconds):
    image = Image.frombytes('RGB', (SIZE, SIZE), frame).convert('RGBA')
    return Image.alpha_composite(image, effects(time_seconds)).convert('RGB').tobytes()


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

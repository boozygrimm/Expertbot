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
    # Fine rising ribbons, laid over the illustration's existing static smoke.
    # Animation is decorative and not a simulated inhalation or puff.
    for strand in range(3):
        points = []
        for i in range(34):
            h = i * 1.65
            x = x0 - h * .09 + (2.0 + h * .04) * math.sin(h * .085 - time_seconds * 1.4 + strand * 1.8)
            y = y0 - h - 1.5 * math.sin(time_seconds * 1.1 + strand)
            points.append((round(x), round(y)))
        # Broken segments drift upward and taper, rather than opaque white tubes.
        phase = (time_seconds * 8 + strand * 11) % 23
        for i in range(len(points) - 1):
            fade = max(0, 1 - i / len(points))
            wave = .45 + .55 * (math.sin((i * 1.65 + phase) * .25) ** 2)
            alpha = round(80 * fade * wave)
            if alpha:
                draw.line([points[i], points[i + 1]], fill=(202, 215, 227, alpha), width=3)
    layer = layer.filter(ImageFilter.GaussianBlur(1.3))
    glow = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow, 'RGBA')
    intensity = .5 + .5 * math.sin(time_seconds * 3.1 + .5 * math.sin(time_seconds * 6.3))
    gd.ellipse((x0 - 4, y0 - 4, x0 + 4, y0 + 4), fill=(255, 93, 5, round(28 + 25 * intensity)))
    glow = glow.filter(ImageFilter.GaussianBlur(3))
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

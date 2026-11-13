"""Isolated trial: a hedgehog runs behind the robot and the sign on the table."""
import argparse
import math
from pathlib import Path
import subprocess

from PIL import Image, ImageChops, ImageDraw, ImageFilter

if __package__:
    from .render import FPS, SIZE, audio_levels, blink_at, make_frames, mouth_states
    from .smoke_layer_render import prepare_frames, frame_with_layers
else:
    from render import FPS, SIZE, audio_levels, blink_at, make_frames, mouth_states
    from smoke_layer_render import prepare_frames, frame_with_layers

HERE = Path(__file__).resolve().parent


def foreground_mask():
    """Restore the original sign and robot in front of the moving cutout."""
    mask = Image.new('L', (512, 512), 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon([(48, 191), (179, 190), (207, 309), (76, 320)], fill=255)
    # In the running corridor, this is the left edge of head/neck and shoulder.
    draw.polygon([(268, 207), (512, 207), (512, 512), (203, 512),
                  (203, 284), (222, 273), (252, 264), (274, 255),
                  (272, 231)], fill=255)
    return mask.filter(ImageFilter.GaussianBlur(.5)).resize((SIZE, SIZE), Image.Resampling.LANCZOS)


FOREGROUND = foreground_mask()


def make_run_frames(sprite_path, width=50, count=12):
    """Separate the four feet and swing them around their joints."""
    with Image.open(sprite_path) as source:
        sprite = source.convert('RGBA')
    if sprite.size != (480, 295) or sprite.getchannel('A').getextrema()[0] != 0:
        raise ValueError('Ожидается прозрачный исходный спрайт ёжика')
    polygons = [
        ([(73, 218), (107, 239), (75, 267), (40, 280), (0, 294), (0, 267), (32, 236)], (75, 230)),
        ([(131, 237), (162, 243), (205, 270), (205, 294), (163, 294), (136, 275)], (148, 240)),
        ([(270, 231), (298, 240), (283, 270), (257, 289), (231, 290), (221, 276), (244, 255)], (275, 237)),
        ([(306, 216), (341, 229), (400, 270), (405, 287), (375, 293), (330, 273), (300, 247)], (315, 225)),
    ]
    alpha = sprite.getchannel('A')
    removed = Image.new('L', sprite.size, 0)
    legs = []
    for points, pivot in polygons:
        mask = Image.new('L', sprite.size, 0)
        ImageDraw.Draw(mask).polygon(points, fill=255)
        removed = ImageChops.lighter(removed, mask)
        leg = sprite.copy()
        leg.putalpha(ImageChops.multiply(alpha, mask))
        legs.append((leg, pivot))
    body = sprite.copy()
    body.putalpha(ImageChops.subtract(alpha, removed))
    frames = []
    height = round(width * sprite.height / sprite.width)
    for index in range(count):
        phase = 2 * math.pi * index / count
        merged = Image.new('RGBA', sprite.size)
        for i, (leg, pivot) in enumerate(legs):
            angle = 17 * math.sin(phase + (i % 2) * math.pi)
            merged.alpha_composite(leg.rotate(angle, center=pivot, resample=Image.Resampling.BICUBIC))
        merged.alpha_composite(body)
        frames.append(merged.resize((width, height), Image.Resampling.LANCZOS))
    return frames


def run_position(seconds):
    """One rightward run, entering behind the poster and leaving behind the robot."""
    start, duration = 1.0, 3.2
    if not start <= seconds < start + duration:
        return None
    progress = (seconds - start) / duration
    scale = SIZE / 512
    return round((150 + 140 * progress) * scale), round(261 * scale)


def frame_with_hedgehog(frame, seconds, sprites):
    position = run_position(seconds)
    if position is None:
        return frame
    original = Image.frombytes('RGB', (SIZE, SIZE), frame).convert('RGBA')
    canvas = original.copy()
    sprite = sprites[int(seconds * 12) % len(sprites)]
    x, baseline = position
    bounce = round(1.2 * abs(math.sin(seconds * 12 * math.pi)))
    canvas.alpha_composite(sprite, (x, baseline - sprite.height - bounce))
    return Image.composite(original, canvas, FOREGROUND).convert('RGB').tobytes()


def render(audio, closed, opened, blink, plate, sprite, output):
    levels = audio_levels(audio)
    frames = prepare_frames(make_frames(closed, opened, blink), plate)
    sprites = make_run_frames(sprite)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
           '-s', f'{SIZE}x{SIZE}', '-r', str(FPS), '-i', 'pipe:0', '-i', str(audio),
           '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
           '-c:a', 'aac', '-b:a', '128k', '-shortest', '-movflags', '+faststart', str(output)]
    process = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i, state in enumerate(mouth_states(levels)):
            seconds = i / FPS
            frame = frame_with_layers(frames[state, blink_at(i)], seconds)
            process.stdin.write(frame_with_hedgehog(frame, seconds, sprites))
    finally:
        process.stdin.close()
        status = process.wait()
    if status:
        raise RuntimeError('FFmpeg не смог создать видео')
    print(f'Пробный кружок с ёжиком готов: {output}', flush=True)


def main():
    parser = argparse.ArgumentParser(description='Пробный кружок с ёжиком')
    for name in ('audio', 'closed', 'open', 'blink', 'plate', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--sprite', default=str(HERE / 'assets/hedgehog.png'))
    args = parser.parse_args()
    render(args.audio, args.closed, args.open, args.blink, args.plate, args.sprite, args.output)


if __name__ == '__main__':
    main()

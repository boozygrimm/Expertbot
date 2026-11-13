"""Experiment: clean patch behind separately animated smoke, outside the worker."""
import argparse
import math
from pathlib import Path
import subprocess

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

if __package__:
    from .render import FPS, SIZE, audio_levels, blink_at, make_frames, mouth_states
    from .smoke_render import EMBER_MASK
else:
    from render import FPS, SIZE, audio_levels, blink_at, make_frames, mouth_states
    from smoke_render import EMBER_MASK


def patch_mask():
    # Local replacement only; protect the head, chain, and cigar ember.
    mask = Image.new('L', (512, 512))
    draw = ImageDraw.Draw(mask)
    draw.line([(349, 57), (360, 85), (375, 103), (390, 120), (400, 143),
               (404, 167), (399, 188), (405, 207), (400, 233), (405, 255),
               (398, 278), (392, 295), (397, 316), (405, 337)],
              fill=255, width=38, joint='curve')
    mask = mask.filter(ImageFilter.GaussianBlur(5))
    protect = Image.new('L', (512, 512))
    draw = ImageDraw.Draw(protect)
    draw.polygon([(0, 0), (343, 0), (345, 80), (360, 95), (370, 110),
                  (376, 130), (380, 160), (383, 195), (392, 243),
                  (383, 266), (360, 282), (0, 282)], fill=255)
    draw.polygon([(398, 338), (416, 333), (424, 346), (412, 356),
                  (397, 348)], fill=255)
    return ImageChops.subtract(mask, protect.filter(ImageFilter.GaussianBlur(1))).resize(
        (SIZE, SIZE), Image.Resampling.LANCZOS)


PATCH_MASK = patch_mask()


def destination_mask():
    mask = PATCH_MASK.filter(ImageFilter.MaxFilter(31))
    protect = Image.new('L', (512, 512))
    draw = ImageDraw.Draw(protect)
    draw.polygon([(0, 0), (343, 0), (345, 80), (360, 95), (370, 110),
                  (376, 130), (380, 160), (383, 195), (392, 243),
                  (383, 266), (360, 282), (0, 282)], fill=255)
    draw.polygon([(398, 338), (416, 333), (424, 346), (412, 356),
                  (397, 348)], fill=255)
    protect = protect.filter(ImageFilter.GaussianBlur(1)).resize((SIZE, SIZE), Image.Resampling.LANCZOS)
    mask = ImageChops.subtract(mask, protect)
    # Dissolve smoke near the top and keep it inside the circular frame.
    circle = Image.new('L', (SIZE, SIZE))
    inset = SIZE * 39 / 512
    ImageDraw.Draw(circle).ellipse((inset, inset, SIZE - inset, SIZE - inset), fill=255)
    circle = circle.filter(ImageFilter.GaussianBlur(2))
    fade = Image.new('L', (1, SIZE))
    fade.putdata([max(0, min(255, round((y - SIZE * 48 / 512) * 255 /
                                        (SIZE * 25 / 512)))) for y in range(SIZE)])
    return ImageChops.multiply(ImageChops.multiply(mask, circle), fade.resize((SIZE, SIZE)))


SMOKE_DESTINATION = destination_mask()


def prepare_frames(frames, plate_path):
    with Image.open(plate_path) as source:
        if source.width != source.height:
            raise ValueError('Подложка должна быть квадратной')
        plate = source.convert('RGB').resize((SIZE, SIZE), Image.Resampling.LANCZOS)
    prepared = {}
    for key, pixels in frames.items():
        original = Image.frombytes('RGB', (SIZE, SIZE), pixels)
        base = Image.composite(plate, original, PATCH_MASK)
        # Estimate smoke opacity from the difference between the approved
        # original and the smoke-free plate, confined to the smoke corridor.
        difference = ImageChops.subtract(original.convert('L'), plate.convert('L'))
        highlights = difference.point(lambda v: max(0, min(160, (v - 5) * 3)))
        highlights = ImageChops.multiply(highlights, PATCH_MASK).filter(ImageFilter.GaussianBlur(.5))
        prepared[key] = base, highlights
    return prepared


def rising_smoke(highlights, time_seconds):
    """Advect overlapping smoke copies upward, crossfading at each reset.

    The source image is static, so two copies are staggered to keep the
    vertical movement continuous. A small stationary wisp at the cigar joins
    the rising layers to the ember.
    """
    travel = SIZE * 55 / 512
    period = 3.0
    combined = Image.new('L', (SIZE, SIZE))
    for phase_offset in (0.0, 0.5):
        phase = (time_seconds / period + phase_offset) % 1.0
        weight = math.sin(math.pi * phase) ** 2
        upward = travel * phase
        # PIL maps output coordinates back to input: positive y means the
        # pixels travel up on screen. Gentle horizontal sway is secondary.
        sideways = 1.5 * math.sin(2 * math.pi * phase)
        shifted = highlights.transform((SIZE, SIZE), Image.Transform.AFFINE,
                                       (1, 0, sideways, 0, 1, upward),
                                       resample=Image.Resampling.BICUBIC)
        combined = ImageChops.add(combined, shifted.point(lambda v: round(v * weight)))

    # Keep the lower end attached to the cigar while successive plumes rise.
    foot = Image.new('L', (1, SIZE))
    foot.putdata([max(0, min(255, round((y - SIZE * 300 / 512) * 255 /
                                        (SIZE * 25 / 512)))) for y in range(SIZE)])
    combined = ImageChops.lighter(combined, ImageChops.multiply(highlights, foot.resize((SIZE, SIZE))))
    # Keep the moving pixels away from the character, text and outer circle.
    return ImageChops.multiply(combined, SMOKE_DESTINATION)


def frame_with_layers(layers, time_seconds):
    base, highlights = layers
    moved = rising_smoke(highlights, time_seconds)
    smoke = Image.new('RGB', (SIZE, SIZE), (190, 185, 207))
    result = Image.composite(smoke, base, moved)
    flicker = .5 + .5 * math.sin(time_seconds * 3.1 + .5 * math.sin(time_seconds * 6.3))
    bright = ImageEnhance.Brightness(result).enhance(1 + .7 * flicker)
    return Image.composite(bright, result, EMBER_MASK).tobytes()


def render(audio, closed, opened, blink, plate_path, output):
    levels = audio_levels(audio)
    layers = prepare_frames(make_frames(closed, opened, blink), plate_path)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
           '-s', f'{SIZE}x{SIZE}', '-r', str(FPS), '-i', 'pipe:0', '-i', str(audio),
           '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
           '-c:a', 'aac', '-b:a', '128k', '-shortest', '-movflags', '+faststart', str(output)]
    process = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i, state in enumerate(mouth_states(levels)):
            process.stdin.write(frame_with_layers(layers[state, blink_at(i)], i / FPS))
    finally:
        process.stdin.close()
        status = process.wait()
    if status:
        raise RuntimeError('FFmpeg не смог создать видео')
    print(f'Видео с дымом готово: {output}', flush=True)


def main():
    parser = argparse.ArgumentParser(description='Пробный рендер дыма с отдельной подложкой')
    for name in ('audio', 'closed', 'open', 'blink', 'plate', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    render(args.audio, args.closed, args.open, args.blink, args.plate, args.output)


if __name__ == '__main__':
    main()

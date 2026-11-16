"""Isolated, silent television-transition sketch; no network or bot integration.

The supplied still is flattened, so the remote and characters are not truly
animated. Coordinates are specific to studio_mockup_corrected.png. The red
button pulse is a timing marker, not a physical finger press.
"""
from __future__ import annotations

import argparse
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
SIZE = (960, 540)
REFERENCE_SIZE = (1365, 768)
FPS = 24
DURATION = 10.5
PRESSES = (0.85, 4.75, 8.85)


def unit(value: float) -> float:
    return max(0.0, min(1.0, value))


def smooth(value: float) -> float:
    value = unit(value)
    return value * value * (3 - 2 * value)


def screen_visibility(t: float) -> float:
    """Brightness for a simple on / change of story / off timeline."""
    if t < 0.98:
        return 0.0
    if t < 1.48:
        return smooth((t - 0.98) / 0.50)
    if t < 4.85:
        return 1.0
    if t < 5.00:
        return 1.0 - smooth((t - 4.85) / 0.15)
    if t < 5.17:
        return smooth((t - 5.00) / 0.17)
    if t < 8.95:
        return 1.0
    if t < 9.48:
        return 1.0 - smooth((t - 8.95) / 0.53)
    return 0.0


def button_pulse(t: float) -> float:
    return max((unit(1 - abs(t - p) / 0.16) for p in PRESSES), default=0.0)


def story_label(t: float) -> tuple[str, float]:
    if 1.58 <= t < 4.45:
        return 'СЮЖЕТ 1', min(smooth((t - 1.58) / .3), smooth((4.45 - t) / .3))
    if 5.30 <= t < 8.55:
        return 'СЮЖЕТ 2', min(smooth((t - 5.30) / .3), smooth((8.55 - t) / .3))
    return '', 0.0


def point(x: float, y: float) -> tuple[int, int]:
    return (round(x * SIZE[0] / REFERENCE_SIZE[0]),
            round(y * SIZE[1] / REFERENCE_SIZE[1]))


def screen_mask() -> Image.Image:
    mask = Image.new('L', SIZE, 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon([point(*p) for p in [
        (175, 95), (255, 78), (1060, 75), (1144, 89), (1183, 125),
        (1197, 190), (1195, 475), (1180, 625), (1150, 674),
        (1060, 693), (320, 695), (188, 679), (155, 644),
        (146, 543), (145, 221), (153, 141),
    ]], fill=255)
    # Protect the foreground hand and remote, traced from this particular still.
    draw.polygon([point(*p) for p in [
        (984, 568), (1000, 560), (1026, 559), (1037, 556), (1040, 553),
        (1049, 553), (1051, 562), (1061, 562), (1074, 555), (1092, 559),
        (1110, 567), (1128, 580), (1149, 585), (1173, 599), (1210, 625),
        (1244, 650), (1276, 670), (1308, 704), (1365, 768), (1110, 768),
        (1092, 723), (1077, 685), (1051, 672), (1032, 668), (1015, 655),
        (1007, 634), (1000, 623), (994, 608), (982, 592),
    ]], fill=0)
    return mask.filter(ImageFilter.GaussianBlur(0.6))


def find_font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                 '/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf'):
        if Path(path).exists():
            return ImageFont.truetype(path, 17)
    return ImageFont.load_default()


def make_frame(base: Image.Image, mask: Image.Image, t: float, font: ImageFont.ImageFont) -> Image.Image:
    visibility = screen_visibility(t)
    off = Image.new('RGB', SIZE, (5, 10, 18))
    # A dim reflection prevents the powered-off screen looking like a hole.
    d = ImageDraw.Draw(off)
    d.ellipse((130, 48, 800, 340), fill=(8, 15, 25))
    inner = Image.blend(off, base, visibility)
    frame = Image.composite(inner, base, mask)

    # Brief CRT flash line on switch on/off, clipped to the screen.
    flash = 0.0
    for center, span in ((1.06, .11), (5.01, .09), (9.39, .12)):
        flash = max(flash, unit(1 - abs(t - center) / span))
    if flash:
        line = Image.new('RGBA', SIZE, (0, 0, 0, 0))
        ld = ImageDraw.Draw(line)
        y = point(0, 385)[1]
        x0, x1 = point(220, 0)[0], point(1140, 0)[0]
        ld.rounded_rectangle((x0, y - 2, x1, y + 2), radius=2,
                             fill=(168, 224, 255, round(155 * flash)))
        alpha = Image.composite(line.getchannel('A'), Image.new('L', SIZE, 0), mask)
        line.putalpha(alpha)
        frame = Image.alpha_composite(frame.convert('RGBA'), line).convert('RGB')

    label, opacity = story_label(t)
    if label:
        overlay = Image.new('RGBA', SIZE, (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        # Explicitly mark these as placeholders; no real news is presented.
        x, y = point(210, 590)
        d.rounded_rectangle((x, y, x + 112, y + 29), radius=5,
                            fill=(8, 19, 35, round(170 * opacity)))
        d.text((x + 9, y + 4), label, font=font, fill=(224, 238, 255, round(240 * opacity)))
        frame = Image.alpha_composite(frame.convert('RGBA'), overlay).convert('RGB')

    pulse = button_pulse(t)
    if pulse:
        glow = Image.new('RGBA', SIZE, (0, 0, 0, 0))
        d = ImageDraw.Draw(glow)
        x, y = point(1045, 560)
        radius = 4 + round(4 * pulse)
        d.ellipse((x-radius, y-radius, x+radius, y+radius), fill=(255, 50, 25, round(125*pulse)))
        glow = glow.filter(ImageFilter.GaussianBlur(3))
        d = ImageDraw.Draw(glow)
        d.ellipse((x-2, y-2, x+2, y+2), fill=(255, 180, 110, round(230*pulse)))
        frame = Image.alpha_composite(frame.convert('RGBA'), glow).convert('RGB')
    return frame


def render(source: Path, output: Path, ffmpeg: str) -> None:
    with Image.open(source) as image:
        if image.size != REFERENCE_SIZE:
            raise ValueError(f'Expected the {REFERENCE_SIZE} concept image, got {image.size}')
        base = image.convert('RGB').resize(SIZE, Image.Resampling.LANCZOS)
    mask, font = screen_mask(), find_font()
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
               '-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size', f'{SIZE[0]}x{SIZE[1]}',
               '-framerate', str(FPS), '-i', 'pipe:0', '-an', '-c:v', 'libx264',
               '-preset', 'medium', '-crf', '19', '-pix_fmt', 'yuv420p',
               '-movflags', '+faststart', str(output)]
    proc = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        assert proc.stdin is not None
        for frame in range(math.ceil(DURATION * FPS)):
            proc.stdin.write(make_frame(base, mask, frame / FPS, font).tobytes())
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError('ffmpeg failed')
    except BaseException:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
        proc.wait()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=HERE / 'studio_mockup_corrected.png')
    parser.add_argument('--output', type=Path, default=HERE / 'transition_trial.mp4')
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    render(args.source, args.output, args.ffmpeg)
    print(f'Готово: {args.output}')


if __name__ == '__main__':
    main()

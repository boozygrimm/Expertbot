"""Offline transition sketch with a tentative finger press and synthetic clicks.

The original scene is a flattened image. The finger is an approximate cutout,
not a real hand rig; the result is intentionally an isolated visual trial.
"""
from __future__ import annotations

import argparse
import math
import random
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

try:
    from . import transition_trial as scene
except ImportError:
    import transition_trial as scene

HERE = Path(__file__).resolve().parent
SAMPLE_RATE = 48000


def press_amount(t: float) -> float:
    """Reach towards the button, press at PRESSES, then lift back."""
    result = 0.0
    for at in scene.PRESSES:
        if at - 0.30 <= t < at:
            result = max(result, scene.smooth((t - (at - 0.30)) / 0.30))
        elif at <= t < at + 0.25:
            result = max(result, 1 - scene.smooth((t - at) / 0.25))
    return result


def finger_layer(base: Image.Image) -> Image.Image:
    """Mask just the tip and first knuckle of the foreground hand."""
    mask = Image.new('L', scene.SIZE, 0)
    d = ImageDraw.Draw(mask)
    d.polygon([scene.point(*p) for p in [
        (1052, 570), (1054, 565), (1061, 564), (1068, 561),
        (1075, 559), (1084, 561), (1088, 567), (1087, 574),
        (1081, 578), (1074, 579), (1066, 582), (1059, 581),
        (1053, 578),
    ]], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(0.65))
    layer = base.convert('RGBA')
    layer.putalpha(mask)
    return layer


def add_finger(frame: Image.Image, layer: Image.Image, amount: float) -> Image.Image:
    if amount <= 0:
        return frame
    # The still has no hidden background behind the finger: overlay the cutout
    # only for this small test, without pretending that this is a finished rig.
    dx = round(-9 * amount * scene.SIZE[0] / scene.REFERENCE_SIZE[0])
    dy = round(-5 * amount * scene.SIZE[1] / scene.REFERENCE_SIZE[1])
    moved = Image.new('RGBA', scene.SIZE, (0, 0, 0, 0))
    moved.alpha_composite(layer, dest=(dx, dy))
    return Image.alpha_composite(frame.convert('RGBA'), moved).convert('RGB')


def audio_samples() -> list[int]:
    """Three original synthesized mechanical clicks; no external audio asset."""
    samples = [0.0] * round(scene.DURATION * SAMPLE_RATE)
    rng = random.Random(817)
    for when in scene.PRESSES:
        for delay, level in ((0.0, 1.0), (0.17, 0.35)):
            start = round((when + delay) * SAMPLE_RATE)
            length = round(0.11 * SAMPLE_RATE)
            for i in range(length):
                t = i / SAMPLE_RATE
                # Dry tick plus a brief low metallic resonance.
                tick = rng.uniform(-1, 1) * math.exp(-t * 170)
                ring = math.sin(2 * math.pi * 1150 * t) * math.exp(-t * 48)
                thump = math.sin(2 * math.pi * 190 * t) * math.exp(-t * 35)
                value = level * (0.29 * tick + 0.10 * ring + 0.12 * thump)
                if start + i < len(samples):
                    samples[start + i] += value
    return [max(-32767, min(32767, round(value * 32767))) for value in samples]


def write_audio(path: Path) -> None:
    samples = audio_samples()
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(struct.pack(f'<{len(samples)}h', *samples))


def render(source: Path, output: Path, ffmpeg: str) -> None:
    with Image.open(source) as image:
        if image.size != scene.REFERENCE_SIZE:
            raise ValueError(f'Expected {scene.REFERENCE_SIZE}, got {image.size}')
        base = image.convert('RGB').resize(scene.SIZE, Image.Resampling.LANCZOS)
    mask, finger, font = scene.screen_mask(), finger_layer(base), scene.find_font()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='news-press-') as work:
        audio = Path(work) / 'clicks.wav'
        write_audio(audio)
        command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
                   '-f', 'rawvideo', '-pixel_format', 'rgb24',
                   '-video_size', f'{scene.SIZE[0]}x{scene.SIZE[1]}',
                   '-framerate', str(scene.FPS), '-i', 'pipe:0', '-i', str(audio),
                   '-c:v', 'libx264', '-preset', 'medium', '-crf', '19',
                   '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k',
                   '-movflags', '+faststart', str(output)]
        proc = subprocess.Popen(command, stdin=subprocess.PIPE)
        try:
            assert proc.stdin is not None
            for index in range(round(scene.DURATION * scene.FPS)):
                t = index / scene.FPS
                frame = scene.make_frame(base, mask, t, font)
                frame = add_finger(frame, finger, press_amount(t))
                proc.stdin.write(frame.tobytes())
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
    parser.add_argument('--output', type=Path, default=HERE / 'press_trial.mp4')
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    render(args.source, args.output, args.ffmpeg)
    print(f'Готово: {args.output}')


if __name__ == '__main__':
    main()

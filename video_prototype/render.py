"""Simple replaceable PNGTuber renderer; run with the Pillow virtual environment."""
import argparse
import array
import math
import subprocess
import sys
import wave
from pathlib import Path

FPS = 24
SIZE = 480


def audio_levels(path, fps=FPS):
    """RMS for each frame; the prototype accepts PCM 16-bit mono WAV only."""
    with wave.open(str(path), 'rb') as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != 'NONE':
            raise ValueError('Нужен WAV PCM 16 бит, моно')
        rate = wav.getframerate()
        count = wav.getnframes()
        if count == 0:
            raise ValueError('Пустой аудиофайл')
        if count / rate >= 60:
            raise ValueError('Аудио должно быть короче 60 секунд')
        samples = array.array('h', wav.readframes(count))
    if sys.byteorder != 'little':
        samples.byteswap()
    if not samples:
        raise ValueError('Пустой аудиофайл')
    levels = []
    for i in range(math.ceil(len(samples) * fps / rate)):
        center = int((i + 0.5) * rate / fps)
        radius = int(rate * 0.04)
        part = samples[max(0, center - radius):min(len(samples), center + radius)]
        levels.append(math.sqrt(sum(x * x for x in part) / len(part)) if part else 0)
    return levels


def mouth_states(levels):
    """The accepted first test: hysteresis driven by loudness, not phonemes."""
    peak = max(levels, default=0)
    opened = False
    for level in levels:
        if peak and opened and level < peak * 0.08:
            opened = False
        elif peak and not opened and level > peak * 0.12:
            opened = True
        yield opened


def blink_at(frame, fps=FPS):
    """Two brief deterministic blinks matching the manually approved test."""
    t = frame / fps
    return 1.3 <= t <= 1.46 or 4.0 <= t <= 4.16


def make_frames(closed_path, open_path, blink_path):
    from PIL import Image, ImageDraw, ImageFilter

    base = Image.open(closed_path).convert('RGB')
    opened = Image.open(open_path).convert('RGB')
    blink = Image.open(blink_path).convert('RGB')
    if base.size != opened.size or base.size != blink.size or base.width != base.height:
        raise ValueError('Все три исходные картинки должны быть квадратными и одного размера')
    scale = base.width / 2048

    def mask(box, radius, blur):
        layer = Image.new('L', base.size, 0)
        ImageDraw.Draw(layer).rounded_rectangle(
            tuple(round(v * scale) for v in box), radius=round(radius * scale), fill=255
        )
        return layer.filter(ImageFilter.GaussianBlur(blur * scale))

    mouth_mask = mask((1035, 775, 1505, 1115), 40, 20)
    eye_mask = mask((985, 600, 1460, 840), 40, 12)
    result = {}
    for mouth_open in (False, True):
        mouth = Image.composite(opened, base, mouth_mask) if mouth_open else base
        for eyes_closed in (False, True):
            frame = Image.composite(blink, mouth, eye_mask) if eyes_closed else mouth
            result[mouth_open, eyes_closed] = frame.resize(
                (SIZE, SIZE), Image.Resampling.LANCZOS
            ).tobytes()
    return result


def render(audio, closed, opened, blink, output):
    levels = audio_levels(audio)
    frames = make_frames(closed, opened, blink)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        'ffmpeg', '-y', '-loglevel', 'error',
        '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{SIZE}x{SIZE}',
        '-r', str(FPS), '-i', 'pipe:0', '-i', str(audio),
        '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '128k', '-shortest',
        '-movflags', '+faststart', str(output),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        for i, state in enumerate(mouth_states(levels)):
            process.stdin.write(frames[state, blink_at(i)])
    except BaseException:
        process.stdin.close()
        process.wait()
        raise
    process.stdin.close()
    if process.wait() != 0:
        raise RuntimeError('FFmpeg не смог создать видео')
    print(f'Видео готово: {output}', flush=True)


def main():
    parser = argparse.ArgumentParser()
    for name in ('audio', 'closed', 'open', 'blink', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    render(args.audio, args.closed, args.open, args.blink, args.output)


if __name__ == '__main__':
    main()

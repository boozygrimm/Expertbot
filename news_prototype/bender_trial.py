"""Isolated widescreen presenter trial driven by an existing mono PCM WAV.

No server, bot, Telegram or production configuration is changed. The generated
mouth and eyelid references are composited only in their respective face masks.
Animation follows audio loudness, not phonemes, as in the current studio.
"""
from __future__ import annotations

import argparse
import array
import math
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
REF_SIZE = (1365, 768)
SIZE = (960, 540)
FPS = 24
LEAD = 0.8
TAIL = 0.8


def read_pcm(path: Path) -> tuple[int, array.array]:
    with wave.open(str(path), 'rb') as source:
        rate, count = source.getframerate(), source.getnframes()
        if (source.getnchannels() != 1 or source.getsampwidth() != 2 or
                source.getcomptype() != 'NONE' or rate <= 0 or count <= 0 or count / rate >= 60):
            raise ValueError('Нужен непустой моно WAV PCM 16 бит короче 60 секунд')
        raw = source.readframes(count)
        if len(raw) != count * 2:
            raise ValueError('WAV повреждён или обрезан')
    samples = array.array('h')
    samples.frombytes(raw)
    if sys.byteorder != 'little':
        samples.byteswap()
    return rate, samples


def padded(samples: array.array, rate: int) -> array.array:
    return array.array('h', [0]) * round(rate * LEAD) + samples + array.array('h', [0]) * round(rate * TAIL)


def levels(samples: array.array, rate: int) -> list[float]:
    result = []
    radius = max(1, round(rate * .0125))
    for frame in range(math.ceil(len(samples) * FPS / rate)):
        center = int((frame + .5) * rate / FPS)
        part = samples[max(0, center - radius):min(len(samples), center + radius)]
        result.append(math.sqrt(sum(value * value for value in part) / len(part)) if part else 0)
    return result


def mouth_states(rms: list[float]) -> list[bool]:
    """Same hysteresis and single-frame suppression as the studio mouth trial."""
    peak = max(rms, default=0)
    active = sorted(value for value in rms if value > peak * .06)
    if not peak or not active:
        return [False] * len(rms)
    reference = active[round((len(active) - 1) * .75)]
    opening = max(peak * .10, reference * .50)
    closing = opening * .75
    current = False
    states = []
    for value in rms:
        if current and value < closing:
            current = False
        elif not current and value > opening:
            current = True
        states.append(current)
    original = states[:]
    for i in range(1, len(original) - 1):
        if original[i - 1] == original[i + 1] != original[i]:
            states[i] = original[i - 1]
    return states


def mask_for(box: tuple[int, int, int, int], radius: int) -> Image.Image:
    # Coordinates are measured on the 1365x768 selected studio still.
    scaled = tuple(round(v * (SIZE[0] / REF_SIZE[0] if i % 2 == 0 else SIZE[1] / REF_SIZE[1]))
                   for i, v in enumerate(box))
    mask = Image.new('L', SIZE, 0)
    ImageDraw.Draw(mask).rounded_rectangle(scaled, radius=radius, fill=255)
    return mask.filter(ImageFilter.GaussianBlur(.5))


def load_image(path: Path) -> Image.Image:
    with Image.open(path) as img:
        if img.size != REF_SIZE:
            raise ValueError(f'Неверный размер {path.name}: {img.size}; нужен {REF_SIZE}')
        return img.convert('RGB').resize(SIZE, Image.Resampling.LANCZOS)


def face_frames(opened: Image.Image, closed: Image.Image, blink: Image.Image) -> dict[tuple[bool, bool], bytes]:
    mouth = mask_for((397, 315, 493, 374), 8)
    eyes = mask_for((421, 251, 520, 301), 14)
    result = {}
    for is_open in (False, True):
        face = opened if is_open else Image.composite(closed, opened, mouth)
        for is_blink in (False, True):
            frame = Image.composite(blink, face, eyes) if is_blink else face
            result[is_open, is_blink] = frame.tobytes()
    return result


def is_blink(t: float) -> bool:
    return any(0 <= t - start < .17 for start in (1.8, 5.3, 8.8, 12.3, 15.8, 19.3, 22.8, 26.3, 29.8, 33.3, 36.8, 40.3, 43.8, 47.3, 50.8, 54.3, 57.8))


def write_wav(path: Path, samples: array.array, rate: int) -> None:
    little_endian = array.array('h', samples)
    if sys.byteorder != 'little':
        little_endian.byteswap()
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(little_endian.tobytes())


def render(audio: Path, output: Path, ffmpeg: str) -> None:
    rate, source = read_pcm(audio)
    samples = padded(source, rate)
    states = mouth_states(levels(samples, rate))
    frames = face_frames(load_image(HERE / 'studio_mockup_corrected.png'),
                         load_image(HERE / 'bender_closed_reference.png'),
                         load_image(HERE / 'bender_blink_reference.png'))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='news-presenter-') as directory:
        wav = Path(directory) / 'speech.wav'
        write_wav(wav, samples, rate)
        command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
                   '-f', 'rawvideo', '-pixel_format', 'rgb24',
                   '-video_size', f'{SIZE[0]}x{SIZE[1]}', '-framerate', str(FPS), '-i', 'pipe:0',
                   '-i', str(wav), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
                   '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart',
                   str(output)]
        process = subprocess.Popen(command, stdin=subprocess.PIPE)
        try:
            assert process.stdin is not None
            for index, state in enumerate(states):
                process.stdin.write(frames[state, is_blink(index / FPS)])
            process.stdin.close()
            if process.wait() != 0:
                raise RuntimeError('Не удалось создать видео')
        except BaseException:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            process.wait()
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audio', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    render(args.audio, args.output, args.ffmpeg)
    print(f'Проба готова: {args.output}', flush=True)


if __name__ == '__main__':
    main()

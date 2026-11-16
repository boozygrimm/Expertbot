"""Render a two-speaker visual trial from the previously saved podcast audio.

No new speech is synthesized. Each speaker's mouth is driven only by their own
saved utterances and their amplitude. Animation is not phoneme-based.
"""
from __future__ import annotations

import argparse
import array
from pathlib import Path
import subprocess
import sys
import tempfile
import wave

from PIL import Image

from news_prototype import bender_trial as bender
from news_prototype.podcast_audio_trial import GAP_MS, RATE, assemble_wav

HERE = Path(__file__).resolve().parent
SPEAKERS = ('Бендер', 'Киберсенсей', 'Бендер', 'Киберсенсей')


def read_wav(path: Path) -> tuple[int, array.array]:
    with wave.open(str(path), 'rb') as source:
        rate, count = source.getframerate(), source.getnframes()
        if (source.getnchannels(), source.getsampwidth(), rate, source.getcomptype()) != (1, 2, RATE, 'NONE') or count <= 0:
            raise ValueError('Нужен непустой моно WAV PCM 16 бит, 24 кГц')
        data = source.readframes(count)
        if len(data) != 2 * count:
            raise ValueError('Аудиофайл повреждён')
    samples = array.array('h')
    samples.frombytes(data)
    if sys.byteorder != 'little':
        samples.byteswap()
    return rate, samples


def dialogue_timeline(folder: Path) -> tuple[Path, list[str | None], list[bool]]:
    parts = [folder / f'{i}-normalized.wav' for i in range(1, 5)]
    audio = folder / 'dialogue.wav'
    counts = [len(read_wav(path)[1]) for path in parts]
    rate, joined = read_wav(audio)
    # Reconstruct the reference and compare PCM samples, not WAV headers.
    with tempfile.TemporaryDirectory(prefix='dialogue-verify-') as temporary:
        rebuilt = Path(temporary) / 'joined.wav'
        assemble_wav(parts, rebuilt)
        _, expected = read_wav(rebuilt)
    if joined != expected:
        raise ValueError('Реплики не совпадают с сохранённым общим аудио')
    spans = []
    cursor = 0
    for idx, count in enumerate(counts):
        spans.append((cursor, cursor + count, SPEAKERS[idx]))
        cursor += count
        if idx < len(counts) - 1:
            cursor += round(rate * GAP_MS / 1000)
    rms = bender.levels(joined, rate)
    active = []
    for index in range(len(rms)):
        midpoint = int((index + .5) * rate / bender.FPS)
        active.append(next((name for start, end, name in spans if start <= midpoint < end), None))
    # Normalize each utterance separately so a quieter voice is not suppressed.
    states = [False] * len(active)
    i = 0
    while i < len(active):
        if active[i] is None:
            i += 1
            continue
        end = i + 1
        while end < len(active) and active[end] == active[i]:
            end += 1
        states[i:end] = bender.mouth_states(rms[i:end])
        i = end
    return audio, active, states


def prepare_frames() -> dict[tuple[bool, bool, bool], bytes]:
    opened = bender.load_image(HERE / 'studio_mockup_soft.png')
    closed = bender.load_image(HERE / 'bender_soft_closed.png')
    blink = bender.load_image(HERE / 'bender_blink_reference.png')
    sensei_open = bender.load_image(HERE / 'sensei_open_subtle.png')
    bender_frames = bender.face_frames(opened, closed, blink, (390, 308, 498, 380))
    sensei_mask = bender.mask_for((835, 342, 878, 378), 6)
    frames = {}
    for is_open in (False, True):
        for is_blink in (False, True):
            base = Image.frombytes('RGB', bender.SIZE, bender_frames[is_open, is_blink])
            for sensei_talking in (False, True):
                image = Image.composite(sensei_open, base, sensei_mask) if sensei_talking else base
                frames[is_open, is_blink, sensei_talking] = image.tobytes()
    return frames


def render(folder: Path, output: Path, ffmpeg: str) -> None:
    audio, active, states = dialogue_timeline(folder)
    frames = prepare_frames()
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
               '-f', 'rawvideo', '-pixel_format', 'rgb24',
               '-video_size', f'{bender.SIZE[0]}x{bender.SIZE[1]}', '-framerate', str(bender.FPS), '-i', 'pipe:0',
               '-i', str(audio), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
               '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(output)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        assert process.stdin is not None
        for i, (speaker, state) in enumerate(zip(active, states)):
            speaking_bender = speaker == 'Бендер' and state
            speaking_sensei = speaker == 'Киберсенсей' and state
            data = frames[speaking_bender, bender.is_blink(i / bender.FPS), speaking_sensei]
            process.stdin.write(data)
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
    parser.add_argument('--dialogue-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    render(args.dialogue_dir, args.output, args.ffmpeg)
    print(f'Видеопроба готова: {args.output}', flush=True)


if __name__ == '__main__':
    main()

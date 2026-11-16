"""Isolated fly-in-the-sensei-head video using the saved podcast audio."""
from __future__ import annotations

import argparse
from functools import lru_cache
from pathlib import Path
import subprocess

from PIL import Image

from news_prototype import bender_trial as bender
from news_prototype import podcast_video_trial as podcast
from news_prototype import sensei_hatch as hatch


def event_window(active: list[str | None], fps: int = bender.FPS) -> tuple[float, float, float] | None:
    """Prefer the third spoken turn; fit one opening to the available clip."""
    if not active or fps <= 0:
        return None
    changes = []
    last = None
    for index, speaker in enumerate(active):
        if speaker is not None and speaker != last:
            changes.append(index / fps)
            last = speaker
    duration = len(active) / fps
    start = (changes[2] + .15) if len(changes) >= 3 else duration * .35
    start = min(start, max(0.0, duration - .5))
    available = max(0.0, duration - start - .1)
    if available < .3:
        return None
    motion = min(.6, available / 3)
    hold = min(2.2, max(0.0, available - 2 * motion))
    return start, hold, motion


def render(folder: Path, output: Path, ffmpeg: str) -> None:
    audio, active, states = podcast.dialogue_timeline(folder)
    frames = podcast.prepare_frames()
    assets = hatch.prepare()
    window = event_window(active)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
               '-f', 'rawvideo', '-pixel_format', 'rgb24',
               '-video_size', f'{bender.SIZE[0]}x{bender.SIZE[1]}', '-framerate', str(bender.FPS), '-i', 'pipe:0',
               '-i', str(audio), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
               '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(output)]

    @lru_cache(maxsize=25)
    def overlay(step: int) -> Image.Image:
        return hatch.overlay_at_fraction(assets, step / 24)

    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        assert process.stdin is not None
        for index, (speaker, state) in enumerate(zip(active, states)):
            timestamp = index / bender.FPS
            data = frames[speaker == 'Бендер' and state, bender.is_blink(timestamp),
                          speaker == 'Киберсенсей' and state]
            amount = hatch.openness(timestamp, window[0], window[1], window[2]) if window else 0.0
            if amount > 0:
                image = Image.frombytes('RGB', bender.SIZE, data).convert('RGBA')
                data = Image.alpha_composite(image, overlay(round(amount * 24))).convert('RGB').tobytes()
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
    print(f'Проба мухи готова: {args.output}', flush=True)


if __name__ == '__main__':
    main()

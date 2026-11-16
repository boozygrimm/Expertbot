"""Isolated framed-hedgehog video using the saved podcast audio."""
from __future__ import annotations

import argparse
from functools import lru_cache
from pathlib import Path
import subprocess

from PIL import Image

from news_prototype import bender_trial as bender
from news_prototype import podcast_video_trial as podcast
from news_prototype import hedgehog_motion as hedgehog


def render(folder: Path, output: Path, ffmpeg: str) -> None:
    audio, active, states = podcast.dialogue_timeline(folder)
    frames = podcast.prepare_frames()
    assets = hedgehog.prepare()
    duration = len(active) / bender.FPS
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
               '-f', 'rawvideo', '-pixel_format', 'rgb24',
               '-video_size', f'{bender.SIZE[0]}x{bender.SIZE[1]}', '-framerate', str(bender.FPS), '-i', 'pipe:0',
               '-i', str(audio), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
               '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(output)]

    @lru_cache(maxsize=32)
    def overlay(nod: int, blink: int) -> Image.Image:
        return hedgehog.overlay(assets, nod / 24, blink / 8)

    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        assert process.stdin is not None
        for index, (speaker, state) in enumerate(zip(active, states)):
            timestamp = index / bender.FPS
            data = frames[speaker == 'Бендер' and state, bender.is_blink(timestamp),
                          speaker == 'Киберсенсей' and state]
            nod, blink = hedgehog.gesture(timestamp, duration)
            nod_step, blink_step = round(nod * 24), round(blink * 8)
            if nod_step or blink_step:
                image = Image.frombytes('RGB', bender.SIZE, data).convert('RGBA')
                data = Image.alpha_composite(image, overlay(nod_step, blink_step)).convert('RGB').tobytes()
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
    print(f'Проба ежа готова: {args.output}', flush=True)


if __name__ == '__main__':
    main()

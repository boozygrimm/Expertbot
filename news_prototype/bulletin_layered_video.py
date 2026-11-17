"""Layered bulletin renderer used by the experimental versioned worker.

Pictures are displayed behind separately composited animated presenters. Speech
is already prepared and verified by the caller; no providers or services are used.
The timing budget retains the existing fixed intro/tail but no old-scene effects.
"""
from __future__ import annotations

from array import array
from bisect import bisect_right
from functools import lru_cache
from pathlib import Path
import subprocess
import tempfile

from PIL import Image
from news_prototype import (bender_trial, bulletin_animation, bulletin_wide_screen, combined_trial, desk_ticker,
                            podcast_video_trial)


def section_index(windows, timestamp: float) -> int | None:
    """Keep the preceding story during gaps; clear screen before/after dialogue."""
    if not windows or timestamp < windows[0].start or timestamp >= windows[-1].end:
        return None
    index = bisect_right([window.start for window in windows], timestamp) - 1
    return index if index >= 0 else None


def write_sound(audio: Path, output: Path, dialogue_frames: int) -> None:
    """Pad original PCM with frame-aligned silence, without modifying speech."""
    rate, samples = podcast_video_trial.read_wav(audio)
    fps = bender_trial.FPS
    if rate % fps or len(samples) > dialogue_frames * (rate // fps):
        raise ValueError('Диалог длиннее таймлайна')
    frame_samples = rate // fps
    padded = array('h', [0]) * ((combined_trial.INTRO_FRAMES + dialogue_frames + combined_trial.TAIL_FRAMES) * frame_samples)
    start = combined_trial.INTRO_FRAMES * frame_samples
    padded[start:start + len(samples)] = samples
    bender_trial.write_wav(output, padded, rate)


def render(output: Path, ffmpeg: str, *, sections: list[dict], windows: list,
           timeline: tuple) -> None:
    """Render one continuous timeline. Caller validates sections and duration."""
    if len(sections) != len(windows) or not sections:
        raise ValueError('Разделы и таймлайн не совпадают')
    audio, active, states = timeline
    if not active or len(active) != len(states):
        raise ValueError('Неверный таймлайн диалога')
    if [section['id'] for section in sections] != [window.section_id for window in windows]:
        raise ValueError('Порядок разделов не совпадает')
    layers = bulletin_animation.frames()
    tickers = [desk_ticker.prepare(section['ticker']) for section in sections]

    @lru_cache(maxsize=8)
    def frame_bytes(index: int | None, state: tuple[bool, bool, bool]) -> bytes:
        image = sections[index].get('image_b64') if index is not None else None
        return bulletin_wide_screen.compose(image, speaker_layers=layers[state]).tobytes()

    fps = bender_trial.FPS
    with tempfile.TemporaryDirectory(prefix='bulletin-layered-') as tmp:
        sound = Path(tmp) / 'sound.wav'
        write_sound(audio, sound, len(active))
        command = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                   '-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size', '960x540',
                   '-framerate', str(fps), '-i', 'pipe:0', '-i', str(sound),
                   '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
                   '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(output)]
        process = subprocess.Popen(command, stdin=subprocess.PIPE)
        try:
            assert process.stdin is not None
            for n in range(combined_trial.INTRO_FRAMES + len(active) + combined_trial.TAIL_FRAMES):
                t = (n + .5) / fps
                local = n - combined_trial.INTRO_FRAMES
                speaker, speaking = (active[local], states[local]) if 0 <= local < len(active) else (None, False)
                state = (speaker == 'Бендер' and speaking,
                         bulletin_animation.is_blink(n / fps),
                         speaker == 'Киберсенсей' and speaking)
                index = section_index(windows, t)
                data = frame_bytes(index, state)
                if index is not None:
                    frame = Image.frombytes('RGB', bulletin_wide_screen.SIZE, data)
                    data = desk_ticker.apply(frame, tickers[index], t - windows[index].start).tobytes()
                process.stdin.write(data)
            process.stdin.close()
            if process.wait() != 0:
                raise RuntimeError('Не удалось создать видео')
        except BaseException:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            process.wait()
            raise

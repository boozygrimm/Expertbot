"""Layered bulletin renderer used by the experimental versioned worker.

Pictures are displayed behind separately composited animated presenters. Speech
is already prepared and verified by the caller; no providers or services are used.
The timing budget retains the existing fixed intro/tail. A quiet remote click
is added only in verified silent gaps between news items.
"""
from __future__ import annotations

from array import array
from bisect import bisect_right
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
import math
import random
import subprocess
import tempfile

from PIL import Image
from news_prototype import (bender_trial, bulletin_animation, bulletin_remote_animation, bulletin_wide_screen,
                            combined_trial, desk_ticker, podcast_video_trial)


def section_index(windows, timestamp: float) -> int | None:
    """Keep the preceding story during gaps; clear screen before/after dialogue."""
    if not windows or timestamp < windows[0].start or timestamp >= windows[-1].end:
        return None
    index = bisect_right([window.start for window in windows], timestamp) - 1
    return index if index >= 0 else None


def click_positions(windows, rate: int) -> list[int]:
    """Select a spot in each inter-news gap, just before the next image."""
    intro = combined_trial.INTRO_FRAMES / bender_trial.FPS
    click_length = round(rate * .055)
    before = round(rate * .075)
    result = []
    for previous, following in zip(windows, windows[1:]):
        gap_start = round((previous.end - intro) * rate)
        gap_end = round((following.start - intro) * rate)
        position = gap_end - before
        if 0 <= gap_start <= position and position + click_length <= gap_end:
            result.append(position)
    return result


def remote_click(rate: int) -> array:
    """A short, quiet, deterministic two-part tactile button click."""
    rng = random.Random(617)
    result = array('h')
    for i in range(round(rate * .055)):
        t = i / rate
        value = 0.0
        for at, strength in ((.002, 1.0), (.025, .48)):
            age = t - at
            if 0 <= age < .015:
                envelope = math.exp(-age / .003)
                value += strength * envelope * (.55 * rng.uniform(-1, 1) + .45 * math.sin(2 * math.pi * 2100 * age))
        result.append(round(3500 * value))
    return result


def write_sound(audio: Path, output: Path, dialogue_frames: int, *, clicks: Sequence[int] = ()) -> None:
    """Use the original power-click audio, adding transitions only in silence."""
    rate, speech = podcast_video_trial.read_wav(audio)
    with tempfile.TemporaryDirectory(prefix='bulletin-legacy-sound-') as tmp:
        legacy = Path(tmp) / 'legacy.wav'
        combined_trial.write_sound(audio, legacy, dialogue_frames)
        legacy_rate, padded = podcast_video_trial.read_wav(legacy)
    start = combined_trial.INTRO_FRAMES * (rate // bender_trial.FPS)
    if legacy_rate != rate or padded[start:start + len(speech)] != speech:
        raise ValueError('Исходный эффект изменил речь')
    effect = remote_click(rate)
    for position in clicks:
        if (not isinstance(position, int) or isinstance(position, bool) or position < 0
                or position + len(effect) > len(speech) or any(speech[position:position + len(effect)])):
            raise ValueError('Щелчок пересекается с речью или находится вне диалога')
        padded[start + position:start + position + len(effect)] = effect
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
    rate = podcast_video_trial.RATE
    clicks = click_positions(windows, rate)
    transition_times = [(combined_trial.INTRO_FRAMES / fps) + position / rate + .002 for position in clicks]
    off_time, _ = combined_trial.timing(len(active))
    screen_mask = bulletin_remote_animation.television_mask()
    off_screen = bulletin_remote_animation.unlit_screen()
    with tempfile.TemporaryDirectory(prefix='bulletin-layered-') as tmp:
        sound = Path(tmp) / 'sound.wav'
        write_sound(audio, sound, len(active), clicks=clicks)
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
                # Retain the last picture during the closing animation.
                if index is None and t >= windows[-1].end:
                    index = len(sections) - 1
                data = frame_bytes(index, state)
                # The old animation used frame start rather than midpoint for its effects.
                effect_t = n / fps
                power_active = (effect_t < 1.48 or effect_t >= off_time + .1
                                or abs(effect_t - combined_trial.ON_PRESS) < .16
                                or abs(effect_t - off_time) < .16)
                amount = max(combined_trial.finger_amount(effect_t, (combined_trial.ON_PRESS, off_time)),
                             bulletin_remote_animation.press_amount(t, transition_times))
                transition_active = any(abs(t - at) < .16 for at in transition_times)
                if index is not None or amount > 0 or power_active or transition_active:
                    frame = Image.frombytes('RGB', bulletin_wide_screen.SIZE, data)
                    if index is not None and t < windows[-1].end:
                        frame = desk_ticker.apply(frame, tickers[index], t - windows[index].start)
                    if power_active:
                        frame = combined_trial.display(frame, screen_mask, off_screen, effect_t, off_time)
                    if amount > 0:
                        frame = bulletin_remote_animation.apply(frame, amount)
                    if transition_active:
                        frame = bulletin_remote_animation.transition_glow(frame, t, transition_times)
                    data = frame.tobytes()
                process.stdin.write(data)
            process.stdin.close()
            if process.wait() != 0:
                raise RuntimeError('Не удалось создать видео')
        except BaseException:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            process.wait()
            raise

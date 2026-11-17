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


def power_click_positions(dialogue_frames: int, rate: int) -> tuple[int, int]:
    """Absolute PCM positions in the intro and after the frame-aligned speech."""
    fps = bender_trial.FPS
    return round(.85 * rate), round(((combined_trial.INTRO_FRAMES + dialogue_frames) / fps + .5) * rate)


def write_sound(audio: Path, output: Path, dialogue_frames: int, *, clicks: Sequence[int] = (),
                power_clicks: Sequence[int] = ()) -> None:
    """Pad PCM and place clicks only over verified silence, never over speech."""
    rate, samples = podcast_video_trial.read_wav(audio)
    fps = bender_trial.FPS
    if rate % fps or len(samples) > dialogue_frames * (rate // fps):
        raise ValueError('Диалог длиннее таймлайна')
    effect = remote_click(rate)
    for position in clicks:
        if (not isinstance(position, int) or isinstance(position, bool) or position < 0
                or position + len(effect) > len(samples) or any(samples[position:position + len(effect)])):
            raise ValueError('Щелчок пересекается с аудио или находится вне диалога')
    frame_samples = rate // fps
    padded = array('h', [0]) * ((combined_trial.INTRO_FRAMES + dialogue_frames + combined_trial.TAIL_FRAMES) * frame_samples)
    start = combined_trial.INTRO_FRAMES * frame_samples
    padded[start:start + len(samples)] = samples
    for position in clicks:
        padded[start + position:start + position + len(effect)] = effect
    for position in power_clicks:
        if (not isinstance(position, int) or isinstance(position, bool) or position < 0
                or position + len(effect) > len(padded)
                or not (position + len(effect) <= start or position >= start + len(samples))
                or any(padded[position:position + len(effect)])):
            raise ValueError('Щелчок включения пересекается со звуком')
        padded[position:position + len(effect)] = effect
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

    @lru_cache(maxsize=24)
    def frame_bytes(index: int | None, state: tuple[bool, bool, bool], brightness: int) -> bytes:
        image = sections[index].get('image_b64') if index is not None else None
        frame = bulletin_wide_screen.compose(image, speaker_layers=layers[state])
        return bulletin_remote_animation.apply_television(frame, brightness / 12).tobytes()

    fps = bender_trial.FPS
    rate = podcast_video_trial.RATE
    clicks = click_positions(windows, rate)
    power_clicks = power_click_positions(len(active), rate)
    on_time, off_time = (position / rate + .002 for position in power_clicks)
    click_times = [on_time, *[(combined_trial.INTRO_FRAMES / fps) + position / rate + .002 for position in clicks], off_time]
    with tempfile.TemporaryDirectory(prefix='bulletin-layered-') as tmp:
        sound = Path(tmp) / 'sound.wav'
        write_sound(audio, sound, len(active), clicks=clicks, power_clicks=power_clicks)
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
                brightness = round(12 * bulletin_remote_animation.screen_level(t, on_time, off_time))
                data = frame_bytes(index, state, brightness)
                amount = bulletin_remote_animation.press_amount(t, click_times)
                effect_active = (abs(t - on_time - .21) < .11 or abs(t - off_time - .54) < .12
                                 or any(abs(t - at) < .16 for at in click_times))
                if index is not None or amount > 0 or effect_active:
                    frame = Image.frombytes('RGB', bulletin_wide_screen.SIZE, data)
                    if index is not None and t < windows[-1].end:
                        frame = desk_ticker.apply(frame, tickers[index], t - windows[index].start)
                    if amount > 0:
                        frame = bulletin_remote_animation.apply(frame, amount)
                    if effect_active:
                        frame = bulletin_remote_animation.power_effect(frame, t, on_time, off_time, click_times)
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

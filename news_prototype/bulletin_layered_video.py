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
import threading

from PIL import Image, ImageOps
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
           timeline: tuple, video: dict | None = None, videos: list[dict] | None = None,
           extra_clicks: Sequence[int] = ()) -> None:
    """Render reviewed media with one continuous decoder per video, sequentially.

    No source download, TTS, delivery or publication occurs here. Video entries
    identify their sections, starting frames and fullscreen ranges; positions
    are absolute in the output including the intro.
    """
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
    clicks = sorted(click_positions(windows, rate) + list(extra_clicks))
    transition_times = [(combined_trial.INTRO_FRAMES / fps) + position / rate + .002 for position in clicks]
    off_time, _ = combined_trial.timing(len(active))
    screen_mask = bulletin_remote_animation.television_mask()
    off_screen = bulletin_remote_animation.unlit_screen()
    total_frames = combined_trial.INTRO_FRAMES + len(active) + combined_trial.TAIL_FRAMES
    if video is not None and videos is not None:
        raise ValueError('Неверные видеовставки')
    videos = [video] if video is not None else (videos or [])
    frame_video = {}
    seen = set()
    for item in videos:
        if (not isinstance(item, dict) or set(item) != {'section_id', 'path', 'start', 'fullscreen'}
                or item['section_id'] not in [s['id'] for s in sections] or item['section_id'] in seen
                or type(item['start']) is not int or not isinstance(item['fullscreen'], tuple)
                or len(item['fullscreen']) != 2 or any(type(x) is not int for x in item['fullscreen'])):
            raise ValueError('Неверная видеовставка')
        seen.add(item['section_id'])
        source = Path(item['path'])
        if source.is_symlink() or not source.is_file() or output.exists() or output.is_symlink():
            raise ValueError('Недопустимый источник или результат')
        if total_frames > 300 * fps:
            raise ValueError('Выпуск превышает пять минут')
        video_index = next(i for i, s in enumerate(sections) if s['id'] == item['section_id'])
        frames = [n for n in range(combined_trial.INTRO_FRAMES, combined_trial.INTRO_FRAMES + len(active))
                  if n >= item['start'] and section_index(windows, (n + .5) / fps) == video_index]
        first, last = item['fullscreen']
        if (not frames or item['start'] != frames[0] or not 0 <= first < last <= total_frames
                or not set(range(first, last)).issubset(frames) or any(n in frame_video for n in frames)):
            raise ValueError('Видеовставка выходит за границы новости')
        for n in frames:
            frame_video[n] = (item, len(frames), n == frames[-1])
    if videos:
        from news_prototype.telegram_public_source import media_env
        transparent = Image.new('RGBA', bulletin_wide_screen.SIZE)
        video_background = bulletin_wide_screen.compose(speaker_layers=(transparent, transparent)).convert('RGBA')
    with tempfile.TemporaryDirectory(prefix='bulletin-layered-') as tmp:
        sound = Path(tmp) / 'sound.wav'
        write_sound(audio, sound, len(active), clicks=clicks)
        command = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                   '-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size', '960x540',
                   '-framerate', str(fps), '-i', 'pipe:0', '-i', str(sound),
                   '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
                   '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(output)]
        decoder = None
        def start_decoder(item, count):
            decoder_cmd = [ffmpeg, '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                           '-format_whitelist', 'mov,mp4,m4a,3gp,3g2,mj2', '-stream_loop', '-1',
                           '-i', str(item['path']), '-map', '0:v:0', '-an', '-sn', '-dn', '-vf',
                           f'scale=704:396:force_original_aspect_ratio=decrease,'
                           f'pad=704:396:(ow-iw)/2:(oh-ih)/2:color=black,fps={fps}:start_time=0',
                           '-frames:v', str(count), '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1']
            return subprocess.Popen(decoder_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=media_env())
        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                       env=media_env() if videos else None)
        except BaseException:
            if decoder is not None:
                decoder.kill(); decoder.wait()
            raise
        def stop_stalled():
            for proc in (decoder, process):
                if proc is not None and proc.poll() is None:
                    try: proc.kill()
                    except OSError: pass
        watchdog = threading.Timer(1800, stop_stalled)
        watchdog.daemon = True
        watchdog.start()
        try:
            assert process.stdin is not None
            for n in range(total_frames):
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
                video_frame = None
                current = frame_video.get(n)
                if current:
                    item, count, last_frame = current
                    if decoder is None:
                        decoder = start_decoder(item, count)
                    assert decoder.stdout is not None
                    pixels = decoder.stdout.read(704 * 396 * 3)
                    if len(pixels) != 704 * 396 * 3:
                        raise ValueError('Неполный кадр исходного видео')
                    video_frame = Image.frombytes('RGB', (704, 396), pixels)
                    if last_frame:
                        decoder.stdout.close()
                        if decoder.wait(timeout=60) != 0:
                            raise RuntimeError('Не удалось декодировать видео источника')
                        decoder = None
                    fitted = ImageOps.contain(video_frame, (456, 212), Image.Resampling.LANCZOS)
                    panel = Image.new('RGB', (456, 212), (18, 53, 94))
                    panel.paste(fitted, ((456 - fitted.width) // 2, (212 - fitted.height) // 2))
                    scene = video_background.copy()
                    x0, y0, _, _ = bulletin_wide_screen.SCREEN
                    scene.paste(panel, (x0 + 4, y0 + 4))
                    for layer in layers[state]:
                        scene.alpha_composite(layer)
                    data = scene.convert('RGB').tobytes()
                else:
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
                    if video_frame is not None and item['fullscreen'][0] <= n < item['fullscreen'][1]:
                        screen = Image.new('RGB', bulletin_wide_screen.SIZE)
                        screen.paste(video_frame, (110, 56 + (419 - 396) // 2))
                        frame = Image.composite(screen, frame, screen_mask)
                    if power_active:
                        frame = combined_trial.display(frame, screen_mask, off_screen, effect_t, off_time)
                    if amount > 0:
                        frame = bulletin_remote_animation.apply(frame, amount)
                    if transition_active:
                        frame = bulletin_remote_animation.transition_glow(frame, t, transition_times)
                    data = frame.tobytes()
                process.stdin.write(data)
            process.stdin.close()
            if decoder is not None:
                decoder.stdout.close()
                if decoder.wait(timeout=60) != 0:
                    raise RuntimeError('Не удалось декодировать видео источника')
            if process.wait(timeout=120) != 0:
                raise RuntimeError('Не удалось создать видео')
        except BaseException:
            stop_stalled()
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            if decoder is not None:
                decoder.wait()
            process.wait()
            raise
        finally:
            watchdog.cancel()

"""One-off owner preview of the approved Telegram story; not a worker pipeline.

One joint two-speaker TTS request uses the existing key rotation. The complete
source video plays and repeats without source audio. Turn boundaries are pause
heuristics, not diarization; spoken words must be checked by the owner.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

from PIL import Image, ImageOps
from news_prototype import (bender_trial, bulletin_animation,
    bulletin_dialogue_audio, bulletin_duo_content_probe, bulletin_full_video_trial,
    bulletin_layered_video, bulletin_preflight, bulletin_remote_animation,
    bulletin_timing, bulletin_video_insert_trial, bulletin_wide_screen,
    combined_trial, desk_ticker, hedgehog_motion, podcast_audio_trial, podcast_video_trial,
    sensei_hatch, sensei_hatch_trial, story_audio)
from video_prototype import gemini_tts

SOURCE = bulletin_video_insert_trial.SOURCE
SOURCE_SIZE = bulletin_video_insert_trial.SOURCE_SIZE
BASE = Path('/data/video-prototype/telegram-story-full-7266-1')
TICKER = 'ЗВЁЗДНАЯ НОЧЬ ПРЕВРАТИЛАСЬ В ИГРУ'
TEXT = [
    'Земляне, срочные культурные новости! Картину Ван Гога превратили в игру. Наконец-то есть картина, внутри которой можно кататься на велосипеде — и охрана не гонится!',
    'Игра называется «Старри Найтрайдер». Она работает в браузере и позволяет прокатиться по трёхмерному миру, вдохновлённому «Звёздной ночью».',
    'То есть я буквально въезжаю в искусство? Наконец-то картина, достаточно большая для моего великолепия.',
    'Можно собирать светящиеся сферы и доставлять их к колокольне. А ещё — прыгать, выполнять трюки и летать.',
    'Летать? Отлично! Если меня попросят платить за парковку, я просто улечу в небо.',
    'Разработчик Эбби Амир пишет, что создал игру с помощью модели Fable пять точка два.',
    'С помощью ИИ? Вот это правильный подход: пусть машины работают, пока… Погоди-ка. Это же я машина!',
    'Не волнуйся, Бендер. Искусственный интеллект здесь — инструмент разработчика, а не повод вычеркнуть человека из истории.',
    'Ладно, признаю: идея красивая. Но если на следующей картине появятся рекламные щиты, я требую процент!',
    'А пока можно самому открыть игру в браузере и посмотреть, как знаменитая картина превращается в пространство для прогулки.',
]


def script():
    return story_audio.validate_script({'ticker': TICKER, 'turns': [
        {'speaker': 'Бендер' if i % 2 == 0 else 'Киберсенсей', 'text': text}
        for i, text in enumerate(TEXT)]})


def source_duration(source: Path, ffprobe: str) -> float:
    command = [ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe',
               '-format_whitelist', 'mov,mp4,m4a,3gp,3g2,mj2',
               '-select_streams', 'v:0', '-show_entries', 'stream=duration:format=duration',
               '-of', 'json', str(source)]
    result = subprocess.run(command, check=True, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=30, env=podcast_audio_trial.normalized_env())
    if len(result.stdout) > 10_000:
        raise ValueError('Неверные сведения о видео')
    metadata = json.loads(result.stdout)
    stream = metadata['streams'][0]
    try:
        duration = float(stream['duration'])
    except (KeyError, TypeError, ValueError):
        duration = float(metadata['format']['duration'])
    if not math.isfinite(duration) or not 0 < duration <= 290:
        raise ValueError('Неверная длительность видео')
    return duration


def prepare_audio(data: dict, folder: Path, ffmpeg: str, keys: list[str]) -> Path:
    """One whole-dialogue request; reuse the established key rotation."""
    folder.mkdir(mode=0o700, parents=False, exist_ok=False)
    body = bulletin_dialogue_audio.request_body(data)
    raw, audio = folder / 'raw.wav', folder / 'dialogue.wav'
    def send(key, _unused, timeout):
        # Full dialogue can take longer than the short two-line previews.
        return bulletin_dialogue_audio.request_one(key, body, max(timeout, 120))
    gemini_tts.synthesize('', raw, keys, request_fn=send, budget=240)
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-i', str(raw), '-ac', '1',
                    '-ar', str(podcast_audio_trial.RATE), '-c:a', 'pcm_s16le', str(audio)],
                   check=True, timeout=120, env=podcast_audio_trial.normalized_env(),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rate, samples = podcast_video_trial.read_wav(audio)
    if not any(samples) or len(samples) / rate < 25:
        raise ValueError('Аудио подозрительно короткое или пустое; проверь вручную')
    return audio


def estimate_boundaries(levels: list[float], turns: list[dict]) -> list[int]:
    """Fit ordered speech turns to silence and text lengths; not diarization."""
    fps = bender_trial.FPS
    if len(turns) < 2 or len(levels) < len(turns):
        raise ValueError('Недостаточно звука для диалога')
    peak = max(levels)
    if peak <= 0:
        raise ValueError('Пустой диалог')
    threshold = min(peak * .15, max(35., peak * .035))
    gaps, start = [], None
    for i in range(len(levels) + 1):
        quiet = i < len(levels) and levels[i] <= threshold
        if quiet and start is None:
            start = i
        elif not quiet and start is not None:
            if i - start >= round(.16 * fps) and 0 < start and i < len(levels):
                gaps.append(((start + i) // 2, (i - start) / fps))
            start = None
    # If pauses are too scarce, use estimated positions; never alter speech.
    weights = [max(1, len(t['text'])) for t in turns]
    total = sum(weights)
    targets = [round(len(levels) * sum(weights[:i]) / total) for i in range(1, len(turns))]
    if len(gaps) < len(targets):
        return targets
    # Dynamic program: prefer plausible per-turn lengths and longer pauses.
    # Restrict candidates near their expected position, to bound work and avoid
    # implausible turn assignments. Fallback remains a heuristic, not a claim.
    candidates = [[(pos, dur) for pos, dur in gaps
                   if abs(pos - target) <= max(3 * fps, int(len(levels) * .12))]
                  for target in targets]
    if any(not row for row in candidates):
        return targets
    previous = {0: (0.0, [])}
    for index, row in enumerate(candidates):
        expected = len(levels) * weights[index] / total
        current = {}
        for pos, gap in row:
            options = [(cost + ((pos - prior - expected) / max(expected, fps)) ** 2
                        - min(gap, .8) * .3, path + [pos])
                       for prior, (cost, path) in previous.items() if pos - prior >= fps * .3]
            if options:
                current[pos] = min(options, key=lambda item: item[0])
        if not current:
            return targets
        previous = current
    expected = len(levels) * weights[-1] / total
    return min(previous.items(), key=lambda item: item[1][0] +
               ((len(levels) - item[0] - expected) / max(expected, fps)) ** 2)[1][1]


def build_timeline(audio: Path, data: dict, duration: float):
    rate, samples = podcast_video_trial.read_wav(audio)
    levels = bender_trial.levels(samples, rate)
    boundaries = estimate_boundaries(levels, data['turns'])
    active, states = [], []
    edges = [0] + boundaries + [len(levels)]
    for turn, begin, end in zip(data['turns'], edges, edges[1:]):
        active.extend([turn['speaker']] * (end - begin))
        states.extend(bender_trial.mouth_states(levels[begin:end]))
    # Source starts with dialogue and plays to its final frame, even if the
    # generated speech is shorter. Silence extends the picture, never speech.
    frames = max(len(active), math.ceil(duration * bender_trial.FPS))
    active.extend([None] * (frames - len(active)))
    states.extend([False] * (frames - len(states)))
    total = (combined_trial.INTRO_FRAMES + frames + combined_trial.TAIL_FRAMES) / bender_trial.FPS
    if total > 300:
        raise bulletin_timing.BulletinTooLong(f'Видео и речь с заставками занимают {total:.1f} с')
    return (audio, active, states), boundaries, total


@lru_cache(maxsize=1)
def gesture_assets():
    return sensei_hatch.prepare(), hedgehog_motion.prepare()


@lru_cache(maxsize=8)
def hatch_frame(step: int):
    old = sensei_hatch.overlay_at_fraction(gesture_assets()[0], step / 24)
    # Map the original hatch onto the smaller head in the wide-screen studio.
    return old.transform(bulletin_wide_screen.SIZE, Image.Transform.AFFINE,
                         (1/.875, 0, 573 - 585/.875, 0, 1/1.25, 148 - 165/1.25),
                         Image.Resampling.BICUBIC)


@lru_cache(maxsize=8)
def hedgehog_frame(nod: int, blink: int):
    return hedgehog_motion.overlay(gesture_assets()[1], nod / 24, blink / 8)


def render(source: Path, output: Path, ffmpeg: str, timeline: tuple, *, fullscreen=None, clicks=(),
           continuous_video=False):
    """Show video in studio; optionally replace the outer TV picture for a clip.

    fullscreen is a pair of frame indices relative to the start of the audio.
    clicks are exact sample offsets in the supplied audio, and must lie in silence.
    continuous_video keeps one video stream across the two screen sizes.
    """
    audio, active, states = timeline
    if not active or len(active) != len(states):
        raise ValueError('Неверный таймлайн')
    if fullscreen is not None and (not isinstance(fullscreen, tuple) or len(fullscreen) != 2
            or any(not isinstance(x, int) or isinstance(x, bool) for x in fullscreen)
            or not 0 <= fullscreen[0] < fullscreen[1] <= len(active)):
        raise ValueError('Неверные границы видео на телевизоре')
    if source.is_symlink() or not source.is_file() or output.exists() or output.is_symlink():
        raise ValueError('Недопустимый источник или результат')
    fps = bender_trial.FPS
    count = combined_trial.INTRO_FRAMES + len(active) + combined_trial.TAIL_FRAMES
    if count / fps > 300:
        raise ValueError('Ролик превышает пять минут')
    width, height = bulletin_video_insert_trial.PANEL_SIZE
    decode_width, decode_height = (704, 396) if continuous_video else (width, height)
    env = podcast_audio_trial.normalized_env()
    layers = bulletin_animation.frames()
    transparent = Image.new('RGBA', bulletin_wide_screen.SIZE)
    background = bulletin_wide_screen.compose(speaker_layers=(transparent, transparent)).convert('RGBA')
    ticker = desk_ticker.prepare(TICKER)
    mask, off = bulletin_remote_animation.television_mask(), bulletin_remote_animation.unlit_screen()
    off_time, _ = combined_trial.timing(len(active))
    with tempfile.TemporaryDirectory(prefix='telegram-podcast-') as tmp:
        sound = Path(tmp) / 'sound.wav'
        bulletin_layered_video.write_sound(audio, sound, len(active), clicks=clicks)
        rate = podcast_audio_trial.RATE
        transition_times = [(combined_trial.INTRO_FRAMES / fps) + pos / rate + .002 for pos in clicks]
        # Play the complete source first, then loop it while the dialogue lasts.
        # The source audio is never mapped. The TV turns on before playback.
        decoder = [ffmpeg, '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                   '-format_whitelist', 'mov,mp4,m4a,3gp,3g2,mj2', '-stream_loop', '-1', '-i', str(source),
                   '-map', '0:v:0', '-an', '-sn', '-dn', '-vf',
                   f'scale={decode_width}:{decode_height}:force_original_aspect_ratio=decrease,'
                   f'pad={decode_width}:{decode_height}:(ow-iw)/2:(oh-ih)/2:color='
                   f'{"black" if continuous_video else "0x12355e"},'
                   f'fps={fps}{":start_time=0" if continuous_video else ""}',
                   '-frames:v', str(len(active)), '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1']
        encoder = [ffmpeg, '-nostdin', '-v', 'error', '-y', '-f', 'rawvideo', '-pixel_format', 'rgb24',
                   '-video_size', '960x540', '-framerate', str(fps), '-i', 'pipe:0', '-i', str(sound),
                   '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
                   '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(output)]
        dec = subprocess.Popen(decoder, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
        full = None
        try:
            if fullscreen is not None and not continuous_video:
                # Preserve the original aspect ratio, filling the outer TV as
                # far as possible without cropping away source video content.
                full_cmd = [ffmpeg, '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                            '-format_whitelist', 'mov,mp4,m4a,3gp,3g2,mj2', '-i', str(source),
                            '-map', '0:v:0', '-an', '-sn', '-dn', '-vf',
                            f'scale=704:419:force_original_aspect_ratio=decrease,'
                            f'pad=704:419:(ow-iw)/2:(oh-ih)/2:color=black,'
                            f'fps={fps}:start_time=0,tpad=stop_mode=clone:stop_duration=300',
                            '-frames:v', str(fullscreen[1] - fullscreen[0]),
                            '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1']
                full = subprocess.Popen(full_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
            enc = subprocess.Popen(encoder, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
        except BaseException:
            for proc in (dec, full):
                if proc is not None:
                    proc.kill(); proc.wait()
            raise
        def stop():
            for proc in (dec, full, enc):
                if proc is None:
                    continue
                if proc.poll() is None:
                    try: proc.kill()
                    except OSError: pass
        watchdog = threading.Timer(1800, stop); watchdog.daemon = True; watchdog.start()
        try:
            assert dec.stdout is not None and enc.stdin is not None
            last_data = None
            hatch_window = sensei_hatch_trial.event_window(active)
            for n in range(count):
                local = n - combined_trial.INTRO_FRAMES
                if n == 0 or 1 <= local < len(active):
                    last_data = dec.stdout.read(decode_width * decode_height * 3)
                data = last_data
                if data is None or len(data) != decode_width * decode_height * 3:
                    raise ValueError('Неполный кадр источника')
                video_frame = Image.frombytes('RGB', (decode_width, decode_height), data)
                if continuous_video:
                    fitted = ImageOps.contain(video_frame, (width, height), Image.Resampling.LANCZOS)
                    panel = Image.new('RGB', (width, height), (18, 53, 94))
                    panel.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
                else:
                    panel = video_frame
                frame = background.copy()
                x0, y0, _, _ = bulletin_wide_screen.SCREEN
                frame.paste(panel, (x0 + 4, y0 + 4))
                speaker, speaking = (active[local], states[local]) if 0 <= local < len(active) else (None, False)
                state = (speaker == 'Бендер' and speaking, bulletin_animation.is_blink(n / fps),
                         speaker == 'Киберсенсей' and speaking)
                bender, sensei = layers[state]
                frame.alpha_composite(bender); frame.alpha_composite(sensei)
                if 0 <= local < len(active):
                    local_t = local / fps
                    amount = sensei_hatch.openness(local_t, *hatch_window) if hatch_window else 0
                    step = round(amount * 24)
                    if step:
                        frame.alpha_composite(hatch_frame(step))
                    nod, blink = hedgehog_motion.gesture(local_t, len(active) / fps)
                    ns, bs = round(nod * 24), round(blink * 8)
                    if ns or bs:
                        frame.alpha_composite(hedgehog_frame(ns, bs))
                frame = frame.convert('RGB')
                t = n / fps
                if 0 <= local < len(active):
                    frame = desk_ticker.apply(frame, ticker, local / fps)
                if fullscreen is not None and fullscreen[0] <= local < fullscreen[1]:
                    screen = Image.new('RGB', bulletin_wide_screen.SIZE)
                    if continuous_video:
                        # The exact same decoded frame used for the studio panel.
                        screen.paste(video_frame, (110, 56 + (419 - decode_height) // 2))
                    else:
                        assert full is not None and full.stdout is not None
                        pixels = full.stdout.read(704 * 419 * 3)
                        if len(pixels) != 704 * 419 * 3:
                            raise ValueError('Неполный кадр полноэкранного видео')
                        screen.paste(Image.frombytes('RGB', (704, 419), pixels), (110, 56))
                    frame = Image.composite(screen, frame, mask)
                if t < 1.48 or t >= off_time + .1 or abs(t - combined_trial.ON_PRESS) < .16 or abs(t - off_time) < .16:
                    frame = combined_trial.display(frame, mask, off, t, off_time)
                amount = max(combined_trial.finger_amount(t, (combined_trial.ON_PRESS, off_time)),
                             bulletin_remote_animation.press_amount(t, transition_times))
                if amount > 0:
                    frame = bulletin_remote_animation.apply(frame, amount)
                if any(abs(t - at) < .16 for at in transition_times):
                    frame = bulletin_remote_animation.transition_glow(frame, t, transition_times)
                enc.stdin.write(frame.tobytes())
            dec.stdout.close()
            if full is not None:
                full.stdout.close()
            enc.stdin.close()
            dec_status = dec.wait(timeout=60)
            full_status = full.wait(timeout=60) if full is not None else 0
            enc_status = enc.wait(timeout=120)
            if dec_status or full_status or enc_status:
                raise RuntimeError('Не удалось создать видео')
        except BaseException:
            stop(); dec.wait()
            if full is not None: full.wait()
            enc.wait()
            raise
        finally:
            watchdog.cancel()


def run(base: Path, source: Path, ffmpeg: str):
    if source.is_symlink() or not source.is_file() or source.stat().st_size != SOURCE_SIZE:
        raise ValueError('Исходное видео не найдено или изменилось')
    if bulletin_preflight.check(ffmpeg):
        raise ValueError('Не пройдена предварительная проверка')
    env = bulletin_duo_content_probe.read_worker_env()
    keys = bulletin_duo_content_probe.worker_keys(env)
    if not keys:
        raise ValueError('Нет ключей TTS')
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        raise ValueError('Не найден ffprobe')
    duration = source_duration(source, ffprobe)
    data = script()
    base.mkdir(mode=0o700, parents=False, exist_ok=False)
    (base / 'script.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Исходное видео: {duration:.1f} с. Один общий двухголосый запрос TTS.', flush=True)
    audio = prepare_audio(data, base / 'audio', ffmpeg, keys)
    timeline, boundaries, total = build_timeline(audio, data, duration)
    print('Приблизительные смены ведущих (сек): ' + ', '.join(f'{x / bender_trial.FPS:.1f}' for x in boundaries), flush=True)
    print(f'Создаю ролик: {total:.1f} с, полное видео без звука источника.', flush=True)
    partial, output = base / 'video.partial.mp4', base / 'video.mp4'
    render(source, partial, ffmpeg, timeline)
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i', str(partial), '-f', 'null', '-'],
                   check=True, timeout=180, env=podcast_audio_trial.normalized_env(),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    partial.replace(output)
    print(f'Видео сохранено: {output}', flush=True)
    print(bulletin_full_video_trial.send_video_once(output, base, env,
          caption='Полная новость: видео поста без исходного звука; синхронизация ведущих приблизительная.'), flush=True)



def main():
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        print('Не найден ffmpeg; ничего не запущено.')
        return 1
    try:
        run(BASE, SOURCE, ffmpeg)
        return 0
    except FileExistsError:
        print('Проба уже запускалась. Не повторяй: проверь папку и личку.')
    except (gemini_tts.TTSFailure, bulletin_duo_content_probe.ProbeError) as exc:
        print('Проба остановлена:', str(exc), 'Не повторяй вслепую; сохрани вывод.')
    except Exception:
        print('Проба остановлена. Не повторяй вслепую; сохрани вывод для диагностики.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())

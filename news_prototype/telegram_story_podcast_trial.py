"""One-off owner preview of the approved Telegram story; not a worker pipeline.

Five two-speaker TTS requests use the existing key rotation. The first 12 s of
previously viewed video repeat, without source audio. Turn boundaries are pause
heuristics, not diarization; spoken words must be checked by the owner.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

from PIL import Image
from news_prototype import (bender_trial, bulletin_animation, bulletin_contract,
    bulletin_dialogue_audio, bulletin_duo_content_probe, bulletin_full_video_trial,
    bulletin_layered_video, bulletin_preflight, bulletin_remote_animation,
    bulletin_timing, bulletin_video_insert_trial, bulletin_wide_screen,
    combined_trial, desk_ticker, podcast_audio_trial)
from video_prototype import gemini_tts

SOURCE = bulletin_video_insert_trial.SOURCE
SOURCE_SIZE = bulletin_video_insert_trial.SOURCE_SIZE
BASE = Path('/data/video-prototype/telegram-story-podcast-7266-1')
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
    # Separate pairs are technical TTS chunks, not five separate news items.
    return bulletin_contract.validate_bulletin({'sections': [
        {'id': i + 1, 'ticker': TICKER, 'turns': [
            {'speaker': 'Бендер', 'text': TEXT[2 * i]},
            {'speaker': 'Киберсенсей', 'text': TEXT[2 * i + 1]}]}
        for i in range(5)]})


def render(source: Path, output: Path, ffmpeg: str, timeline: tuple, *, clip_seconds=12):
    """Mute/loop the approved excerpt, add dialogue and original TV power effect."""
    audio, active, states = timeline
    if not active or len(active) != len(states) or not 1 <= clip_seconds <= 12:
        raise ValueError('Неверный таймлайн')
    if source.is_symlink() or not source.is_file() or output.exists() or output.is_symlink():
        raise ValueError('Недопустимый источник или результат')
    fps = bender_trial.FPS
    count = combined_trial.INTRO_FRAMES + len(active) + combined_trial.TAIL_FRAMES
    if count / fps > 300:
        raise ValueError('Ролик превышает пять минут')
    width, height = bulletin_video_insert_trial.PANEL_SIZE
    env = podcast_audio_trial.normalized_env()
    layers = bulletin_animation.frames()
    transparent = Image.new('RGBA', bulletin_wide_screen.SIZE)
    background = bulletin_wide_screen.compose(speaker_layers=(transparent, transparent)).convert('RGBA')
    ticker = desk_ticker.prepare(TICKER)
    mask, off = bulletin_remote_animation.television_mask(), bulletin_remote_animation.unlit_screen()
    off_time, _ = combined_trial.timing(len(active))
    with tempfile.TemporaryDirectory(prefix='telegram-podcast-') as tmp:
        clip, sound = Path(tmp) / 'clip.mp4', Path(tmp) / 'sound.wav'
        # Map video only, so the source's audio can never enter the output.
        subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                        '-format_whitelist', 'mov,mp4,m4a,3gp,3g2,mj2', '-i', str(source),
                        '-map', '0:v:0', '-an', '-sn', '-dn', '-t', str(clip_seconds),
                        '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,'
                               f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x12355e,fps={fps}',
                        '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p', str(clip)],
                       check=True, timeout=120, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        bulletin_layered_video.write_sound(audio, sound, len(active))
        decoder = [ffmpeg, '-nostdin', '-v', 'error', '-stream_loop', '-1', '-i', str(clip),
                   '-map', '0:v:0', '-an', '-frames:v', str(count), '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1']
        encoder = [ffmpeg, '-nostdin', '-v', 'error', '-y', '-f', 'rawvideo', '-pixel_format', 'rgb24',
                   '-video_size', '960x540', '-framerate', str(fps), '-i', 'pipe:0', '-i', str(sound),
                   '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
                   '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(output)]
        dec = subprocess.Popen(decoder, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
        try:
            enc = subprocess.Popen(encoder, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
        except BaseException:
            dec.kill(); dec.wait()
            raise
        def stop():
            for proc in (dec, enc):
                if proc.poll() is None:
                    try: proc.kill()
                    except OSError: pass
        watchdog = threading.Timer(1800, stop); watchdog.daemon = True; watchdog.start()
        try:
            assert dec.stdout is not None and enc.stdin is not None
            for n in range(count):
                data = dec.stdout.read(width * height * 3)
                if len(data) != width * height * 3:
                    raise ValueError('Неполный кадр источника')
                frame = background.copy()
                x0, y0, _, _ = bulletin_wide_screen.SCREEN
                frame.paste(Image.frombytes('RGB', (width, height), data), (x0 + 4, y0 + 4))
                local = n - combined_trial.INTRO_FRAMES
                speaker, speaking = (active[local], states[local]) if 0 <= local < len(active) else (None, False)
                state = (speaker == 'Бендер' and speaking, bulletin_animation.is_blink(n / fps),
                         speaker == 'Киберсенсей' and speaking)
                bender, sensei = layers[state]
                frame.alpha_composite(bender); frame.alpha_composite(sensei)
                frame = frame.convert('RGB')
                t = n / fps
                if 0 <= local < len(active):
                    frame = desk_ticker.apply(frame, ticker, local / fps)
                if t < 1.48 or t >= off_time + .1 or abs(t - combined_trial.ON_PRESS) < .16 or abs(t - off_time) < .16:
                    frame = combined_trial.display(frame, mask, off, t, off_time)
                amount = combined_trial.finger_amount(t, (combined_trial.ON_PRESS, off_time))
                if amount > 0:
                    frame = bulletin_remote_animation.apply(frame, amount)
                enc.stdin.write(frame.tobytes())
            dec.stdout.close(); enc.stdin.close()
            if dec.wait(timeout=60) != 0 or enc.wait(timeout=120) != 0:
                raise RuntimeError('Не удалось создать видео')
        except BaseException:
            stop(); dec.wait(); enc.wait()
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
    data = script()
    base.mkdir(mode=0o700, parents=False, exist_ok=False)
    (base / 'script.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Готовлю пять двухголосых частей; используется существующая ротация ключей.', flush=True)
    audio, parts = bulletin_dialogue_audio.prepare(data, base / 'audio', ffmpeg, keys)
    _, total = bulletin_timing.section_windows(parts)
    timeline, estimates = bulletin_full_video_trial.build_timeline(audio, parts, data['sections'])
    for i, estimate in enumerate(estimates, 1):
        print(f'Часть {i}: {estimate}', flush=True)
    print(f'Создаю ролик: {total:.1f} с, без звука источника.', flush=True)
    partial, output = base / 'video.partial.mp4', base / 'video.mp4'
    render(source, partial, ffmpeg, timeline)
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i', str(partial), '-f', 'null', '-'],
                   check=True, timeout=180, env=podcast_audio_trial.normalized_env(),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    partial.replace(output)
    print(f'Видео сохранено: {output}', flush=True)
    print(bulletin_full_video_trial.send_video_once(output, base, env,
          caption='Пробная новость: видео поста без исходного звука; смена ведущих приблизительная. Проверь текст на слух.'), flush=True)


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

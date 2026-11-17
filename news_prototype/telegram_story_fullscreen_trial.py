"""Owner-only trial: switch the outer TV to the post video with original audio.

Uses an already generated dialogue and the saved MP4. Speech finishes before
switching to the entire post; the remote then returns to the studio. No TTS,
queue, database, service changes or publication. Output is a fresh one-shot
folder and source assets are never modified.
"""
from __future__ import annotations

from array import array
import json
import math
from pathlib import Path
import shutil
import subprocess

from news_prototype import (bender_trial, bulletin_duo_content_probe,
    bulletin_full_video_trial, bulletin_preflight, bulletin_timing, combined_trial,
    podcast_audio_trial, podcast_video_trial, telegram_story_podcast_trial as story)

BASE = story.BASE
SOURCE = story.SOURCE
FOLDER = 'video-fullscreen-1'
GAP_FRAMES = 18
RETURN_HOLD_FRAMES = 2


class NoSourceAudio(ValueError):
    pass


def has_audio(source: Path, ffprobe: str) -> bool:
    result = subprocess.run([ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe',
                             '-format_whitelist', 'mov,mp4,m4a,3gp,3g2,mj2',
                             '-select_streams', 'a:0', '-show_entries', 'stream=codec_type',
                             '-of', 'json', str(source)], check=True, timeout=30,
                            env=podcast_audio_trial.normalized_env(),
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if len(result.stdout) > 10_000:
        raise ValueError('Некорректные метаданные аудио')
    return any(s.get('codec_type') == 'audio' for s in json.loads(result.stdout).get('streams', []))


def extract_audio(source: Path, output: Path, ffmpeg: str) -> None:
    # Reset timestamps relative to the input start and preserve any initial
    # silence, so the audio and decoded video begin at the same point.
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                    '-format_whitelist', 'mov,mp4,m4a,3gp,3g2,mj2', '-i', str(source),
                    '-map', '0:a:0', '-vn', '-sn', '-dn', '-af', 'aresample=async=1:first_pts=0',
                    '-ac', '1', '-ar', str(podcast_audio_trial.RATE), '-c:a', 'pcm_s16le', str(output)],
                   check=True, timeout=180, env=podcast_audio_trial.normalized_env(),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _, samples = podcast_video_trial.read_wav(output)
    if not any(samples):
        raise NoSourceAudio('Аудиодорожка исходного видео оказалась полностью беззвучной')


def build_audio_and_timeline(dialogue: Path, clip_audio: Path, output: Path, duration: float, data: dict):
    """Place untrimmed dialogue and post audio in separate, frame-aligned spans."""
    (old_audio, active, states), _, _ = story.build_timeline(dialogue, data, 0)
    rate, speech = podcast_video_trial.read_wav(old_audio)
    clip_rate, clip = podcast_video_trial.read_wav(clip_audio)
    fps = bender_trial.FPS
    if rate != clip_rate or rate % fps or not any(speech) or not any(clip):
        raise ValueError('Неверная озвучка или звук видео')
    per_frame = rate // fps
    film_start = len(active) + GAP_FRAMES
    clip_frames = max(math.ceil(duration * fps), math.ceil(len(clip) / per_frame))
    film_end = film_start + clip_frames + RETURN_HOLD_FRAMES
    total_frames = film_end + GAP_FRAMES
    total = (combined_trial.INTRO_FRAMES + total_frames + combined_trial.TAIL_FRAMES) / fps
    if total > 300:
        raise bulletin_timing.BulletinTooLong(f'Проба целиком занимает {total:.1f} с; максимум 300 с')
    audio = array('h', [0]) * (total_frames * per_frame)
    audio[:len(speech)] = speech
    clip_start = film_start * per_frame
    audio[clip_start:clip_start + len(clip)] = clip
    bender_trial.write_wav(output, audio, rate)
    # First click is shortly before the video begins, second on return; both
    # land in verified silence, never over either recording.
    clicks = (clip_start - round(.09 * rate), film_end * per_frame)
    if any(any(audio[pos:pos + round(.055 * rate)]) for pos in clicks):
        raise ValueError('Щелчок пересекается со звуком')
    active.extend([None] * (total_frames - len(active)))
    states.extend([False] * (total_frames - len(states)))
    return (output, active, states), (film_start, film_end), clicks, total


def run(base: Path, source: Path, ffmpeg: str, ffprobe: str) -> Path:
    if base.is_symlink() or not base.is_dir():
        raise ValueError('Нет папки прежнего подкаста')
    snapshot, audio_dir = base / 'script.json', base / 'audio'
    dialogue = audio_dir / 'dialogue.wav'
    if (snapshot.is_symlink() or not snapshot.is_file() or snapshot.stat().st_size > 100_000
            or audio_dir.is_symlink() or not audio_dir.is_dir()
            or dialogue.is_symlink() or not dialogue.is_file()):
        raise ValueError('Нет сохранённого сценария или озвучки')
    data = json.loads(snapshot.read_text(encoding='utf-8'))
    if data != story.script():
        raise ValueError('Сценарий не совпадает с предыдущим выпуском')
    if source.is_symlink() or not source.is_file() or source.stat().st_size != story.SOURCE_SIZE:
        raise ValueError('Не найден исходный MP4 поста')
    if bulletin_preflight.check(ffmpeg):
        raise ValueError('Не пройдена предварительная проверка')
    duration = story.source_duration(source, ffprobe)
    if not has_audio(source, ffprobe):
        raise NoSourceAudio('В исходном MP4 нет аудиодорожки; включить нечего')
    folder = base / FOLDER
    folder.mkdir(mode=0o700, exist_ok=False)
    print(f'Исходное видео {duration:.1f} с: аудиодорожка найдена. Использую сохранённый диалог.', flush=True)
    clip_audio, mix = folder / 'source-audio.wav', folder / 'timeline.wav'
    extract_audio(source, clip_audio, ffmpeg)
    timeline, fullscreen, clicks, total = build_audio_and_timeline(dialogue, clip_audio, mix, duration, data)
    print(f'Полная проба: {total:.1f} с; диалог, затем видео со звуком, затем студия.', flush=True)
    partial, output = folder / 'video.partial.mp4', folder / 'video.mp4'
    story.render(source, partial, ffmpeg, timeline, fullscreen=fullscreen, clicks=clicks)
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i', str(partial), '-f', 'null', '-'],
                   check=True, timeout=240, env=podcast_audio_trial.normalized_env(),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    partial.replace(output)
    print(f'Готово: {output}', flush=True)
    env = bulletin_duo_content_probe.read_worker_env()
    print(bulletin_full_video_trial.send_video_once(output, folder, env,
          caption='Проба: пульт переключает внешний телевизор на видео поста с его звуком и обратно.'), flush=True)
    return output


def main() -> int:
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        print('Не найден ffmpeg или ffprobe; ничего не запущено.')
        return 1
    try:
        run(BASE, SOURCE, ffmpeg, ffprobe)
        return 0
    except NoSourceAudio as exc:
        print(str(exc), 'Ролик не отправлен.')
    except FileExistsError:
        print('Проба уже запускалась. Проверь папку и личку; не повторяй вслепую.')
    except bulletin_timing.BulletinTooLong as exc:
        print(str(exc), 'Речь и видео не обрезаны; рендер не запускался.')
    except Exception:
        print('Проба остановлена. Пришли вывод и не повторяй запуск вслепую.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())

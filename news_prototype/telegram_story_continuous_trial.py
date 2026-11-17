"""Rerender the owner overlay trial with one continuous video timeline.

Uses the previous dialogue, the already generated two-comment WAV, and the
previously extracted source audio. No TTS request, queue, service, database or
publication. The previous PCM outside the video insertion must match exactly.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

from news_prototype import (bulletin_duo_content_probe, bulletin_full_video_trial,
    bulletin_preflight, podcast_audio_trial, podcast_video_trial,
    telegram_story_overlay_trial as overlay, telegram_story_podcast_trial as story)

BASE = story.BASE
SOURCE = story.SOURCE
FOLDER = 'video-overlay-continuous-1'


class TimingMismatch(ValueError):
    pass


def regular_file(path: Path, limit: int | None = None) -> None:
    if path.is_symlink() or not path.is_file() or (limit is not None and path.stat().st_size > limit):
        raise ValueError('Не найден или повреждён сохранённый файл пробы')


def run(base: Path, source: Path, ffmpeg: str, ffprobe: str) -> Path:
    if base.is_symlink() or not base.is_dir():
        raise ValueError('Нет папки подкаста')
    snapshot, audio_dir = base / 'script.json', base / 'audio'
    previous = base / overlay.FOLDER
    comments_dir = previous / 'comments'
    if any(path.is_symlink() or not path.is_dir() for path in (audio_dir, previous, comments_dir)):
        raise ValueError('Нет сохранённых файлов предыдущей пробы')
    dialogue = audio_dir / 'dialogue.wav'
    comments = comments_dir / 'comments.wav'
    comments_script = previous / 'comments.json'
    old_timeline = previous / 'timeline.wav'
    source_audio = previous / 'source-audio.wav'
    for path in (dialogue, comments, old_timeline, source_audio):
        regular_file(path)
    regular_file(snapshot, 100_000)
    regular_file(comments_script, 100_000)
    data = json.loads(snapshot.read_text(encoding='utf-8'))
    if data != story.script() or json.loads(comments_script.read_text(encoding='utf-8')) != overlay.COMMENTS:
        raise ValueError('Сценарии не совпадают с предыдущей пробой')
    if source.is_symlink() or not source.is_file() or source.stat().st_size != story.SOURCE_SIZE:
        raise ValueError('Не найден исходный MP4')
    if bulletin_preflight.check(ffmpeg):
        raise ValueError('Не пройдена предварительная проверка')
    duration = story.source_duration(source, ffprobe)
    cut = overlay.choose_pause(dialogue, data)
    folder = base / FOLDER
    folder.mkdir(mode=0o700, exist_ok=False)
    timeline, window, clicks, total, clip_seconds = overlay.assemble(
        dialogue, source_audio, comments, folder / 'timeline.wav', cut_frame=cut,
        source_duration=duration, data=data, continuous_video=True)
    _, old = podcast_video_trial.read_wav(old_timeline)
    _, new = podcast_video_trial.read_wav(timeline[0])
    per_frame = podcast_audio_trial.RATE // story.bender_trial.FPS
    start, end = (frame * per_frame for frame in window)
    if len(old) != len(new) or old[:start] != new[:start] or old[end:] != new[end:]:
        raise TimingMismatch('Тайминг или речь вне вставки отличаются от предыдущей пробы')
    print(f'Тайминг сохранён: пауза {cut / overlay.FPS:.2f} с, вставка {clip_seconds:.1f} с, '
          f'длительность {total:.1f} с.', flush=True)
    print('Использую прежние реплики: новой озвучки нет. Видео не перезапускается при переключении.', flush=True)
    partial, output = folder / 'video.partial.mp4', folder / 'video.mp4'
    story.render(source, partial, ffmpeg, timeline, fullscreen=window, clicks=clicks, continuous_video=True)
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i', str(partial), '-f', 'null', '-'],
                   check=True, timeout=240, env=podcast_audio_trial.normalized_env(),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    partial.replace(output)
    print(f'Готово: {output}', flush=True)
    env = bulletin_duo_content_probe.read_worker_env()
    print(bulletin_full_video_trial.send_video_once(output, folder, env,
          caption='Проба: видео продолжает идти при переключении телевизора; реплики и время сохранены.'), flush=True)
    return output


def main() -> int:
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        print('Не найден ffmpeg или ffprobe; ничего не запущено.')
        return 1
    try:
        run(BASE, SOURCE, ffmpeg, ffprobe)
        return 0
    except FileExistsError:
        print('Пересборка уже запускалась. Проверь папку и личку; не повторяй вслепую.')
    except TimingMismatch as exc:
        print(str(exc), 'Пересборка остановлена без отправки.')
    except Exception:
        print('Пересборка остановлена. Пришли вывод и не повторяй запуск вслепую.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())

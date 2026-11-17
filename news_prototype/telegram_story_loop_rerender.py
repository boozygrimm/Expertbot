"""One-off rerender of post 7266 from its saved dialogue, no TTS or worker job.

Play the complete source video and then repeat it until the dialogue ends. The
new folder and Telegram marker make this a one-shot owner delivery. No queue,
service, database, source media or previous result is changed.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess

from news_prototype import (bulletin_duo_content_probe, bulletin_full_video_trial,
                            bulletin_preflight, podcast_audio_trial,
                            telegram_story_podcast_trial as trial)

BASE = trial.BASE
SOURCE = trial.SOURCE
FOLDER_NAME = 'video-loop-1'


def report_black(path: Path, ffmpeg: str, *, screen: bool) -> None:
    """Report dark intervals, distinguishing source content from the studio screen."""
    filters = ('crop=230:100:340:115,' if screen else '') + 'blackdetect=d=2:pix_th=0.10:pic_th=0.95'
    try:
        result = subprocess.run([ffmpeg, '-nostdin', '-hide_banner', '-nostats', '-v', 'info',
                                 '-protocol_whitelist', 'file,pipe',
                                 '-format_whitelist', 'mov,mp4,m4a,3gp,3g2,mj2',
                                 '-i', str(path), '-map', '0:v:0', '-vf', filters, '-an', '-f', 'null', '-'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=180,
                                env=podcast_audio_trial.normalized_env())
        matches = re.findall(rb'black_start:([0-9.]+)\s+black_end:([0-9.]+)\s+black_duration:([0-9.]+)', result.stderr)
        label = 'На экране готового ролика' if screen else 'В исходном видео'
        if matches:
            intervals = ', '.join(f'{float(start):.1f}–{float(end):.1f} с' for start, end, _ in matches[:10])
            print(f'{label} найдены тёмные интервалы от 2 с: {intervals}.', flush=True)
        else:
            print(f'{label} тёмных интервалов от 2 с не обнаружено.', flush=True)
    except (OSError, subprocess.SubprocessError, ValueError):
        print('Автоматическая проверка тёмных кадров недоступна; проверь видео глазами.', flush=True)


def run(base: Path, source: Path, ffmpeg: str, ffprobe: str) -> Path:
    if base.is_symlink() or not base.is_dir():
        raise ValueError('Нет папки исходного подкаста')
    snapshot, audio_dir = base / 'script.json', base / 'audio'
    audio = audio_dir / 'dialogue.wav'
    if (snapshot.is_symlink() or not snapshot.is_file() or snapshot.stat().st_size > 100_000
            or audio_dir.is_symlink() or not audio_dir.is_dir() or audio.is_symlink() or not audio.is_file()):
        raise ValueError('Нет проверенных файлов подкаста')
    data = json.loads(snapshot.read_text(encoding='utf-8'))
    if data != trial.script():
        raise ValueError('Сохранённый сценарий не совпадает с исходным')
    if source.is_symlink() or not source.is_file() or source.stat().st_size != trial.SOURCE_SIZE:
        raise ValueError('Не найден ожидаемый исходный MP4')
    if bulletin_preflight.check(ffmpeg):
        raise ValueError('Не пройдена предварительная проверка')
    duration = trial.source_duration(source, ffprobe)
    timeline, _, total = trial.build_timeline(audio, data, duration)
    folder = base / FOLDER_NAME
    folder.mkdir(mode=0o700, exist_ok=False)
    partial, output = folder / 'video.partial.mp4', folder / 'video.mp4'
    print(f'Использую сохранённую озвучку. Длительность ролика {total:.1f} с, видео поста {duration:.1f} с.', flush=True)
    print('Показываю исходное видео целиком и повторяю его до конца диалога.', flush=True)
    report_black(source, ffmpeg, screen=False)
    trial.render(source, partial, ffmpeg, timeline)
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i', str(partial), '-f', 'null', '-'],
                   check=True, timeout=180, env=podcast_audio_trial.normalized_env(),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    partial.replace(output)
    print(f'Видео сохранено: {output}', flush=True)
    report_black(output, ffmpeg, screen=True)
    env = bulletin_duo_content_probe.read_worker_env()
    print(bulletin_full_video_trial.send_video_once(output, folder, env,
          caption='Исправленный ролик: видео поста повторяется до конца диалога. Без новой озвучки.'), flush=True)
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
        print('Пересборка уже запускалась. Проверь папку и личку, не повторяй вслепую.')
    except Exception:
        print('Пересборка остановлена. Сохрани вывод и не повторяй запуск вслепую.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())

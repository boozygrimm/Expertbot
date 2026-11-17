"""Isolated first stage of a one-off full bulletin trial.

This does not resume or mutate the failed job, queue, worker, or database. It
makes one multi-speaker request per news item using existing key rotation and
sends the combined audio to the owner once. The owner must review the speech
and mark speaker boundaries before a separate video stage can be attempted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

from news_prototype import (bulletin_dialogue_audio, bulletin_duo_content_probe,
                            bulletin_preflight, bulletin_timing, podcast_audio_trial,
                            podcast_video_trial)
from video_prototype import gemini_tts
from video_prototype.news_jobs import MAX_BULLETIN_BYTES, validate_bulletin

JOB = Path('/data/video-prototype/news-jobs/fe65e362-28b2-51bd-b626-0e33388b165c')
FOLDER = Path('/data/video-prototype/bulletin-full-duo-fe65e362-1')


def load_bulletin(job: Path) -> dict:
    request = job / 'request.json'
    if job.is_symlink() or request.is_symlink() or not job.is_dir() or request.stat().st_size > MAX_BULLETIN_BYTES:
        raise ValueError('Неверная сохранённая задача')
    data = json.loads(request.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or set(data) != {'bulletin'}:
        raise ValueError('Неверная сохранённая задача')
    _, bulletin = validate_bulletin({'request_id': job.name, 'bulletin': data['bulletin']})
    return bulletin


def run(job: Path, folder: Path, ffmpeg: str) -> None:
    bulletin = load_bulletin(job)
    if folder.resolve().is_relative_to(job.resolve()):
        raise ValueError('Нельзя сохранять пробу внутри задачи')
    problems = bulletin_preflight.check(ffmpeg)
    if problems:
        raise ValueError('Не пройдена проверка кода, слоёв или кодеков')
    env = bulletin_duo_content_probe.read_worker_env()
    keys = bulletin_duo_content_probe.worker_keys(env)
    if not keys:
        raise ValueError('Не найдены ключи TTS воркера')
    # This fresh directory is the one-shot marker even after a partial failure.
    folder.mkdir(mode=0o700, parents=False, exist_ok=False)
    snapshot = folder / 'bulletin.json'
    with snapshot.open('x', encoding='utf-8') as out:
        json.dump(bulletin, out, ensure_ascii=False)
        out.flush()
        os.fsync(out.fileno())
    audio, parts = bulletin_dialogue_audio.prepare(bulletin, folder / 'audio', ffmpeg, keys)
    _, total = bulletin_timing.section_windows(parts)
    print(f'Длительность с заставками: {total:.1f} с (максимум 300 с).', flush=True)
    cursor = 0
    rate = podcast_audio_trial.RATE
    for number, ((_, path), section) in enumerate(zip(parts, bulletin['sections']), 1):
        part_rate, samples = podcast_video_trial.read_wav(path)
        if part_rate != rate:
            raise ValueError('Неверный формат аудио')
        start, end = cursor / rate, (cursor + len(samples)) / rate
        speakers = ' → '.join(turn['speaker'] for turn in section['turns'])
        print(f'Новость {number}: {start:.2f}–{end:.2f} с; порядок по сценарию: {speakers}', flush=True)
        cursor += len(samples) + round(rate * podcast_audio_trial.GAP_MS / 1000)
    print(bulletin_duo_content_probe.send_once(audio, folder, env), flush=True)
    print('Прослушай весь звук: нужно подтвердить полноту и разметить смены ведущих до видео.', flush=True)


def main() -> int:
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        print('Не найден ffmpeg; ничего не запущено.')
        return 1
    try:
        run(JOB, FOLDER, ffmpeg)
        return 0
    except FileExistsError:
        print('Папка пробы уже существует. Не повторяй запуск: сначала проверь её и личку.')
    except bulletin_timing.BulletinTooLong as exc:
        print('Выпуск слишком длинный:', str(exc), 'Аудио сохранено, видео и отправка не выполнены.')
    except (ValueError, json.JSONDecodeError, gemini_tts.TTSFailure, bulletin_duo_content_probe.ProbeError) as exc:
        # TTSFailure and ProbeError contain only fixed, sanitized categories.
        if isinstance(exc, (gemini_tts.TTSFailure, bulletin_duo_content_probe.ProbeError)):
            print('Проба остановлена:', str(exc))
        else:
            print('Проба остановлена: не прошла проверка данных или настроек.')
        print('Не повторяй запуск вслепую; сохрани вывод для диагностики.')
    except Exception:
        print('Проба остановлена из-за ошибки. Не повторяй запуск вслепую; сохрани вывод для диагностики.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())

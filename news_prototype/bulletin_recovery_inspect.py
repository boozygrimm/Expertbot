"""Read-only inspection of a failed bulletin's saved speech.

This is NOT a resume command. It neither changes the job nor contacts Gemini.
A consistent snapshot alone does not prove that the files have not been
modified, that a worker is idle, or that a retry is safe. In particular it
never changes a database state, reserves the worker or starts a renderer.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import stat
import wave

from video_prototype.news_jobs import MAX_BULLETIN_BYTES, validate_bulletin
from news_prototype.story_audio import validate_script


class InspectionError(Exception):
    pass


@dataclass(frozen=True)
class Inspection:
    request_id: str
    turns: int
    complete_pairs: int
    seconds: float


def _regular(path: Path, limit: int) -> bool:
    if path.is_symlink():
        raise InspectionError('Обнаружена символическая ссылка')
    try:
        info = path.stat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise InspectionError('Необычный или слишком большой файл')
    return True


def _read_json(path: Path, limit: int):
    if not _regular(path, limit):
        raise InspectionError('Отсутствуют данные задачи')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (UnicodeError, ValueError):
        raise InspectionError('Повреждены данные задачи') from None


def _wav(path: Path, normalized: bool) -> float:
    if not _regular(path, 4 * 1024 * 1024):
        raise InspectionError('Отсутствует аудиофайл')
    try:
        with wave.open(str(path), 'rb') as audio:
            rate, frames = audio.getframerate(), audio.getnframes()
            if (audio.getnchannels() != 1 or audio.getsampwidth() != 2 or
                    audio.getcomptype() != 'NONE' or rate <= 0 or frames <= 0 or
                    frames / rate >= 60 or (normalized and rate != 24000)):
                raise InspectionError('Неподходящий формат или длительность аудио')
            samples = audio.readframes(frames)
            if len(samples) != 2 * frames or not any(samples):
                raise InspectionError('Повреждённая или пустая запись')
            return frames / rate
    except (wave.Error, EOFError):
        raise InspectionError('Повреждённая запись WAV') from None


def inspect(job: Path) -> Inspection:
    """Validate a completed failure snapshot, with no side effects."""
    job = Path(job)
    if job.is_symlink() or not job.is_dir():
        raise InspectionError('Папка задачи недоступна')
    if not _regular(job / 'failed', 4096):
        raise InspectionError('Нет подтверждённого маркера ошибки')
    request = _read_json(job / 'request.json', MAX_BULLETIN_BYTES)
    if not isinstance(request, dict) or set(request) != {'bulletin'}:
        raise InspectionError('Это не сохранённая задача вестника')
    try:
        request_id, bulletin = validate_bulletin({'request_id': job.name, 'bulletin': request['bulletin']})
        turns = [turn for section in bulletin['sections'] for turn in section['turns']]
        expected = validate_script({'turns': turns, 'ticker': bulletin['sections'][0]['ticker']})
    except (ValueError, TypeError, KeyError):
        raise InspectionError('Неверная задача или сценарий') from None
    work = job / 'work'
    if work.is_symlink() or not work.is_dir():
        raise InspectionError('Папка аудио недоступна')
    saved = _read_json(work / 'script.json', 100_000)
    if saved != expected:
        raise InspectionError('Сценарий аудио отличается от задачи')
    # Completed audio-stage or rendering artifacts imply a different failure;
    # do not misclassify these as a partial speech-only job.
    for name in ('status.json', 'story.json', 'dialogue.wav', 'video.mp4', 'video.partial.mp4'):
        if (work / name).exists() or (work / name).is_symlink():
            raise InspectionError('Найден результат другого этапа')
    raw, normalized = set(), set()
    for child in work.iterdir():
        match = re.fullmatch(r'([1-9][0-9]*)-(raw|normalized)\.wav', child.name)
        if match:
            number = int(match[1])
            (raw if match[2] == 'raw' else normalized).add(number)
        elif child.name.endswith(('.wav', '.wav.tmp')):
            raise InspectionError('Найдена незавершённая или неизвестная аудиозапись')
    if raw != normalized or raw != set(range(1, len(raw) + 1)) or len(raw) >= len(turns):
        raise InspectionError('Неполная или неоднозначная последовательность аудио')
    total = 0.0
    for index in sorted(raw):
        a = _wav(work / f'{index}-raw.wav', False)
        b = _wav(work / f'{index}-normalized.wav', True)
        if abs(a - b) > 0.05:
            raise InspectionError('Длины пары аудиозаписей не совпадают')
        total += b
    return Inspection(request_id, len(turns), len(raw), total)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job', type=Path)
    args = parser.parse_args()
    try:
        result = inspect(args.job)
    except (InspectionError, OSError) as exc:
        print('Проверка не пройдена:', str(exc) if isinstance(exc, InspectionError) else type(exc).__name__)
        return 1
    print('Задача:', result.request_id)
    print('Реплик:', result.turns, '; полных пар аудио:', result.complete_pairs)
    print('Продолжительность сохранённой озвучки:', round(result.seconds, 1), 'с')
    print('Сценарий и формат записей совпадают с ожидаемыми.')
    print('Это не проверка занятости воркера и не разрешение на повтор; ничего не запущено.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

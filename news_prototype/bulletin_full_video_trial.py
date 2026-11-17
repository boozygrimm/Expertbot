"""One-off video preview from the already generated bulletin dialogue.

No TTS, queue, database, service or publication operations. Candidate speaker
boundaries are only heuristics; ambiguous sections have no talking-mouth
animation. This is a review preview, not a verified speaker alignment.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import urllib.request

from news_prototype import (bender_trial, bulletin_contract, bulletin_duo_content_probe,
                            bulletin_full_dialogue_trial, bulletin_layered_video,
                            bulletin_preflight, bulletin_timing, podcast_audio_trial,
                            podcast_video_trial)
from news_prototype.send_bulletin_preview import read_settings, DEFAULT_OWNER
from video_prototype.gemini_tts import NoRedirect

BASE = bulletin_full_dialogue_trial.FOLDER
JOB = bulletin_full_dialogue_trial.JOB
MAX_VIDEO_BYTES = 50 * 1024 * 1024


def candidate_split(samples, turns):
    """Return one plausible pause, or None when ambiguous. Not diarization."""
    if len(turns) != 2 or turns[0]['speaker'] == turns[1]['speaker']:
        return None
    rate, fps = podcast_audio_trial.RATE, bender_trial.FPS
    levels = bender_trial.levels(samples, rate)
    if not levels:
        return None
    peak = max(levels)
    if peak <= 0:
        return None
    threshold = min(peak * .15, max(35.0, peak * .035))
    lengths = [len(t['text'].strip()) for t in turns]
    ratio = lengths[0] / sum(lengths)
    lower, upper = max(.20, ratio - .18), min(.80, ratio + .18)
    candidates = []
    start = None
    for i in range(len(levels) + 1):
        silent = i < len(levels) and levels[i] <= threshold
        if silent and start is None:
            start = i
        elif not silent and start is not None:
            if (i - start) / fps >= .30:
                midpoint = (start + i) / (2 * fps)
                if lower <= midpoint / (len(samples) / rate) <= upper:
                    candidates.append(midpoint)
            start = None
    return candidates[0] if len(candidates) == 1 else None


def build_timeline(audio: Path, parts: list[tuple[int, Path]], sections: list[dict]):
    """Verify unaltered PCM assembly, then estimate active speakers by section."""
    with tempfile.TemporaryDirectory(prefix='bulletin-check-') as tmp:
        rebuilt = Path(tmp) / 'rebuilt.wav'
        podcast_audio_trial.assemble_wav([path for _, path in parts], rebuilt)
        rate, joined = podcast_video_trial.read_wav(audio)
        _, expected = podcast_video_trial.read_wav(rebuilt)
    if joined != expected:
        raise ValueError('Общий звук не совпадает с записями новостей')
    fps = bender_trial.FPS
    ranges, cursor, estimates = [], 0, []
    gap = round(rate * podcast_audio_trial.GAP_MS / 1000)
    for index, ((section_id, path), section) in enumerate(zip(parts, sections), 1):
        if section_id != section['id']:
            raise ValueError('Неверный порядок новостей')
        _, samples = podcast_video_trial.read_wav(path)
        turns = section['turns']
        names = {turn['speaker'] for turn in turns}
        if len(names) == 1:
            split, speaker = None, turns[0]['speaker']
            ranges.append((cursor, cursor + len(samples), speaker))
            estimates.append('один ведущий по сценарию')
        else:
            split = candidate_split(samples, turns)
            if split is None:
                estimates.append('граница неясна, рты неподвижны')
            else:
                boundary = cursor + round(split * rate)
                ranges.extend([(cursor, boundary, turns[0]['speaker']),
                               (boundary, cursor + len(samples), turns[1]['speaker'])])
                estimates.append(f'предположительная смена на {boundary / rate:.2f} с аудио')
        cursor += len(samples)
        if index < len(parts):
            cursor += gap
    rms = bender_trial.levels(joined, rate)
    active = []
    for i in range(len(rms)):
        midpoint = int((i + .5) * rate / fps)
        active.append(next((name for start, end, name in ranges if start <= midpoint < end), None))
    states = [False] * len(active)
    i = 0
    while i < len(active):
        if active[i] is None:
            i += 1
            continue
        end = i + 1
        while end < len(active) and active[end] == active[i]:
            end += 1
        states[i:end] = bender_trial.mouth_states(rms[i:end])
        i = end
    return (audio, active, states), estimates


def send_video_once(video: Path, folder: Path, env: dict) -> str:
    settings = read_settings()
    token = (os.environ.get('BOT_TOKEN') or os.environ.get('TELEGRAM_TOKEN') or settings.get('BOT_TOKEN')
             or settings.get('TELEGRAM_TOKEN') or env.get('BOT_TOKEN') or env.get('TELEGRAM_TOKEN'))
    chat = os.environ.get('ADMIN_ID') or settings.get('ADMIN_ID') or env.get('ADMIN_ID') or DEFAULT_OWNER
    if not token or not chat.isdigit() or int(chat) <= 0:
        return 'Видео сохранено, но настройки отправки не найдены.'
    if video.stat().st_size > MAX_VIDEO_BYTES:
        return 'Видео сохранено, но превышает 50 МБ; автоматическая отправка не выполнена.'
    data = video.read_bytes()
    boundary = '----bulletin-video-' + secrets.token_hex(12)
    payload = (f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat}\r\n'
               f'--{boundary}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n'
               'Пробный вестник: смена говорящих приблизительная, проверь на слух.\r\n'
               f'--{boundary}\r\nContent-Disposition: form-data; name="video"; filename="bulletin-preview.mp4"\r\n'
               'Content-Type: video/mp4\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
    request = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendVideo', data=payload,
                                     headers={'Content-Type': 'multipart/form-data; boundary=' + boundary}, method='POST')
    try:
        with (folder / 'telegram-send-started').open('xb') as f:
            f.write(b'one send\n'); f.flush(); os.fsync(f.fileno())
    except FileExistsError:
        return 'Отправка уже начиналась; проверь личку перед повтором.'
    print('Отправляю видео в личку один раз...', flush=True)
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=120) as response:
            result = json.loads(response.read(65537))
        return 'Telegram подтвердил отправку видео.' if result.get('ok') is True else 'Отправка не подтверждена; проверь личку.'
    except Exception:
        return 'Отправка не подтверждена; проверь личку перед повтором.'


def run(job: Path, base: Path, ffmpeg: str) -> Path:
    if base.is_symlink() or not base.is_dir():
        raise ValueError('Нет папки пробы')
    snapshot = base / 'bulletin.json'
    if snapshot.is_symlink() or snapshot.stat().st_size > 5_100_000:
        raise ValueError('Неверный снимок')
    bulletin = bulletin_contract.validate_bulletin(json.loads(snapshot.read_text(encoding='utf-8')))
    if bulletin != bulletin_full_dialogue_trial.load_bulletin(job):
        raise ValueError('Сценарий пробы не совпадает с сохранённой задачей')
    if bulletin_preflight.check(ffmpeg):
        raise ValueError('Не пройдена проверка кода, слоёв или кодеков')
    audio_dir = base / 'audio'
    audio = audio_dir / 'dialogue.wav'
    parts = [(section['id'], audio_dir / f'{index}-normalized.wav')
             for index, section in enumerate(bulletin['sections'], 1)]
    if audio_dir.is_symlink() or any(p.is_symlink() for p in [audio, *(p for _, p in parts)]):
        raise ValueError('Недопустимые ссылки на аудио')
    windows, total = bulletin_timing.section_windows(parts)
    timeline, estimates = build_timeline(audio, parts, bulletin['sections'])
    folder = base / 'video-preview-1'
    folder.mkdir(mode=0o700, exist_ok=False)
    for index, estimate in enumerate(estimates, 1):
        print(f'Новость {index}: {estimate}', flush=True)
    print(f'Создаю видео: {total:.1f} с. Синхронизация ведущих приблизительная.', flush=True)
    partial, output = folder / 'video.partial.mp4', folder / 'video.mp4'
    bulletin_layered_video.render(partial, ffmpeg, sections=bulletin['sections'], windows=windows, timeline=timeline)
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i', str(partial), '-f', 'null', '-'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180,
                   env=podcast_audio_trial.normalized_env())
    partial.replace(output)
    env = bulletin_duo_content_probe.read_worker_env()
    print(send_video_once(output, folder, env), flush=True)
    return output


def main() -> int:
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        print('Не найден ffmpeg; видео не запущено.')
        return 1
    try:
        output = run(JOB, BASE, ffmpeg)
        print(f'Видео сохранено: {output}')
        return 0
    except FileExistsError:
        print('Видео уже запускалось. Проверь папку и личку; повтор не выполнен.')
    except Exception:
        print('Проба остановлена. Не повторяй запуск вслепую; сохрани вывод для диагностики.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

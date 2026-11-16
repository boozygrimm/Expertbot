"""One-off two-voice podcast audition on InstaBox; not a production feature.

The speech is staged, not a news report. Each utterance and the assembled
recording are saved before the single Telegram send. An uncertain send is
never retried automatically. Uses the existing Gemini keys and bot token.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import urllib.request
import wave

from news_prototype.run_bender_trial import load_keys
from news_prototype.send_press_trial import load_token
from video_prototype import gemini_tts

CHAT = '-1004395648779'
BASE = Path('/data/video-prototype/clips')
RATE = 24000
GAP_MS = 350
BENDER_STYLE = 'Говори по-русски, низким мужским голосом, выразительно и с лёгким сарказмом.'
SENSEI_STYLE = 'Говори по-русски спокойно, размеренно и тепло, как мудрый собеседник, без пародийного акцента.'
DIALOGUE = (
    ('Бендер', 'Algenib', BENDER_STYLE,
     'Земляне, срочные новости! Нашу студию наконец запустили. Сенсей, ты готов к прямому эфиру?'),
    ('Киберсенсей', 'Iapetus', SENSEI_STYLE,
     'Готов, Бендер. Но прежде чем говорить громко, убедимся, что нам есть что сказать.'),
    ('Бендер', 'Algenib', BENDER_STYLE,
     'Есть! У нас потрясающий ёжик в рамке и я, великолепный робот. Разве этого мало?'),
    ('Киберсенсей', 'Iapetus', SENSEI_STYLE,
     'Для начала достаточно. А дальше проверим факты, даже если твоё великолепие не вызывает сомнений.'),
)


def normalized_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in ('BOT_TOKEN', 'VIDEO_WORKER_KEY', 'STORAGE_CHANNEL_ID', 'GEMINI_TTS_KEYS',
                 'GEMINI_KEYS', 'GEMINI_API_KEYS', 'GEMINI_API_KEY'):
        env.pop(name, None)
    return env


def assemble_wav(paths: list[Path], output: Path) -> None:
    """Combine normalized mono 24 kHz PCM without modifying the recordings."""
    if not paths:
        raise ValueError('Нет записей для сборки')
    gap = bytes(round(RATE * GAP_MS / 1000) * 2)
    with wave.open(str(output), 'wb') as combined:
        combined.setnchannels(1)
        combined.setsampwidth(2)
        combined.setframerate(RATE)
        for index, path in enumerate(paths):
            with wave.open(str(path), 'rb') as part:
                count = part.getnframes()
                if (part.getnchannels(), part.getsampwidth(), part.getframerate(), part.getcomptype()) != (1, 2, RATE, 'NONE'):
                    raise ValueError('Неверный формат реплики')
                data = part.readframes(count)
                if count <= 0 or len(data) != count * 2:
                    raise ValueError('Пустая или повреждённая реплика')
            if index:
                combined.writeframes(gap)
            combined.writeframes(data)


def send_audio(token: str, audio: Path) -> bool:
    data = audio.read_bytes()
    if len(data) > 50 * 1024 * 1024:
        raise ValueError('Файл больше 50 МБ')
    boundary = '----news-dialogue-' + secrets.token_hex(12)
    def field(name: str, value: str) -> bytes:
        return (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                f'{value}\r\n').encode()
    body = field('chat_id', CHAT) + field('caption', 'Проба подкаста: Бендер — Algenib, киберсенсей — Iapetus')
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="voice"; '
             'filename="podcast.ogg"\r\nContent-Type: audio/ogg\r\n\r\n').encode()
    body += data + f'\r\n--{boundary}--\r\n'.encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendVoice', data=body,
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'}, method='POST')
    with urllib.request.urlopen(req, timeout=120) as response:
        return bool(json.load(response).get('ok'))


def main() -> None:
    token, keys = load_token(), load_keys()
    if not token:
        raise SystemExit('Не найден BOT_TOKEN; ничего не отправлено.')
    if not keys:
        raise SystemExit('Не найдены ключи Gemini; ничего не отправлено.')
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise SystemExit('Не найден ffmpeg; ничего не отправлено.')
    BASE.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='news-dialogue-', dir=BASE))
    print(f'Папка пробы: {work}', flush=True)
    print('Озвучиваю четыре реплики существующим Gemini TTS; используется его квота.', flush=True)
    env = normalized_env()
    pieces = []
    old_voice, old_style = gemini_tts.VOICE, gemini_tts.STYLE
    try:
        for i, (speaker, voice, style, text) in enumerate(DIALOGUE):
            raw, normalized = work / f'{i + 1}-{voice}.wav', work / f'{i + 1}-normalized.wav'
            gemini_tts.VOICE, gemini_tts.STYLE = voice, style
            print(f'Реплика {i + 1}: {speaker} ({voice})...', flush=True)
            try:
                gemini_tts.synthesize(text, raw, keys, start_index=i)
            except gemini_tts.TTSFailure:
                raise SystemExit('Не удалось озвучить все реплики; проба не отправлена.') from None
            try:
                subprocess.run([ffmpeg, '-nostdin', '-y', '-loglevel', 'error', '-i', str(raw),
                                '-ac', '1', '-ar', str(RATE), '-c:a', 'pcm_s16le', str(normalized)],
                               env=env, check=True, timeout=120)
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                raise SystemExit('Не удалось подготовить реплику; проба не отправлена.') from None
            pieces.append(normalized)
    finally:
        gemini_tts.VOICE, gemini_tts.STYLE = old_voice, old_style
    wav, ogg = work / 'dialogue.wav', work / 'dialogue.ogg'
    try:
        assemble_wav(pieces, wav)
        subprocess.run([ffmpeg, '-nostdin', '-y', '-loglevel', 'error', '-i', str(wav),
                        '-c:a', 'libopus', '-b:a', '64k', '-vn', str(ogg)],
                       env=env, check=True, timeout=120)
    except (OSError, ValueError, wave.Error, EOFError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise SystemExit('Не удалось собрать диалог; проба не отправлена.') from None
    print('Диалог сохранён. Отправляю в тестовый чат один раз...', flush=True)
    try:
        accepted = send_audio(token, ogg)
    except Exception:
        accepted = False
    if accepted:
        print('Готово: голосовое отправлено в тестовый чат.', flush=True)
    else:
        print('Нет подтверждения отправки. Проверьте чат перед повтором.', flush=True)
    print(f'Готовый файл сохранён: {ogg}', flush=True)


if __name__ == '__main__':
    main()

"""Isolated Gemini voice audition for the cyber-sensei; manual InstaBox run.

Only the test chat is used. No service configuration is changed and every
sample is stored before one send. Do not retry an uncertain Telegram send.
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

from news_prototype.run_bender_trial import load_keys
from news_prototype.send_press_trial import load_token
from video_prototype import gemini_tts

CHAT = '-1004395648779'
VOICES = ('Charon', 'Orus', 'Iapetus')
TEXT = ('Приветствую вас, земляне. Сегодня мы с Бендером попытаемся разобраться, '
        'что творится в мире технологий. Не стоит спешить с выводами: сначала проверим факты, '
        'а затем позволим себе немного мудрости. И да, я слышу, как муха переключает рычаги.')
STYLE = 'Говори по-русски спокойно, размеренно и тепло, как мудрый собеседник, без пародийного акцента.'


def send_voice(token: str, voice: str, audio: Path) -> bool:
    media = audio.read_bytes()
    if len(media) > 50 * 1024 * 1024:
        raise ValueError('Пробный файл больше 50 МБ')
    boundary = '----sensei-' + secrets.token_hex(12)
    def field(name: str, value: str) -> bytes:
        return (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                f'{value}\r\n').encode()
    body = field('chat_id', CHAT) + field('caption', f'Киберсенсей — голос Gemini {voice}')
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="voice"; '
             'filename="sensei.ogg"\r\nContent-Type: audio/ogg\r\n\r\n').encode()
    body += media + f'\r\n--{boundary}--\r\n'.encode()
    request = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendVoice', data=body,
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'}, method='POST')
    with urllib.request.urlopen(request, timeout=120) as response:
        return bool(json.load(response).get('ok'))


def main() -> None:
    token, keys = load_token(), load_keys()
    if not token:
        raise SystemExit('Не найден BOT_TOKEN; ничего не отправлено.')
    if not keys:
        raise SystemExit('Не найден список ключей Gemini; ничего не отправлено.')
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise SystemExit('Не найден ffmpeg; ничего не отправлено.')
    base = Path('/data/video-prototype/clips')
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='sensei-voices-', dir=base))
    print(f'Папка проб: {work}', flush=True)
    print('Будет до трёх проб Gemini; используется существующая квота.', flush=True)
    clean_env = os.environ.copy()
    for name in ('BOT_TOKEN', 'VIDEO_WORKER_KEY', 'STORAGE_CHANNEL_ID', 'GEMINI_TTS_KEYS',
                 'GEMINI_KEYS', 'GEMINI_API_KEYS', 'GEMINI_API_KEY'):
        clean_env.pop(name, None)
    original_voice, original_style = gemini_tts.VOICE, gemini_tts.STYLE
    successes = 0
    try:
        gemini_tts.STYLE = STYLE
        for index, voice in enumerate(VOICES):
            wav, ogg = work / f'{voice}.wav', work / f'{voice}.ogg'
            gemini_tts.VOICE = voice
            print(f'Готовлю голос {voice}...', flush=True)
            try:
                gemini_tts.synthesize(TEXT, wav, keys, start_index=index)
            except gemini_tts.TTSFailure:
                print(f'{voice}: синтез недоступен, пропускаю.', flush=True)
                continue
            try:
                subprocess.run([ffmpeg, '-nostdin', '-y', '-loglevel', 'error', '-i', str(wav),
                                '-c:a', 'libopus', '-b:a', '48k', '-vn', str(ogg)],
                               env=clean_env, check=True, timeout=120)
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                print(f'{voice}: не удалось собрать голосовое, пропускаю.', flush=True)
                continue
            print(f'Отправляю {voice} один раз...', flush=True)
            try:
                accepted = send_voice(token, voice, ogg)
            except Exception:
                accepted = False
            if not accepted:
                print('Нет подтверждения отправки; остановлено. Проверьте чат перед повтором.', flush=True)
                return
            successes += 1
            print(f'{voice}: Telegram подтвердил отправку.', flush=True)
    finally:
        gemini_tts.VOICE, gemini_tts.STYLE = original_voice, original_style
    print(f'Готово. Отправлено проб: {successes}.', flush=True)


if __name__ == '__main__':
    main()

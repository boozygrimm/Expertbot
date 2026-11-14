"""Manual InstaBox-only voice auditions; no production worker changes."""
import argparse
import base64
import getpass
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave

TEXT = ('Ну что, мясные мешки, рабочий день закончился? Пора отдохнуть по-настоящему. '
        'Я бы посоветовал вам луна-парк, но лучше налейте чего-нибудь вкусного и выдохните. '
        'А завтра снова спасайте мир. Или хотя бы не забудьте зарядить мой блестящий металлический зад!')
MODEL = 'gemini-3.8-flash-tts'
GEMINI_VOICES = ('Zephyr', 'Puck', 'Charon', 'Kore', 'Fenrir', 'Leda', 'Orus',
                 'Aoede', 'Callirrhoe', 'Autonoe', 'Enceladus', 'Iapetus', 'Umbriel',
                 'Algieba', 'Despina', 'Erinome', 'Algenib', 'Rasalgethi', 'Laomedeia',
                 'Achernar', 'Alnilam', 'Schedar', 'Gacrux', 'Pulcherrima', 'Achird',
                 'Zubenelgenubi', 'Vindemiatrix', 'Sadachbia', 'Sadaltager', 'Sulafat')


def edge_voice(text, path, pitch="+0Hz"):
    # No shell, and don't echo a potentially sensitive subprocess error.
    result = subprocess.run([sys.executable, '-m', 'edge_tts', '--voice', 'ru-RU-DmitryNeural',
                             '--pitch=' + pitch, '--text', text, '--write-media', str(path)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    if result.returncode or not path.is_file() or not path.stat().st_size:
        raise RuntimeError('Edge TTS не вернул аудио')


def gemini_voice(key, voice, text, path):
    body = {'contents': [{'role': 'user', 'parts': [{'text': text,
            'speech_metadata': {'style': 'Говори по-русски, низким мужским голосом, выразительно и с лёгким сарказмом.'}}]}],
            'generationConfig': {'responseModalities': ['AUDIO'],
                                 'speechConfig': {'voiceConfig': {'voice': voice}}}}
    request = urllib.request.Request(
        f'https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent',
        data=json.dumps(body, ensure_ascii=False).encode(), method='POST',
        headers={'Content-Type': 'application/json', 'x-goog-api-key': key})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Gemini: HTTP {exc.code} (проверьте доступ и квоту)') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError('Gemini: ошибка связи') from None
    try:
        parts = result['candidates'][0]['content']['parts']
        audio = next(p['inlineData'] for p in parts if 'inlineData' in p)
        data = base64.b64decode(audio['data'], validate=True)
        mime = audio.get('mimeType', '').lower()
        if not data:
            raise ValueError('empty audio')
        if data.startswith(b'RIFF') and data[8:12] == b'WAVE':
            path.write_bytes(data)
        elif mime.startswith('audio/l16') or mime.startswith('audio/pcm'):
            # Only accept the documented mono 24 kHz 16-bit output for this audition.
            if 'rate=' in mime and 'rate=24000' not in mime.replace(' ', ''):
                raise ValueError('unexpected rate')
            if len(data) % 2:
                raise ValueError('odd PCM')
            with wave.open(str(path), 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(data)
        else:
            raise ValueError('unknown audio format')
    except (KeyError, IndexError, StopIteration, TypeError, ValueError):
        raise RuntimeError('Gemini: ответ не содержит ожидаемое аудио') from None


def send_document(token, chat_id, thread_id, path, caption):
    boundary = '----audition' + secrets.token_hex(16)
    body = bytearray()
    def field(name, value):
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    field('chat_id', chat_id)
    field('caption', caption)
    if thread_id is not None:
        field('message_thread_id', thread_id)
    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{path.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())
    body.extend(path.read_bytes())
    body.extend(f'\r\n--{boundary}--\r\n'.encode())
    request = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendDocument', data=bytes(body),
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.load(response)
        if not payload.get('ok'):
            raise ValueError('not confirmed')
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        # Do not send subsequent samples or retry after an ambiguous side effect.
        raise RuntimeError('Telegram не подтвердил отправку. Проверьте чат перед повтором.') from None


def read_key_file(path):
    """Read a private regular file, never following a symlink."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as f:
            info = os.fstat(f.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or info.st_size > 4096):
                raise ValueError('Файл ключа должен принадлежать текущему пользователю и иметь права 0600')
            key = f.read(4097).decode('utf-8').strip()
            if len(key) > 4096 or not key:
                raise ValueError('Файл ключа пустой или слишком большой')
            return key
    except (OSError, UnicodeError):
        raise ValueError('Не удалось безопасно прочитать файл ключа') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chat-id', type=int, required=True)
    parser.add_argument('--thread-id', type=int)
    parser.add_argument('--output-dir', type=Path, default=Path('/data/video-prototype/clips'))
    parser.add_argument('--gemini', action='store_true', help='добавить Gemini (только проект без биллинга)')
    parser.add_argument('--voices', nargs='+', choices=GEMINI_VOICES, default=['Charon', 'Fenrir'],
                        help='голоса Gemini для сравнения')
    parser.add_argument('--gemini-key-file', type=Path, help='закрытый файл ключа Gemini, права 0600')
    parser.add_argument('--skip-edge', action='store_true', help='только Gemini (вместе с --gemini)')
    args = parser.parse_args(argv)
    if args.gemini_key_file and not args.gemini:
        parser.error('--gemini-key-file требует --gemini')
    if args.skip_edge and not args.gemini:
        parser.error('--skip-edge требует --gemini')
    token = os.getenv('BOT_TOKEN', '').strip() or getpass.getpass('Токен бота (не отображается): ').strip()
    if not token:
        parser.error('Нужен токен бота')
    key = ''
    if args.gemini:
        print('Используйте только ключ проекта Gemini Free Tier без включённого billing. '
              'Платный проект может списать деньги. Содержимое Free Tier может использоваться Google для улучшения продуктов.')
        try:
            key = (read_key_file(args.gemini_key_file) if args.gemini_key_file else
                   getpass.getpass('Ключ Gemini (не отображается): ').strip())
        except ValueError as exc:
            parser.error(str(exc))
        if not key:
            parser.error('Нужен ключ Gemini')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='tts-audition-', dir=args.output_dir))
    print('Папка проб:', work, flush=True)
    print('Текст:', TEXT, flush=True)
    samples = [] if args.skip_edge else [
        ('Edge Dmitry обычный', 'edge-dmitry.mp3', lambda p: edge_voice(TEXT, p)),
        ('Edge Dmitry ниже', 'edge-dmitry-low.mp3', lambda p: edge_voice(TEXT, p, '-15Hz'))]

    if args.gemini:
        samples += [(f'Gemini {v}', f'gemini-{v.lower()}.wav',
                     lambda p, v=v: gemini_voice(key, v, TEXT, p)) for v in args.voices]
    successes = 0
    for label, filename, generate in samples:
        print(f'Готовлю {label}...', flush=True)
        path = work / filename
        start = time.monotonic()
        try:
            generate(path)
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
            print(f'{label}: не получилось ({type(exc).__name__}: {exc})', flush=True)
            continue
        elapsed = time.monotonic() - start
        print(f'{label}: {elapsed:.1f} с. Отправляю один раз...', flush=True)
        try:
            send_document(token, args.chat_id, args.thread_id, path,
                          f'{label} — генерация {elapsed:.1f} с. Одна и та же фраза для сравнения.')
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            return 1
        successes += 1
        print('Telegram подтвердил отправку.', flush=True)
    return 0 if successes else 1


if __name__ == '__main__':
    sys.exit(main())

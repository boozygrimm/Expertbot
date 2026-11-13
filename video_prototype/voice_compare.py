"""One-off diagnostic: synthesize once, send the WAV and its video note."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import urllib.error
import urllib.request

if __package__:
    from .pipeline import telegram_call
else:
    from pipeline import telegram_call


def send_wav(token, chat_id, path):
    boundary = '----bender-compare-' + secrets.token_hex(16)
    body = bytearray()
    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode())
    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="speech.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode())
    body.extend(Path(path).read_bytes())
    body.extend(f'\r\n--{boundary}--\r\n'.encode())
    request = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendDocument', data=bytes(body),
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Telegram отклонил WAV: HTTP {exc.code}') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError('Нет подтверждения отправки WAV; проверь тестовый чат') from None
    if not payload.get('ok'):
        raise RuntimeError('Telegram не подтвердил отправку WAV')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Сравнение WAV и кружка из одной записи')
    parser.add_argument('--text', required=True)
    parser.add_argument('--chat-id', type=int, required=True)
    parser.add_argument('--code-dir', type=Path, default=Path('/data/video-prototype/code-smoke-828b78d'))
    parser.add_argument('--plate', type=Path, default=Path('/data/video-prototype/assets/smoke-free-103.png'))
    parser.add_argument('--frames', type=Path, default=Path('/data/video-prototype/preview'))
    parser.add_argument('--output-dir', type=Path, default=Path('/data/video-prototype/clips'))
    args = parser.parse_args(argv)
    if not args.text.strip() or len(args.text) > 480:
        parser.error('Текст должен содержать от 1 до 480 символов')
    token = os.environ.get('BOT_TOKEN', '').strip()
    if not token:
        parser.error('Нужен BOT_TOKEN в окружении')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='voice-compare-', dir=args.output_dir))
    print('Папка результата:', work, flush=True)
    env = os.environ.copy()
    for name in ('BOT_TOKEN', 'VIDEO_WORKER_KEY', 'STORAGE_CHANNEL_ID'):
        env.pop(name, None)
    env['HF_HOME'] = '/data/teratts-test/hf'
    wav = work / 'speech.wav'
    video = work / 'video.mp4'
    subprocess.run(['/data/teratts-test/.venv/bin/python',
                    str(args.code_dir / 'video_prototype/tera_speech.py'),
                    '--text', args.text, '--output', str(wav)], env=env, check=True)
    subprocess.run(['/data/video-prototype/.venv/bin/python',
                    str(args.code_dir / 'video_prototype/smoke_layer_render.py'),
                    '--audio', str(wav), '--closed', str(args.frames / 'closed.png'),
                    '--open', str(args.frames / 'open.png'), '--blink', str(args.frames / 'blink.png'),
                    '--plate', str(args.plate), '--output', str(video)], env=env, check=True)
    print('Отправляю WAV...', flush=True)
    send_wav(token, args.chat_id, wav)
    print('WAV отправлен. Отправляю кружок...', flush=True)
    telegram_call(token, 'sendVideoNote', {'chat_id': args.chat_id}, video)
    print('Кружок отправлен', flush=True)


if __name__ == '__main__':
    main()

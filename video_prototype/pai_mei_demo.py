"""One-off cameo from a Telegram photo and existing audio; does not touch the worker."""
import argparse
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.request

if __package__:
    from .pipeline import telegram_call
    from .pai_mei_render import render
else:
    from pipeline import telegram_call
    from pai_mei_render import render


def fetch_photo(token, source_chat, message_id, storage_chat, destination):
    # The Bot API has no getMessage: forwarding makes the file_id available.
    # This intentionally creates one copy in the private storage channel.
    forwarded = telegram_call(token, 'forwardMessage', {
        'chat_id': storage_chat, 'from_chat_id': source_chat, 'message_id': message_id})
    if forwarded.get('photo'):
        file_id = forwarded['photo'][-1]['file_id']
    elif forwarded.get('document', {}).get('mime_type', '').startswith('image/'):
        file_id = forwarded['document']['file_id']
    else:
        raise ValueError('В сообщении нет изображения')
    metadata = telegram_call(token, 'getFile', {'file_id': file_id})
    file_path = metadata.get('file_path')
    if not file_path:
        raise RuntimeError('Telegram не вернул путь к изображению')
    try:
        with urllib.request.urlopen(f'https://api.telegram.org/file/bot{token}/{file_path}', timeout=120) as response:
            content = response.read(20 * 1024 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError('Не удалось скачать изображение из Telegram') from None
    if len(content) > 20 * 1024 * 1024:
        raise ValueError('Картинка слишком большая')
    Path(destination).write_bytes(content)


def main():
    parser = argparse.ArgumentParser(description='Пробный кружок с Пай Меем')
    parser.add_argument('--audio', type=Path, required=True)
    parser.add_argument('--plate', type=Path, required=True)
    parser.add_argument('--chat-id', type=int, required=True)
    parser.add_argument('--message-id', type=int, required=True)
    parser.add_argument('--frames-dir', type=Path, default=Path('/data/video-prototype/preview'))
    parser.add_argument('--output-dir', type=Path, default=Path('/data/video-prototype/clips'))
    args = parser.parse_args()
    if not args.audio.is_file() or not args.plate.is_file():
        parser.error('Не найден WAV или бездымная подложка')
    token = os.environ.get('BOT_TOKEN', '').strip()
    storage = os.environ.get('STORAGE_CHANNEL_ID', '').strip()
    if not token or not storage:
        parser.error('В окружении нужны BOT_TOKEN и STORAGE_CHANNEL_ID')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='pai-mei-', dir=args.output_dir))
    print('Папка результата:', work, flush=True)
    photo = work / 'source-photo'
    fetch_photo(token, args.chat_id, args.message_id, storage, photo)
    print('Изображение получено из Telegram', flush=True)
    start = time.monotonic()
    output = work / 'video.mp4'
    render(args.audio, args.frames_dir / 'closed.png', args.frames_dir / 'open.png',
           args.frames_dir / 'blink.png', args.plate, photo, output)
    print(f'Время рендера: {time.monotonic() - start:.1f} с', flush=True)
    telegram_call(token, 'sendVideoNote', {'chat_id': args.chat_id}, output)
    print('Кружок отправлен в тестовый чат', flush=True)


if __name__ == '__main__':
    main()

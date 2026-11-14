"""One-off mouth test with existing audio, sent directly to the test chat."""
import argparse
import os
from pathlib import Path
import tempfile
import time

if __package__:
    from .pipeline import telegram_call
    from .mouth_trial_render import render
else:
    from pipeline import telegram_call
    from mouth_trial_render import render


def main():
    parser = argparse.ArgumentParser(description='Пробный кружок с новым движением рта')
    parser.add_argument('--audio', type=Path, required=True)
    parser.add_argument('--plate', type=Path, required=True)
    parser.add_argument('--chat-id', type=int, required=True)
    parser.add_argument('--frames-dir', type=Path, default=Path('/data/video-prototype/preview'))
    parser.add_argument('--output-dir', type=Path, default=Path('/data/video-prototype/clips'))
    args = parser.parse_args()
    if not args.audio.is_file() or not args.plate.is_file():
        parser.error('Не найден WAV или бездымная подложка')
    token = os.environ.get('BOT_TOKEN', '').strip()
    if not token:
        parser.error('В окружении нужен BOT_TOKEN')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='mouth-trial-', dir=args.output_dir))
    print('Папка результата:', work, flush=True)
    output = work / 'video.mp4'
    start = time.monotonic()
    render(args.audio, args.frames_dir / 'closed.png', args.frames_dir / 'open.png',
           args.frames_dir / 'blink.png', args.plate, output)
    print(f'Время рендера: {time.monotonic() - start:.1f} с', flush=True)
    telegram_call(token, 'sendVideoNote', {'chat_id': args.chat_id}, output)
    print('Кружок отправлен в тестовый чат', flush=True)


if __name__ == '__main__':
    main()

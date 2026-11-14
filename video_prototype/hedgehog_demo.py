"""One-off hedgehog test: Tera speech, experimental render, direct Telegram send."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import time

if __package__:
    from .pipeline import telegram_call
    from .hedgehog_render import render
else:
    from pipeline import telegram_call
    from hedgehog_render import render


def main():
    parser = argparse.ArgumentParser(description='Тестовый кружок с ёжиком')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--text')
    source.add_argument('--audio', type=Path, help='Готовый WAV вместо нового синтеза')
    parser.add_argument('--chat-id', type=int, required=True)
    parser.add_argument('--plate', type=Path, required=True)
    parser.add_argument('--frames-dir', type=Path, default=Path('/data/video-prototype/preview'))
    parser.add_argument('--output-dir', type=Path, default=Path('/data/video-prototype/clips'))
    args = parser.parse_args()
    if args.text is not None and (not args.text.strip() or len(args.text) > 480):
        parser.error('Текст должен содержать от 1 до 480 символов')
    if args.audio is not None and not args.audio.is_file():
        parser.error('Не найден аудиофайл')
    token = os.environ.get('BOT_TOKEN', '').strip()
    if not token:
        parser.error('Нужен BOT_TOKEN в окружении')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='hedgehog-', dir=args.output_dir))
    print('Папка результата:', work, flush=True)
    env = os.environ.copy()
    for name in ('BOT_TOKEN', 'VIDEO_WORKER_KEY', 'STORAGE_CHANNEL_ID'):
        env.pop(name, None)
    env['HF_HOME'] = '/data/teratts-test/hf'
    audio = args.audio
    if audio is None:
        audio = work / 'speech.wav'
        subprocess.run(['/data/teratts-test/.venv/bin/python',
                        '/data/video-prototype/code-speech-128a140/video_prototype/tera_speech.py',
                        '--text', args.text, '--output', str(audio)], env=env, check=True)
    start = time.monotonic()
    render(audio, args.frames_dir / 'closed.png', args.frames_dir / 'open.png',
           args.frames_dir / 'blink.png', args.plate, Path(__file__).resolve().parent / 'assets/hedgehog.png', work / 'video.mp4')
    print(f'Время рендера: {time.monotonic() - start:.1f} с', flush=True)
    telegram_call(token, 'sendVideoNote', {'chat_id': args.chat_id}, work / 'video.mp4')
    print('Кружок отправлен в тестовый чат', flush=True)


if __name__ == '__main__':
    main()

"""Synthesize a fresh test phrase, render the multi-guest gaze trial, and send it."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import time

if __package__:
    from .pipeline import telegram_call
    from .multi_gaze_render import render
else:
    from pipeline import telegram_call
    from multi_gaze_render import render


def main():
    parser = argparse.ArgumentParser(description='Проба взглядов на нескольких гостей')
    parser.add_argument('--text', required=True)
    parser.add_argument('--plate', type=Path, required=True)
    parser.add_argument('--chat-id', type=int, required=True)
    parser.add_argument('--frames-dir', type=Path, default=Path('/data/video-prototype/preview'))
    parser.add_argument('--output-dir', type=Path, default=Path('/data/video-prototype/clips'))
    parser.add_argument('--portrait', type=Path)
    args = parser.parse_args()
    if not args.text.strip() or len(args.text) > 480:
        parser.error('Текст должен содержать от 1 до 480 символов')
    if not args.plate.is_file():
        parser.error('Не найдена бездымная подложка')
    portrait = args.portrait
    if portrait is None:
        photos = [p for p in args.output_dir.glob('pai-mei-*/source-photo') if p.is_file()]
        if not photos:
            parser.error('Не нашлась картинка из успешной пробы Пай Мея')
        portrait = max(photos, key=lambda p: p.stat().st_mtime)
    if not portrait.is_file():
        parser.error('Не найдена картинка Пай Мея')
    token = os.environ.get('BOT_TOKEN', '').strip()
    if not token:
        parser.error('В окружении нужен BOT_TOKEN')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='multi-gaze-', dir=args.output_dir))
    print('Папка результата:', work, flush=True)
    print('Картинка Пай Мея:', portrait, flush=True)
    audio = work / 'speech.wav'
    env = os.environ.copy()
    for name in ('BOT_TOKEN', 'VIDEO_WORKER_KEY', 'STORAGE_CHANNEL_ID'):
        env.pop(name, None)
    env['HF_HOME'] = '/data/teratts-test/hf'
    subprocess.run(['/data/teratts-test/.venv/bin/python',
                    '/data/video-prototype/code-speech-128a140/video_prototype/tera_speech.py',
                    '--text', args.text, '--output', str(audio)], env=env, check=True)
    output = work / 'video.mp4'
    start = time.monotonic()
    render(audio, args.frames_dir / 'closed.png', args.frames_dir / 'open.png',
           args.frames_dir / 'blink.png', args.plate, portrait, output)
    print(f'Время рендера: {time.monotonic()-start:.1f} с', flush=True)
    telegram_call(token, 'sendVideoNote', {'chat_id': args.chat_id}, output)
    print('Кружок отправлен в тестовый чат', flush=True)


if __name__ == '__main__':
    main()

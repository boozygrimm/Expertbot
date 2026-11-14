"""One test: choose guests once, synthesize if needed, render, and send."""
import argparse
import os
from pathlib import Path
import random
import secrets
import subprocess
import tempfile
import time

if __package__:
    from .pipeline import telegram_call
    from .guest_planner import select_guests
    from .random_guest_render import render
else:
    from pipeline import telegram_call
    from guest_planner import select_guests
    from random_guest_render import render


def main():
    parser = argparse.ArgumentParser(description='Проба случайных гостей и взгляда')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--text')
    source.add_argument('--audio', type=Path)
    parser.add_argument('--plate', type=Path, required=True)
    parser.add_argument('--chat-id', type=int, required=True)
    parser.add_argument('--frames-dir', type=Path, default=Path('/data/video-prototype/preview'))
    parser.add_argument('--output-dir', type=Path, default=Path('/data/video-prototype/clips'))
    parser.add_argument('--portrait', type=Path, help='Ранее скачанная картинка сенсея')
    parser.add_argument('--seed', help='Необязательная строка для воспроизводимой пробы')
    args = parser.parse_args()
    if args.text is not None and (not args.text.strip() or len(args.text) > 480):
        parser.error('Текст должен содержать от 1 до 480 символов')
    if (args.audio is not None and not args.audio.is_file()) or not args.plate.is_file():
        parser.error('Не найден WAV или бездымная подложка')
    token = os.environ.get('BOT_TOKEN', '').strip()
    if not token:
        parser.error('В окружении нужен BOT_TOKEN')
    seed = args.seed if args.seed is not None else secrets.token_hex(16)
    # Check before synthesizing: use exactly the same first RNG draw as make_plan.
    selected = select_guests(random.Random(seed))
    portrait = args.portrait
    if portrait is None:
        photos = [p for p in args.output_dir.glob('pai-mei-*/source-photo') if p.is_file()]
        portrait = max(photos, key=lambda p: p.stat().st_mtime) if photos else None
    if 'pai_mei' in selected and (portrait is None or not portrait.is_file()):
        parser.error('Выбран сенсей, но его картинка из прошлой пробы не найдена')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='random-guests-', dir=args.output_dir))
    print('Папка результата:', work, flush=True)
    print('Seed:', seed, flush=True)
    audio = args.audio
    if audio is None:
        audio = work / 'speech.wav'
        env = os.environ.copy()
        for name in ('BOT_TOKEN', 'VIDEO_WORKER_KEY', 'STORAGE_CHANNEL_ID'):
            env.pop(name, None)
        env['HF_HOME'] = '/data/teratts-test/hf'
        subprocess.run(['/data/teratts-test/.venv/bin/python',
                        '/data/video-prototype/code-speech-128a140/video_prototype/tera_speech.py',
                        '--text', args.text, '--output', str(audio)], env=env, check=True)
    output = work / 'video.mp4'
    started = time.monotonic()
    render(audio, args.frames_dir/'closed.png', args.frames_dir/'open.png',
           args.frames_dir/'blink.png', args.plate, portrait, output, seed, work/'scene.json')
    print(f'Время рендера: {time.monotonic()-started:.1f} с', flush=True)
    telegram_call(token, 'sendVideoNote', {'chat_id': args.chat_id}, output)
    print('Кружок отправлен в тестовый чат', flush=True)


if __name__ == '__main__':
    main()

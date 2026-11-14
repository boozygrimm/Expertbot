"""Two Tera speech chunks with measured cues, then one test video and send."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import time
import wave

if __package__:
    from .pipeline import telegram_call
    from .scripted_reaction_render import render
else:
    from pipeline import telegram_call
    from scripted_reaction_render import render


def join_audio(first, second, output, gap=.25):
    chunks = []
    parameters = None
    for path in (first, second):
        with wave.open(str(path), 'rb') as wav:
            params = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype())
            if params[0] != 1 or params[1] != 2 or params[3] != 'NONE':
                raise ValueError('Нужен WAV PCM 16 бит, моно')
            if parameters is not None and params != parameters:
                raise ValueError('Разные форматы частей WAV')
            parameters = params
            chunks.append(wav.readframes(wav.getnframes()))
    rate = parameters[2]
    if not chunks[0] or not chunks[1]:
        raise ValueError('Пустая часть WAV')
    silence = b'\0\0' * round(rate * gap)
    second_start = (len(chunks[0]) + len(silence)) / (2 * rate)
    duration = (len(chunks[0]) + len(silence) + len(chunks[1])) / (2 * rate)
    if duration >= 60:
        raise ValueError('Аудио должно быть короче 60 секунд')
    with wave.open(str(output), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate)
        wav.writeframes(chunks[0] + silence + chunks[1])
    return second_start, duration


def main():
    parser = argparse.ArgumentParser(description='Проба реакций на речь')
    parser.add_argument('--first', required=True)
    parser.add_argument('--second', required=True)
    parser.add_argument('--plate', type=Path, required=True)
    parser.add_argument('--chat-id', type=int, required=True)
    parser.add_argument('--frames-dir', type=Path, default=Path('/data/video-prototype/preview'))
    parser.add_argument('--output-dir', type=Path, default=Path('/data/video-prototype/clips'))
    parser.add_argument('--portrait', type=Path)
    args = parser.parse_args()
    if any(not p.strip() or len(p) > 200 for p in (args.first, args.second)):
        parser.error('Каждая часть должна содержать от 1 до 200 символов')
    if not args.plate.is_file():
        parser.error('Не найдена бездымная подложка')
    portrait = args.portrait
    if portrait is None:
        photos = [p for p in args.output_dir.glob('pai-mei-*/source-photo') if p.is_file()]
        if not photos:
            parser.error('Не нашлась картинка из успешной пробы сенсея')
        portrait = max(photos, key=lambda p: p.stat().st_mtime)
    if not portrait.is_file():
        parser.error('Не найдена картинка сенсея')
    token = os.environ.get('BOT_TOKEN', '').strip()
    if not token:
        parser.error('В окружении нужен BOT_TOKEN')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='scripted-reactions-', dir=args.output_dir))
    print('Папка результата:', work, flush=True)
    env = os.environ.copy()
    for name in ('BOT_TOKEN', 'VIDEO_WORKER_KEY', 'STORAGE_CHANNEL_ID'):
        env.pop(name, None)
    env['HF_HOME'] = '/data/teratts-test/hf'
    paths = [work / 'part-1.wav', work / 'part-2.wav']
    for text, path in zip((args.first, args.second), paths):
        subprocess.run(['/data/teratts-test/.venv/bin/python',
                        '/data/video-prototype/code-speech-128a140/video_prototype/tera_speech.py',
                        '--text', text, '--output', str(path)], env=env, check=True)
    audio = work / 'speech.wav'
    second_start, duration = join_audio(paths[0], paths[1], audio)
    print(f'Вторая часть начинается в {second_start:.2f} с, всего {duration:.2f} с', flush=True)
    output = work / 'video.mp4'
    start = time.monotonic()
    render(audio, args.frames_dir / 'closed.png', args.frames_dir / 'open.png',
           args.frames_dir / 'blink.png', args.plate, portrait, output, second_start, duration)
    print(f'Время рендера: {time.monotonic()-start:.1f} с', flush=True)
    telegram_call(token, 'sendVideoNote', {'chat_id': args.chat_id}, output)
    print('Кружок отправлен в тестовый чат', flush=True)


if __name__ == '__main__':
    main()

"""One-off Edge voice comparison: synthesize small parts on InstaBox, then join locally."""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import wave

from video_prototype.tts_audition import TEXT, send_document


def split_text(text, limit=65):
    """Preserve words and punctuation, preferring sentence boundaries."""
    if limit < 1:
        raise ValueError('Недопустимый предел')
    chunks = []
    for sentence in re.split(r'(?<=[.!?…])\s+', text.strip()):
        current = ''
        for word in sentence.split():
            if len(word) > limit:
                raise ValueError('Слишком длинное слово')
            candidate = (current + ' ' + word).strip()
            if len(candidate) > limit:
                chunks.append(current)
                current = word
            else:
                current = candidate
        if current:
            chunks.append(current)
    return chunks


def synthesize(text, path, env):
    command = [sys.executable, '-m', 'edge_tts', '--voice', 'ru-RU-DmitryNeural',
               '--text', text, '--write-media', str(path)]
    for attempt in range(2):
        # An unsuccessful request can leave a partially written file.
        path.unlink(missing_ok=True)
        try:
            result = subprocess.run(command, env=env, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, text=True, timeout=60)
            if result.returncode == 0 and path.is_file() and path.stat().st_size:
                return
            reason = 'сервис не вернул аудио'
        except subprocess.TimeoutExpired:
            reason = 'тайм-аут'
        if attempt == 0:
            print('  Не вышло, повторяю эту часть один раз...', flush=True)
            time.sleep(1)
    raise RuntimeError(reason)


def join_parts(paths, output, env):
    # Decode each MP3 to the same PCM format, avoiding MP3 concatenation artifacts.
    with wave.open(str(output), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        for i, path in enumerate(paths):
            result = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 's16le',
                                     '-ac', '1', '-ar', '24000', 'pipe:1'],
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=60, check=True)
            if not result.stdout or len(result.stdout) % 2:
                raise RuntimeError('Ошибка декодирования аудио')
            if i:
                wav.writeframes(b'\0\0' * 3600)  # 150 ms between parts
            wav.writeframes(result.stdout)


def make_lower(source, output, env):
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-n', '-i', str(source),
                    '-af', 'asetrate=21600,aresample=24000,atempo=1.111111', str(output)],
                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                   timeout=60, check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chat-id', type=int, required=True)
    parser.add_argument('--thread-id', type=int)
    parser.add_argument('--output-dir', type=Path, default=Path('/data/video-prototype/clips'))
    args = parser.parse_args(argv)
    token = os.environ.get('BOT_TOKEN', '').strip()
    if not token:
        parser.error('BOT_TOKEN не задан')
    chunks = split_text(TEXT)
    if not chunks:
        parser.error('Пустой текст')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='edge-long-trial-', dir=args.output_dir))
    print('Папка:', folder, flush=True)
    print('Текст:', TEXT, flush=True)
    # TTS and ffmpeg do not need any inherited secrets.
    env = {key: value for key, value in os.environ.items()
           if key in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
                      'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY',
                      'http_proxy', 'https_proxy', 'no_proxy')}
    paths = []
    start = time.monotonic()
    try:
        for index, chunk in enumerate(chunks, 1):
            print(f'Часть {index}/{len(chunks)}...', flush=True)
            path = folder / f'part-{index:02}.mp3'
            synthesize(chunk, path, env)
            paths.append(path)
        elapsed = time.monotonic() - start
        normal, lower = folder / 'dmitry.wav', folder / 'dmitry-local-low.wav'
        join_parts(paths, normal, env)
        make_lower(normal, lower, env)
    except (RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        print(f'Проба не собрана: {type(exc).__name__}. Отправок не было.', file=sys.stderr)
        return 1
    print(f'Все части получены за {elapsed:.1f} с; две записи собраны.', flush=True)
    for path, label in [(normal, 'Edge Dmitry, обычный'),
                        (lower, 'Edge Dmitry, понижен локально на InstaBox')]:
        print('Отправляю:', label, flush=True)
        try:
            send_document(token, args.chat_id, args.thread_id, path,
                          f'{label}. Длинная проба, склеенная из частей. Синтез: {elapsed:.1f} с.')
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            return 1
        print('Telegram подтвердил отправку.', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())

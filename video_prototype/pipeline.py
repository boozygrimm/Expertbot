"""Local, manual proof of concept: speech -> video -> Telegram storage -> chat."""
import argparse
import getpass
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

if __package__:
    from .stress import apply_stress_marks, apply_ukusi_stress
else:
    from stress import apply_stress_marks, apply_ukusi_stress

HERE = Path(__file__).resolve().parent
DEFAULT_REFERENCE_TEXT = 'Ладно, я построю свой парк, с блэкджеком и шлюхами. И зачем мне... парк?'


def telegram_call(token, method, fields, file_path=None):
    """Send multipart without putting the token in a shell command or logs."""
    boundary = '----video-note-' + secrets.token_hex(16)
    body = bytearray()
    for key, value in fields.items():
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    if file_path:
        media = Path(file_path).read_bytes()
        if len(media) > 50 * 1024 * 1024:
            raise ValueError('Видео больше 50 МБ')
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="video_note"; filename="clip.mp4"\r\nContent-Type: video/mp4\r\n\r\n'.encode())
        body.extend(media)
        body.extend(b'\r\n')
    body.extend(f'--{boundary}--\r\n'.encode())
    request = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/{method}', data=bytes(body),
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'}, method='POST'
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.load(exc)
        except (ValueError, OSError):
            raise RuntimeError(f'Telegram вернул HTTP {exc.code}') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError('Не удалось связаться с Telegram; проверь соединение') from None
    if not payload.get('ok'):
        raise RuntimeError('Telegram: ' + str(payload.get('description', 'неизвестная ошибка')))
    return payload['result']


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='Ручной PoC видеокружка, не бот и не сервер')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--text', help='Фраза для синтеза (до 240 символов)')
    source.add_argument('--audio', type=Path, help='Готовый моно WAV PCM 16 бит вместо TTS')
    parser.add_argument('--channel-id', type=int, help='ID приватного канала-склада')
    parser.add_argument('--chat-id', type=int, help='ID тестового чата для отправки по file_id')
    parser.add_argument('--reply-to-message-id', type=int, help='ID сообщения в целевом чате, на которое ответить')
    parser.add_argument('--message-thread-id', type=int, help='ID темы форума, если нужен')
    parser.add_argument('--no-send', action='store_true', help='Только создать локальное видео')
    parser.add_argument('--frames-dir', type=Path, default=Path('/data/video-prototype/preview'))
    parser.add_argument('--output-dir', type=Path, default=Path('/data/video-prototype/clips'))
    parser.add_argument('--reference', type=Path, default=Path('/data/qwen-prototype/reference.wav'))
    parser.add_argument('--reference-text', default=DEFAULT_REFERENCE_TEXT)
    parser.add_argument('--qwen-python', default='/data/qwen-prototype/.venv/bin/python')
    parser.add_argument('--render-python', default='/data/video-prototype/.venv/bin/python')
    parser.add_argument('--hf-home', default='/data/qwen-prototype/hf')
    parser.add_argument('--tts-max-new-tokens', type=int, default=768, help='Предел токенов Qwen')
    stress = parser.add_mutually_exclusive_group()
    stress.add_argument('--stress-marks', action='store_true', help='Экспериментально отметить все слова для TTS')
    stress.add_argument('--ukusi-stress', action='store_true', help='Отметить только укуси/укусите')
    parser.add_argument('--seed', type=int, help='Фиксировать случайность TTS для сравнения проб')
    args = parser.parse_args(argv)
    if args.tts_max_new_tokens < 1:
        parser.error('--tts-max-new-tokens должен быть положительным')
    if args.text is not None and (not args.text.strip() or len(args.text) > 240):
        parser.error('Текст должен содержать от 1 до 240 символов')
    if not args.no_send and args.channel_id is None:
        parser.error('Укажи --channel-id либо --no-send')
    if args.no_send and args.chat_id is not None:
        parser.error('--chat-id нельзя использовать с --no-send')
    if (args.reply_to_message_id is not None or args.message_thread_id is not None) and args.chat_id is None:
        parser.error('Для ответа или темы укажи --chat-id')
    if args.reply_to_message_id is not None and args.reply_to_message_id <= 0:
        parser.error('--reply-to-message-id должен быть положительным')
    if args.message_thread_id is not None and args.message_thread_id <= 0:
        parser.error('--message-thread-id должен быть положительным')
    return args


def run(args):
    for filename in ('closed.png', 'open.png', 'blink.png'):
        if not (args.frames_dir / filename).is_file():
            raise FileNotFoundError(f'Нет картинки: {args.frames_dir / filename}')
    if args.audio and not args.audio.is_file():
        raise FileNotFoundError(f'Нет аудиофайла: {args.audio}')
    if args.text is not None and not args.reference.is_file():
        raise FileNotFoundError(f'Нет записи для голоса: {args.reference}')

    args.output_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='clip-', dir=args.output_dir))
    audio = args.audio
    if args.text is not None:
        audio = work / 'speech.wav'
        env = os.environ.copy()
        env.pop('BOT_TOKEN', None)
        env['HF_HOME'] = args.hf_home
        print(f'Папка результата: {work}', flush=True)
        if args.stress_marks:
            tts_text = apply_stress_marks(args.text)
        elif args.ukusi_stress:
            tts_text = apply_ukusi_stress(args.text)
        else:
            tts_text = args.text
        command = [
            args.qwen_python, str(HERE / 'speech.py'), '--text', tts_text,
            '--reference', str(args.reference), '--reference-text', args.reference_text,
            '--output', str(audio), '--max-new-tokens', str(args.tts_max_new_tokens),
        ]
        if args.seed is not None:
            command.extend(['--seed', str(args.seed)])
        subprocess.run(command, env=env, check=True)

    video = work / 'video.mp4'
    subprocess.run([
        args.render_python, str(HERE / 'render.py'), '--audio', str(audio),
        '--closed', str(args.frames_dir / 'closed.png'),
        '--open', str(args.frames_dir / 'open.png'),
        '--blink', str(args.frames_dir / 'blink.png'), '--output', str(video),
    ], check=True)
    if args.no_send:
        print(f'Локальный результат: {video}')
        return

    token = os.environ.get('BOT_TOKEN') or getpass.getpass('Токен бота (ввод не виден): ')
    if not token.strip():
        raise ValueError('Токен не введён')
    token = token.strip()
    result = telegram_call(token, 'sendVideoNote', {'chat_id': args.channel_id}, video)
    file_id = result.get('video_note', {}).get('file_id')
    if not file_id:
        raise RuntimeError('Telegram не вернул file_id видеокружка')
    record = {'channel_id': args.channel_id, 'file_id': file_id, 'video': str(video)}
    record_path = work / 'result.json'
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Кружок загружен в канал, file_id сохранён: {record_path}', flush=True)
    if args.chat_id is not None:
        # If this fails the channel upload and file_id remain available in result.json.
        fields = {'chat_id': args.chat_id, 'video_note': file_id}
        if args.reply_to_message_id is not None:
            fields['reply_parameters'] = json.dumps({'message_id': args.reply_to_message_id})
        if args.message_thread_id is not None:
            fields['message_thread_id'] = args.message_thread_id
        telegram_call(token, 'sendVideoNote', fields)
        print('Кружок отправлен в тестовый чат по file_id', flush=True)


def main():
    args = parse_args()
    try:
        run(args)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'Ошибка: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

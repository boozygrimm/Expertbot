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
import time
import urllib.error
import urllib.request

if __package__:
    from . import gemini_tts
else:
    import gemini_tts

HERE = Path(__file__).resolve().parent
TTS_SECRET_VARS = ('GEMINI_TTS_KEYS', 'GEMINI_KEYS', 'GEMINI_API_KEYS', 'GEMINI_API_KEY')


class TelegramRejected(RuntimeError):
    """Telegram explicitly rejected the request without accepting the video."""


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
        error = 'Telegram: ' + str(payload.get('description', 'неизвестная ошибка'))
        code = payload.get('error_code')
        # An explicit 4xx rejection is distinct from a lost response or 5xx.
        if isinstance(code, int) and 400 <= code < 500 and code != 408:
            raise TelegramRejected(error)
        raise RuntimeError(error)
    return payload['result']


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='Ручной PoC видеокружка, не бот и не сервер')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--text', help='Фраза для синтеза (до 480 символов)')
    source.add_argument('--audio', type=Path, help='Готовый моно WAV PCM 16 бит вместо TTS')
    parser.add_argument('--channel-id', type=int, help='ID приватного канала-склада')
    parser.add_argument('--chat-id', type=int, help='ID тестового чата для отправки по file_id')
    parser.add_argument('--reply-to-message-id', type=int, help='ID сообщения в целевом чате, на которое ответить')
    parser.add_argument('--message-thread-id', type=int, help='ID темы форума, если нужен')
    parser.add_argument('--no-send', action='store_true', help='Только создать локальное видео')
    parser.add_argument('--frames-dir', type=Path, default=Path('/data/video-prototype/preview'))
    parser.add_argument('--smoke-plate', type=Path, help='Бездымная подложка для анимации дыма')
    parser.add_argument('--output-dir', type=Path, default=Path('/data/video-prototype/clips'))
    parser.add_argument('--tera-python', default='/data/teratts-test/.venv/bin/python')
    parser.add_argument('--render-python', default='/data/video-prototype/.venv/bin/python')
    parser.add_argument('--hf-home', default='/data/teratts-test/hf')
    parser.add_argument('--seed', type=int, help='Фиксировать случайность TTS для сравнения проб')
    parser.add_argument('--scene-seed', help='Включить сцену с гостями и задать её seed')
    parser.add_argument('--portrait', type=Path, help='Сохранённый портрет сенсея для сцены')
    parser.add_argument('--sign', help='Необязательная надпись для сцены')
    args = parser.parse_args(argv)
    if args.scene_seed is not None and (not args.scene_seed.strip() or not args.smoke_plate or not args.portrait):
        parser.error('Для сцены нужны непустой --scene-seed, --smoke-plate и --portrait')
    if args.scene_seed is None and args.portrait is not None:
        parser.error('--portrait нужен только вместе с --scene-seed')
    if args.text is not None and (not args.text.strip() or len(args.text) > 480):
        parser.error('Текст должен содержать от 1 до 480 символов')
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
    if args.smoke_plate and not args.smoke_plate.is_file():
        raise FileNotFoundError(f'Нет бездымной подложки: {args.smoke_plate}')
    if args.portrait and not args.portrait.is_file():
        raise FileNotFoundError(f'Нет портрета сенсея: {args.portrait}')

    args.output_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='clip-', dir=args.output_dir))
    audio = args.audio
    if args.text is not None:
        audio = work / 'speech.wav'
        env = os.environ.copy()
        env.pop('BOT_TOKEN', None)
        env.pop('VIDEO_WORKER_KEY', None)
        env.pop('STORAGE_CHANNEL_ID', None)
        for name in TTS_SECRET_VARS:
            env.pop(name, None)
        env['HF_HOME'] = args.hf_home
        print(f'Папка результата: {work}', flush=True)
        provider = os.environ.get('BENDER_TTS_PROVIDER', 'tera').strip().lower()
        if provider not in ('tera', 'gemini'):
            raise ValueError('Неизвестный BENDER_TTS_PROVIDER')
        generated = False
        tts_started = time.monotonic()
        if provider == 'gemini':
            keys = gemini_tts.parse_keys(os.environ.get('GEMINI_TTS_KEYS', ''))
            try:
                start_index = int(os.environ.get('BENDER_TTS_START_INDEX', '0'))
            except ValueError:
                start_index = 0
            try:
                gemini_tts.synthesize(args.text, audio, keys, start_index)
                generated = True
            except gemini_tts.TTSFailure as exc:
                print(f'Gemini TTS недоступен ({exc}); резерв Tera', flush=True)
        if not generated:
            command = [
                args.tera_python, str(HERE / 'tera_speech.py'), '--text', args.text,
                '--output', str(audio),
            ]
            if args.seed is not None:
                command.extend(['--seed', str(args.seed)])
            subprocess.run(command, env=env, check=True)
        print(f'Озвучка: {time.monotonic() - tts_started:.1f} с (включая попытки и резерв)', flush=True)

    video = work / 'video.mp4'
    render_script = ('random_guest_render.py' if args.scene_seed is not None else
                     'smoke_layer_render.py' if args.smoke_plate else 'render.py')
    command = [
        args.render_python, str(HERE / render_script), '--audio', str(audio),
        '--closed', str(args.frames_dir / 'closed.png'),
        '--open', str(args.frames_dir / 'open.png'),
        '--blink', str(args.frames_dir / 'blink.png'), '--output', str(video),
    ]
    if args.smoke_plate:
        command.extend(['--plate', str(args.smoke_plate)])
    if args.scene_seed is not None:
        command.extend(['--scene-seed', args.scene_seed, '--portrait', str(args.portrait),
                        '--manifest', str(work / 'scene.json')])
        if args.sign:
            command.append('--sign=' + args.sign)
    # Rendering does not need the Telegram token or the worker key.
    env = os.environ.copy()
    for name in ('BOT_TOKEN', 'VIDEO_WORKER_KEY', 'STORAGE_CHANNEL_ID', *TTS_SECRET_VARS):
        env.pop(name, None)
    render_started = time.monotonic()
    subprocess.run(command, env=env, check=True)
    print(f'Рендер: {time.monotonic() - render_started:.1f} с', flush=True)
    if args.no_send:
        print(f'Локальный результат: {video}')
        return

    token = os.environ.get('BOT_TOKEN') or getpass.getpass('Токен бота (ввод не виден): ')
    if not token.strip():
        raise ValueError('Токен не введён')
    token = token.strip()
    # Persist a marker before the external side effect. If the response is lost,
    # the worker can make this already-rendered file available for direct delivery.
    pending = work / 'storage-upload.pending'
    pending.write_text('pending\n', encoding='utf-8')
    upload_started = time.monotonic()
    try:
        result = telegram_call(token, 'sendVideoNote', {'chat_id': args.channel_id}, video)
    except TelegramRejected:
        # A definite rejection must not be reported as an uncertain upload.
        pending.unlink(missing_ok=True)
        raise
    print(f'Загрузка на склад: {time.monotonic() - upload_started:.1f} с', flush=True)
    file_id = result.get('video_note', {}).get('file_id')
    if not file_id:
        raise RuntimeError('Telegram не вернул file_id видеокружка')
    record = {'channel_id': args.channel_id, 'file_id': file_id, 'video': str(video)}
    record_path = work / 'result.json'
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    pending.unlink(missing_ok=True)
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

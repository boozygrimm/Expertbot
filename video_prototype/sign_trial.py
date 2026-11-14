"""Isolated, fixed-text sign trial on the existing random-guest scene.

This is deliberately not connected to the worker. The sign is a crop of the
approved visual mockup, not the eventual dynamic text implementation.
"""
import argparse
import json
import os
from pathlib import Path
import tempfile
import time

from PIL import Image, ImageDraw, ImageFilter

from . import gemini_tts
from .guest_planner import make_plan
from .mouth_trial_render import quick_levels, render as base_render
from .pipeline import telegram_call
from .random_guest_render import make_effect
from .render import FPS, SIZE

MOCKUP = Path(__file__).resolve().parent / 'trials/sign-preview.jpg'
# Measured in the 768px visual reference. Protect the fingers as in the mockup.
SIGN = [(126, 397), (274, 365), (288, 438), (149, 469)]
HAND = [(108,465),(110,456),(119,444),(130,433),(143,426),(149,425),
        (157,430),(163,436),(162,443),(158,447),(159,451),(165,456),
        (175,455),(183,450),(187,449),(192,456),(200,454),(211,458),
        (219,466),(224,478),(223,493),(213,510),(189,524),(151,526),
        (120,503)]


def prepare_sign(mockup=MOCKUP):
    """Create a transparent sign layer; do not cover the moving scene or hand."""
    with Image.open(mockup) as source:
        if source.width != source.height:
            raise ValueError('Макет таблички должен быть квадратным')
        image = source.convert('RGB').resize((SIZE, SIZE), Image.Resampling.LANCZOS)
    scale = SIZE / 768
    points = lambda vertices: [(round(x * scale), round(y * scale)) for x, y in vertices]
    mask = Image.new('L', (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).polygon(points(SIGN), fill=255)
    hand = Image.new('L', (SIZE, SIZE), 0)
    ImageDraw.Draw(hand).polygon(points(HAND), fill=255)
    hand = hand.filter(ImageFilter.GaussianBlur(1.5 * scale))
    from PIL import ImageChops
    mask = ImageChops.subtract(mask, hand)
    image.putalpha(mask)
    box = mask.getbbox()
    return image.crop(box), box[:2]


def add_sign(frame, layer, position):
    image = Image.frombytes('RGB', (SIZE, SIZE), frame)
    image.paste(layer, position, layer)
    return image.tobytes()


def render_trial(audio, closed, opened, blink, plate, portrait, output, seed='319', manifest=None):
    plan = make_plan(len(quick_levels(audio)), seed, FPS)
    layer, position = prepare_sign()
    guest_effect = make_effect(closed, portrait, plan)

    def effect(frame, seconds):
        # The hedgehog renderer restores the foreground from its input, so the
        # sign must be applied before, not after, the existing guest effects.
        return guest_effect(add_sign(frame, layer, position), seconds)

    if manifest is not None:
        Path(manifest).write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Гости:', ', '.join(a.name for a in plan.guests), flush=True)
    print('Эффекты:', ', '.join(a.name for a in plan.decorations) or 'нет', flush=True)
    base_render(audio, closed, opened, blink, plate, output, frame_effect=effect)
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description='Отдельная проба таблички с гостями')
    parser.add_argument('--chat-id', required=True, type=int)
    parser.add_argument('--frames-dir', type=Path, default=Path('/data/video-prototype/preview'))
    parser.add_argument('--plate', type=Path, default=Path('/data/video-prototype/assets/smoke-free-103.png'))
    parser.add_argument('--portrait', type=Path, default=Path('/data/video-prototype/assets/sensei-da4fc66.img'))
    parser.add_argument('--keys-file', type=Path, default=Path('/data/video-prototype/gemini-tts-keys.txt'))
    parser.add_argument('--output-dir', type=Path, default=Path('/data/video-prototype/clips'))
    parser.add_argument('--seed', default='319')
    parser.add_argument('--audio', type=Path, help='Готовая запись вместо нового синтеза')
    args = parser.parse_args(argv)
    token = os.environ.get('BOT_TOKEN', '').strip()
    if not token:
        parser.error('В окружении нужен BOT_TOKEN')
    for path in (args.frames_dir/'closed.png', args.frames_dir/'open.png', args.frames_dir/'blink.png', args.plate, args.portrait, MOCKUP):
        if not path.is_file():
            parser.error(f'Файл не найден: {path}')
    if args.audio is not None and not args.audio.is_file():
        parser.error('Не найден готовый WAV')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='sign-trial-', dir=args.output_dir))
    print('Папка пробы:', work, flush=True)
    audio = args.audio or work/'speech.wav'
    if args.audio is None:
        if not args.keys_file.is_file():
            parser.error('Не найден файл сохранённых ключей Gemini')
        keys = gemini_tts.parse_keys(args.keys_file.read_text(encoding='utf-8'))
        gemini_tts.synthesize('Новости космоса! Гравитация ушла в отпуск, а я остался работать. '
            'Учёные снова смотрят на звёзды, а я смотрю на счёт за межгалактическую доставку. '
            'Запомните, земляне: если жизнь летит кувырком, возможно, это не проблема, а новый космический аттракцион!', audio, keys)
    video = work/'video.mp4'
    started = time.monotonic()
    render_trial(audio, args.frames_dir/'closed.png', args.frames_dir/'open.png', args.frames_dir/'blink.png',
                 args.plate, args.portrait, video, args.seed, work/'scene.json')
    print(f'Время рендера: {time.monotonic()-started:.1f} с', flush=True)
    print('Отправляем в Telegram один раз; при ошибке сначала проверьте чат.', flush=True)
    telegram_call(token, 'sendVideoNote', {'chat_id': args.chat_id}, video)
    print('Кружок отправлен в тестовый чат', flush=True)


if __name__ == '__main__':
    main()

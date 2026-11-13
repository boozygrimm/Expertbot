"""Experimental LivLiv video-note demo. It never touches the running worker."""
import argparse
from collections import deque
import http.server
import io
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import urllib.request

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

if __package__:
    from .livliv_probe import PLAYER_URL, write_player
    from .pipeline import telegram_call
    from .render import audio_levels, mouth_states
else:
    from livliv_probe import PLAYER_URL, write_player
    from pipeline import telegram_call
    from render import audio_levels, mouth_states

SIZE = 512
# Outline of the head and antenna in the approved 2048px image, at 512px scale.
HEAD_OUTLINE = [
    (259, 152), (259, 135), (261, 117), (270, 103), (284, 96), (301, 93),
    (303, 56), (306, 51), (302, 46), (302, 40), (306, 35), (311, 32),
    (316, 33), (320, 37), (320, 44), (316, 49), (319, 58), (324, 91),
    (337, 94), (352, 99), (365, 110), (373, 122), (376, 135), (379, 153),
    (385, 210), (388, 252), (377, 259), (345, 268), (302, 275), (275, 272),
    (274, 261), (270, 257), (267, 229), (264, 213), (253, 211), (243, 204),
    (238, 197), (238, 182), (241, 171), (247, 163),
]



def fill_hidden_background(image, head_mask):
    """Extend adjacent background into the hidden head, without adding dependencies.

    It is an approximation: the original picture has no actual background behind
    the head. Dilate the region so an outline or antenna does not show through.
    """
    mask = head_mask.point(lambda value: 255 if value >= 32 else 0).filter(ImageFilter.MaxFilter(7))
    pending = mask.copy().load()
    result = image.convert('RGB').copy()
    pixels = result.load()
    width, height = image.size
    queue = deque()
    for y in range(height):
        for x in range(width):
            if not pending[x, y] and any(
                0 <= nx < width and 0 <= ny < height and pending[nx, ny]
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
            ):
                queue.append((x, y))
    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < width and 0 <= ny < height and pending[nx, ny]:
                pending[nx, ny] = 0
                pixels[nx, ny] = pixels[x, y]
                queue.append((nx, ny))
    softened = result.filter(ImageFilter.GaussianBlur(1))
    return Image.composite(softened, image, mask.filter(ImageFilter.GaussianBlur(.5)))


def build_model(root, frames_dir):
    textures = root / 'model' / 'textures'
    textures.mkdir(parents=True)
    images = []
    for filename in ('closed.png', 'open.png', 'blink.png'):
        with Image.open(frames_dir / filename) as image:
            if image.size != (2048, 2048):
                raise ValueError(f'{filename}: ожидалась согласованная картинка 2048×2048')
            images.append(image.convert('RGB').resize((SIZE, SIZE), Image.Resampling.LANCZOS))
    closed, opened, blink = images
    head_mask = Image.new('L', (SIZE, SIZE), 0)
    ImageDraw.Draw(head_mask).polygon(HEAD_OUTLINE, fill=255)
    head_mask = head_mask.filter(ImageFilter.GaussianBlur(.6))
    # Reconstruct only the hidden area from its surroundings. Unlike a blur of
    # the original head, the fill cannot leave a duplicate head behind it.
    background = fill_hidden_background(closed, head_mask)
    background.save(textures / 'background.png')

    def save_patch(image, mask, name):
        patch = image.convert('RGBA')
        patch.putalpha(mask)
        patch.save(textures / name)

    save_patch(closed, head_mask, 'head.png')
    def patch_mask(box, radius, blur):
        mask = Image.new('L', (SIZE, SIZE), 0)
        ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
        return ImageChops.multiply(mask.filter(ImageFilter.GaussianBlur(blur)), head_mask)
    mouth_mask = patch_mask((259, 194, 376, 279), 10, 5)
    eye_mask = patch_mask((246, 150, 365, 210), 10, 3)
    save_patch(closed, mouth_mask, 'mouth_closed.png')
    save_patch(opened, mouth_mask, 'mouth_open.png')
    save_patch(blink, eye_mask, 'eyes_closed.png')

    def layer(layer_id, texture, group, **extra):
        return {'id': layer_id, 'texture': f'textures/{texture}', 'x': 0, 'y': 0,
                'w': SIZE, 'h': SIZE, 'group': group, **extra}
    manifest = {
        'format': 'livliv-model', 'version': 4, 'name': 'Robot experiment',
        'meta': {'generator': {'name': 'expertbot-test'}},
        'canvas': [SIZE, SIZE],
        'anchors': {'head': {'cx': 311, 'cy': 180, 'rx': 76, 'ry': 100},
                    'face': [238, 150, 378, 260], 'neck_y': 275,
                    'top_y': 0, 'bottom_y': 511, 'hair_root_y': 95},
        'expressions': [{'id': 'x_blink', 'label': {'en': 'Blink'}}],
        'layers': [
            layer('x_head', 'head.png', 'head'),
            layer('x_mouth', 'mouth_closed.png', 'head', role='mouth',
                  frames=['textures/mouth_closed.png', 'textures/mouth_open.png', 'textures/mouth_open.png'],
                  visemes=['closed', 'half', 'open'], pivot=[319, 218]),
            layer('x_eyes_closed', 'eyes_closed.png', 'head', role='expr', expression='x_blink'),
        ],
    }
    (root / 'model' / 'model.json').write_text(json.dumps(manifest), encoding='utf-8')


HTML = '''<!doctype html><style>
html,body {margin:0; width:480px; height:480px}
body {background: url('/model/textures/background.png') center / 480px 480px no-repeat}
canvas {width:480px;height:480px}
</style>
<canvas width="480" height="480"></canvas>
<script type="module">
import { Player } from '/dist/index.js';
window.setup = async () => {
  window.player = new Player(document.querySelector('canvas'), {idle: false, followPointer: false, maxPixelRatio: 1});
  await player.load('/model/');
  return player.model.name;
};
window.pose = (angleZ, angleY, mouth, blink) => {
  player.params.angleZ = angleZ;
  player.params.angleY = angleY;
  player.params.mouthOpen = mouth;
  player.setExpression(blink ? 'x_blink' : null);
};
</script>'''


def render_demo(audio, frames_dir, output, chromium='/usr/bin/chromium'):
    from playwright.sync_api import sync_playwright

    levels = audio_levels(audio)
    states = list(mouth_states(levels))
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='livliv-demo-') as temp:
        root = Path(temp)
        build_model(root, frames_dir)
        with urllib.request.urlopen(PLAYER_URL, timeout=60) as response:
            archive = response.read(4 * 1024 * 1024 + 1)
        if len(archive) > 4 * 1024 * 1024:
            raise ValueError('Архив плеера неожиданно велик')
        write_player(root, archive)
        (root / 'index.html').write_text(HTML, encoding='utf-8')
        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(root), **kwargs)
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=chromium, headless=True, args=[
                    '--no-sandbox', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
                ])
                try:
                    page = browser.new_page(viewport={'width': 480, 'height': 480}, device_scale_factor=1)
                    page.goto(f'http://127.0.0.1:{server.server_port}/', wait_until='load')
                    page.wait_for_function('typeof window.setup === "function"')
                    print('Загружаем модель:', page.evaluate('window.setup()'), flush=True)
                    # Assert that the background really stays still while the head
                    # moves; comparing a left strip avoids the head and antenna.
                    page.evaluate('window.pose(0, 0, 0, false)')
                    page.wait_for_timeout(500)
                    first = Image.open(io.BytesIO(page.screenshot())).convert('RGB')
                    page.evaluate('window.pose(6, 0, 0, false)')
                    page.wait_for_timeout(700)
                    second = Image.open(io.BytesIO(page.screenshot())).convert('RGB')
                    diff = ImageChops.difference(first, second)
                    background_delta = sum(ImageStat.Stat(diff.crop((0, 0, 180, 480))).mean) / 3
                    head_delta = sum(ImageStat.Stat(diff.crop((220, 25, 380, 270))).mean) / 3
                    print(f'Изменение фона: {background_delta:.3f}; головы: {head_delta:.3f}', flush=True)
                    if background_delta > .5 or head_delta < .05:
                        raise RuntimeError('Фон движется или движение головы не обнаружено; отправка отменена')
                    page.evaluate('window.pose(0, 0, 0, false)')
                    page.wait_for_timeout(500)
                    cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                           '-s', '480x480', '-r', '24', '-i', 'pipe:0', '-i', str(audio),
                           '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
                           '-c:a', 'aac', '-b:a', '128k', '-shortest', '-movflags', '+faststart', str(output)]
                    process = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                    try:
                        for i, is_open in enumerate(states):
                            t = i / 24
                            blink = (1.3 <= t <= 1.5) or (4.0 <= t <= 4.2)
                            page.evaluate('([z, y, m, b]) => window.pose(z, y, m, b)',
                                          [3 * math.sin(t * 1.1), .7 * math.sin(t * .8), int(is_open), blink])
                            page.wait_for_timeout(35)
                            screenshot = page.screenshot()
                            with Image.open(io.BytesIO(screenshot)) as image:
                                process.stdin.write(image.convert('RGB').tobytes())
                    finally:
                        process.stdin.close()
                        rc = process.wait()
                    if rc:
                        raise RuntimeError('FFmpeg не смог создать видео')
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    print(f'Видео готово: {output}; рендер {time.monotonic() - start:.1f} с', flush=True)


def main():
    parser = argparse.ArgumentParser(description='Тестовый кружок с LivLiv')
    parser.add_argument('--text', required=True)
    parser.add_argument('--chat-id', type=int, required=True)
    parser.add_argument('--frames-dir', type=Path, default=Path('/data/video-prototype/preview'))
    parser.add_argument('--output-dir', type=Path, default=Path('/data/video-prototype/clips'))
    args = parser.parse_args()
    if not args.text.strip() or len(args.text) > 480:
        parser.error('Текст должен содержать от 1 до 480 символов')
    token = os.environ.get('BOT_TOKEN', '').strip()
    if not token:
        parser.error('Нужен BOT_TOKEN в окружении')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='livliv-', dir=args.output_dir))
    print('Папка результата:', work, flush=True)
    env = os.environ.copy()
    for name in ('BOT_TOKEN', 'VIDEO_WORKER_KEY', 'STORAGE_CHANNEL_ID'):
        env.pop(name, None)
    env['HF_HOME'] = '/data/teratts-test/hf'
    subprocess.run(['/data/teratts-test/.venv/bin/python',
                    '/data/video-prototype/code-speech-128a140/video_prototype/tera_speech.py',
                    '--text', args.text, '--output', str(work / 'speech.wav')], env=env, check=True)
    render_demo(work / 'speech.wav', args.frames_dir, work / 'video.mp4')
    # One direct send only; uncertain Telegram failures must not be retried blindly.
    telegram_call(token, 'sendVideoNote', {'chat_id': args.chat_id}, work / 'video.mp4')
    print('Кружок отправлен в тестовый чат', flush=True)


if __name__ == '__main__':
    main()

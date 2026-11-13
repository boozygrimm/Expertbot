"""Isolated LivLiv player smoke test. Does not change or run the worker."""
import argparse
import hashlib
import http.server
import io
import json
from pathlib import Path
import tarfile
import tempfile
import threading
import urllib.request

# Core player, not its separately licensed sample characters.
PLAYER_URL = 'https://registry.npmjs.org/@livliv-dev/player/-/player-0.3.0.tgz'
PLAYER_SHA256 = '0c8d2ea6c8598b6f707c6276501175a62af3f5bad9fe4dfc39a1c0b7dd37b142'


def write_model(root, image_path):
    from PIL import Image
    model = root / 'model'
    (model / 'textures').mkdir(parents=True)
    with Image.open(image_path) as image:
        # This first technical probe deliberately uses one flat image. Its
        # whole background moves too; it is not the finished layered robot.
        image.convert('RGB').resize((512, 512), Image.Resampling.LANCZOS).save(model / 'textures' / 'picture.png')
    manifest = {
        'format': 'livliv-model', 'version': 4, 'name': 'Robot test',
        'meta': {'generator': {'name': 'expertbot-test'}},
        'canvas': [512, 512],
        'anchors': {
            'head': {'cx': 310, 'cy': 210, 'rx': 110, 'ry': 140},
            'face': [245, 145, 370, 285], 'neck_y': 310,
            'top_y': 0, 'bottom_y': 511, 'hair_root_y': 70,
        },
        'layers': [{'id': 'x_picture', 'texture': 'textures/picture.png',
                    'x': 0, 'y': 0, 'w': 512, 'h': 512, 'group': 'head'}],
    }
    (model / 'model.json').write_text(json.dumps(manifest), encoding='utf-8')


def write_player(root, archive):
    if hashlib.sha256(archive).hexdigest() != PLAYER_SHA256:
        raise ValueError('Контрольная сумма LivLiv не совпала')
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
        for member in tar:
            if not member.name.startswith('package/dist/') or not member.isfile():
                continue
            relative = Path(member.name).relative_to('package/dist')
            if '..' in relative.parts or relative.is_absolute():
                raise ValueError('Некорректный путь в архиве')
            dest = root / 'dist' / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, dest.open('wb') as target:
                target.write(source.read())


HTML = '''<!doctype html><style>html,body {margin:0} canvas {width:480px;height:480px}</style>
<canvas width="480" height="480"></canvas>
<script type="module">
import { Player } from '/dist/index.js';
window.setup = async () => {
  window.player = new Player(document.querySelector('canvas'), {idle: false, followPointer: false, maxPixelRatio: 1});
  await player.load('/model/');
  return player.model.name;
};
</script>'''


def run(image, chromium):
    from PIL import Image, ImageChops, ImageStat
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(prefix='livliv-probe-') as temp:
        root = Path(temp)
        write_model(root, image)
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
                    print('Модель:', page.evaluate('window.setup()'), flush=True)
                    page.wait_for_timeout(300)
                    first = page.screenshot()
                    page.evaluate('player.params.angleX = 25; player.params.angleZ = 15')
                    page.wait_for_timeout(1000)
                    second = page.screenshot()
                finally:
                    browser.close()
            a = Image.open(io.BytesIO(first)).convert('RGB')
            b = Image.open(io.BytesIO(second)).convert('RGB')
            difference = ImageStat.Stat(ImageChops.difference(a, b)).mean
            print('Среднее изменение пикселей:', round(sum(difference) / len(difference), 3), flush=True)
            if max(ImageStat.Stat(a).stddev) < 1:
                raise RuntimeError('Кадр однотонный: модель могла не отрисоваться')
            if sum(difference) / len(difference) < 0.1:
                raise RuntimeError('Движение не обнаружено')
            print('Плеер загрузил модель и отрисовал два разных положения.', flush=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


def main():
    parser = argparse.ArgumentParser(description='Изолированная техническая проверка LivLiv')
    parser.add_argument('--image', type=Path, default=Path('/data/video-prototype/preview/closed.png'))
    parser.add_argument('--chromium', default='/usr/bin/chromium')
    args = parser.parse_args()
    run(args.image, args.chromium)


if __name__ == '__main__':
    main()

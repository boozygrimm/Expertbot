import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from video_prototype import livliv_demo, livliv_probe


class LivlivProbeTests(unittest.TestCase):
    def test_model_uses_local_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / 'input.png'
            Image.new('RGB', (64, 64), 'red').save(image)
            livliv_probe.write_model(root, image)
            manifest = json.loads((root / 'model/model.json').read_text())
            self.assertEqual(manifest['version'], 4)
            self.assertEqual(manifest['layers'][0]['group'], 'head')
            with Image.open(root / 'model' / manifest['layers'][0]['texture']) as result:
                self.assertEqual(result.size, (512, 512))

    def test_fill_replaces_hidden_head_but_preserves_outside(self):
        image = Image.new('RGB', (21, 21), 'blue')
        for y in range(8, 13):
            for x in range(8, 13):
                image.putpixel((x, y), (255, 0, 0))
        mask = Image.new('L', image.size, 0)
        for y in range(8, 13):
            for x in range(8, 13):
                mask.putpixel((x, y), 255)
        result = livliv_demo.fill_hidden_background(image, mask)
        self.assertEqual(result.getpixel((10, 10)), (0, 0, 255))
        self.assertEqual(result.getpixel((0, 0)), (0, 0, 255))

    def test_layered_model_is_built_from_three_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            frames = root / 'frames'
            frames.mkdir()
            for name, color in [('closed', 'red'), ('open', 'green'), ('blink', 'blue')]:
                Image.new('RGB', (2048, 2048), color).save(frames / (name + '.png'))
            livliv_demo.build_model(root, frames)
            model_dir = root / 'model'
            model = json.loads((model_dir / 'model.json').read_text())
            self.assertEqual([layer['group'] for layer in model['layers']], ['head', 'head', 'head'])
            self.assertIn("background.png", livliv_demo.HTML)
            mouth = model['layers'][1]
            self.assertEqual(mouth['role'], 'mouth')
            self.assertEqual(len(mouth['frames']), len(mouth['visemes']))
            for layer in model['layers']:
                for path in [layer['texture'], *layer.get('frames', [])]:
                    with Image.open(model_dir / path) as image:
                        self.assertEqual(image.size, (512, 512))
            with Image.open(model_dir / 'textures/mouth_open.png') as image:
                self.assertEqual(image.getpixel((310, 225))[:3], (0, 128, 0))
            with Image.open(model_dir / 'textures/eyes_closed.png') as image:
                self.assertEqual(image.getpixel((310, 180))[:3], (0, 0, 255))

    def test_optional_background_plate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            frames = root / 'frames'
            frames.mkdir()
            for name in ('closed', 'open', 'blink'):
                Image.new('RGB', (2048, 2048), 'red').save(frames / (name + '.png'))
            plate = root / 'plate.png'
            Image.new('RGB', (512, 512), 'blue').save(plate)
            livliv_demo.build_model(root, frames, background_path=plate)
            with Image.open(root / 'model/textures/background.png') as actual:
                self.assertEqual(actual.getpixel((310, 180)), (0, 0, 255))

    def test_archive_verified_and_dist_extracted(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as archive:
            data = b'test'
            member = tarfile.TarInfo('package/dist/index.js')
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        contents = output.getvalue()
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(livliv_probe, 'PLAYER_SHA256', hashlib.sha256(contents).hexdigest()):
                livliv_probe.write_player(Path(temp), contents)
            self.assertEqual((Path(temp) / 'dist/index.js').read_bytes(), b'test')
            with self.assertRaisesRegex(ValueError, 'Контрольная сумма'):
                livliv_probe.write_player(Path(temp), contents)


if __name__ == '__main__':
    unittest.main()

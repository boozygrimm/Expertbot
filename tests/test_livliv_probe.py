import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from video_prototype import livliv_probe


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

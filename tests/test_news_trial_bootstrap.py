import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from news_prototype import start_bender_trial as bootstrap


def archive_for(name, data):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w:gz') as tar:
        info = tarfile.TarInfo(f'Expertbot-{bootstrap.REF}/{name}')
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return result.getvalue()


class NewsTrialBootstrapTests(unittest.TestCase):
    def test_extracts_only_verified_named_files(self):
        name, payload = 'news_prototype/example.py', b'print(42)'
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(bootstrap, 'FILES', {name: hashlib.sha256(payload).hexdigest()}):
            root = Path(folder)
            bootstrap.unpack_checked(archive_for(name, payload), root)
            self.assertEqual((root / name).read_bytes(), payload)

    def test_rejects_tampering(self):
        name = 'news_prototype/example.py'
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(bootstrap, 'FILES', {name: hashlib.sha256(b'expected').hexdigest()}):
            root = Path(folder)
            with self.assertRaises(ValueError):
                bootstrap.unpack_checked(archive_for(name, b'tampered'), root)
            self.assertFalse((root / name).exists())


if __name__ == '__main__':
    unittest.main()

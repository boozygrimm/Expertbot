import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from news_prototype import start_sensei_audition as launcher


class SenseiLauncherTests(unittest.TestCase):
    def test_download_checks_hash_before_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = b'trial code'
            with patch.object(launcher, 'FILES', {'news_prototype/demo.py': hashlib.sha256(data).hexdigest()}), \
                 patch.object(launcher.urllib.request, 'urlopen', return_value=io.BytesIO(data)):
                launcher.download_files(root)
            self.assertEqual((root / 'news_prototype/demo.py').read_bytes(), data)

    def test_rejects_wrong_content(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(launcher, 'FILES', {'news_prototype/demo.py': hashlib.sha256(b'good').hexdigest()}), \
                 patch.object(launcher.urllib.request, 'urlopen', return_value=io.BytesIO(b'bad')):
                with self.assertRaises(ValueError):
                    launcher.download_files(root)
            self.assertFalse((root / 'news_prototype/demo.py').exists())


if __name__ == '__main__':
    unittest.main()

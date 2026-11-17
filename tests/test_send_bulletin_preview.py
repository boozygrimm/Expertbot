import base64
import hashlib
import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from news_prototype import send_bulletin_preview as send


class DeliveryTests(TestCase):
    def test_environment_file_parser(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('export BOT_TOKEN="secret" # comment\nADMIN_ID=123\nOTHER=ignored\n')
            self.assertEqual(send.read_settings(path), {'BOT_TOKEN': 'secret', 'ADMIN_ID': '123'})

    def test_download_checks_hash(self):
        data = b'video'
        payload = json.dumps({'encoding': 'base64', 'content': base64.b64encode(data).decode()}).encode()
        with patch.object(send.urllib.request, 'urlopen', return_value=BytesIO(payload)), \
             patch.object(send, 'SHA256', hashlib.sha256(data).hexdigest()):
            self.assertEqual(send.download(), data)
        with patch.object(send.urllib.request, 'urlopen', return_value=BytesIO(payload)):
            with self.assertRaises(ValueError): send.download()

    def test_one_send_and_private_id(self):
        response = BytesIO(b'{"ok":true}')
        with patch.dict(send.os.environ, {'BOT_TOKEN': 'secret', 'ADMIN_ID': '123'}, clear=True), \
             patch.object(send, 'read_settings', return_value={}), \
             patch.object(send, 'download', return_value=b'video'), \
             patch.object(send.urllib.request, 'urlopen', return_value=response) as call:
            send.main()
        self.assertEqual(call.call_count, 1)
        self.assertIn(b'\r\n\r\n123\r\n', call.call_args.args[0].data)

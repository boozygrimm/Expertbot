import contextlib
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from news_prototype import send_press_trial as sender


class NewsTrialDeliveryTests(unittest.TestCase):
    def run_with_responses(self, responses, video=b'test-video'):
        output = io.StringIO()
        requests = []

        def fake_open(request, timeout):
            requests.append(request)
            result = responses[len(requests) - 1]
            if isinstance(result, Exception):
                raise result
            return io.BytesIO(result)

        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'video.mp4'
            with patch.object(sender, 'load_token', return_value='secret-token'), \
                 patch.object(sender, 'SHA256', hashlib.sha256(video).hexdigest()), \
                 patch.object(sender, 'Path', return_value=destination), \
                 patch.object(sender.urllib.request, 'urlopen', side_effect=fake_open), \
                 contextlib.redirect_stdout(output):
                try:
                    sender.main()
                except SystemExit as exc:
                    print(str(exc), file=output)
            stored = destination.read_bytes() if destination.exists() else None
        self.assertNotIn('secret-token', output.getvalue())
        return requests, output.getvalue(), stored

    def test_sends_verified_video_to_test_chat_once(self):
        requests, output, stored = self.run_with_responses([b'test-video', b'{"ok":true}'])
        self.assertEqual(len(requests), 2)
        self.assertIn(b'-1004395648779', requests[1].data)
        self.assertIn(b'test-video', requests[1].data)
        self.assertEqual(stored, b'test-video')
        self.assertIn('видео отправлено', output)

    def test_invalid_hash_does_not_send(self):
        requests, output, stored = self.run_with_responses([b'wrong'])
        self.assertEqual(len(requests), 1)
        self.assertIsNone(stored)
        self.assertIn('ничего не отправлено', output)

    def test_uncertain_send_is_not_retried_or_leaked(self):
        requests, output, stored = self.run_with_responses([b'test-video', OSError('secret-token')])
        self.assertEqual(len(requests), 2)
        self.assertEqual(stored, b'test-video')
        self.assertIn('Проверь тестовый чат', output)


if __name__ == '__main__':
    unittest.main()

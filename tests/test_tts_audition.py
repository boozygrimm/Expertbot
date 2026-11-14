import base64
import io
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from video_prototype import tts_audition as t


class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


class AuditionTests(unittest.TestCase):
    def test_gemini_pcm_and_key_header(self):
        body = {'candidates': [{'content': {'parts': [{'inlineData': {
            'mimeType': 'audio/L16; rate=24000', 'data': base64.b64encode(b'\0\0' * 100).decode()}}]}}]}
        with tempfile.TemporaryDirectory() as d, patch.object(t.urllib.request, 'urlopen', return_value=Response(json.dumps(body).encode())) as urlopen:
            path = Path(d) / 'a.wav'
            t.gemini_voice('secret-key', 'Charon', 'Привет', path)
            self.assertNotIn('secret-key', urlopen.call_args.args[0].full_url)
            with wave.open(str(path)) as f:
                self.assertEqual((f.getframerate(), f.getnchannels(), f.getnframes()), (24000, 1, 100))

    def test_unexpected_response_does_not_create_wav(self):
        with tempfile.TemporaryDirectory() as d, patch.object(t.urllib.request, 'urlopen', return_value=Response(b'{}')):
            path = Path(d) / 'a.wav'
            with self.assertRaises(RuntimeError): t.gemini_voice('key', 'Fenrir', 'текст', path)
            self.assertFalse(path.exists())

    def test_send_failure_stops_additional_samples(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(t.os.environ, {'BOT_TOKEN': 'test'}), \
             patch.object(t, 'edge_voice', side_effect=lambda text, path: path.write_bytes(b'a')), \
             patch.object(t, 'send_document', side_effect=RuntimeError('not confirmed')) as send:
            self.assertEqual(t.main(['--chat-id', '123', '--output-dir', d]), 1)
            self.assertEqual(send.call_count, 1)

if __name__ == '__main__': unittest.main()

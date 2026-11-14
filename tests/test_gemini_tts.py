import base64
import io
import json
import tempfile
import unittest
import urllib.error
import wave
from pathlib import Path
from unittest.mock import patch

from video_prototype import gemini_tts as t


def make_wav(frames=b'\x01\0' * 2400, rate=24000, channels=1):
    out = io.BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(frames)
    return out.getvalue()


def result(data, mime='audio/wav', finish='STOP'):
    return {'candidates': [{'finishReason': finish, 'content': {'parts': [
        {'inlineData': {'mimeType': mime, 'data': base64.b64encode(data).decode()}}]}}]}


class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


class GeminiTests(unittest.TestCase):
    def test_parse_keys_deduplicates_and_preserves_order(self):
        self.assertEqual(t.parse_keys(' a, b,,a,c '), ['a', 'b', 'c'])

    def test_wav_and_raw_formats(self):
        expected = make_wav()
        actual, seconds = t.validated_audio(result(expected))
        self.assertEqual(actual, expected)
        self.assertAlmostEqual(seconds, .1)
        actual, _ = t.validated_audio(result(b'\x01\x00' * 2400, 'audio/L16; rate=24000; channels=1'))
        with wave.open(io.BytesIO(actual), 'rb') as wav:
            self.assertEqual((wav.getnchannels(), wav.getframerate(), wav.getnframes()), (1, 24000, 2400))

    def test_invalid_truncated_silent_and_overlong_rejected(self):
        cases = [result(b'bad', 'audio/ogg'), result(make_wav(b'\0\0' * 2400)),
                 result(make_wav()[:-10]), result(make_wav(channels=2)),
                 result(make_wav(b'\1\0' * (24000 * 60))), result(make_wav(), finish='MAX_TOKENS'),
                 result(b'\1\0' * 100, 'audio/L16; rate=16000'), {}]
        for case in cases:
            with self.subTest(case=str(case)[:60]), self.assertRaises(t.TTSFailure):
                t.validated_audio(case)

    def test_request_uses_fixed_voice_header_and_blocks_redirect(self):
        opener = unittest.mock.Mock()
        opener.open.return_value = Response(json.dumps(result(make_wav())).encode())
        with patch.object(t.urllib.request, 'build_opener', return_value=opener) as build:
            t.request_one('hidden-key', 'Текст', 12)
        self.assertIsInstance(build.call_args.args[0], t.NoRedirect)
        request = opener.open.call_args.args[0]
        self.assertNotIn('hidden-key', request.full_url)
        self.assertEqual(request.get_header('X-goog-api-key'), 'hidden-key')
        body = json.loads(request.data)
        self.assertEqual(body['generationConfig']['speechConfig']['voiceConfig']['voice'], 'Algenib')
        self.assertIsNone(t.NoRedirect().redirect_request(request, None, 302, '', {}, 'https://evil.example'))

    def test_http_error_does_not_expose_contents(self):
        opener = unittest.mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError('https://example/?secret', 429, 'SECRET', {}, None)
        with patch.object(t.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(t.TTSFailure) as ex:
                t.request_one('key-secret', 'private text', 5)
        self.assertEqual(str(ex.exception), 'HTTP 429')

    def test_rotation_and_fallback_to_next_key(self):
        audio = make_wav()
        with tempfile.TemporaryDirectory() as d, \
             patch.object(t, 'request_one', side_effect=[t.TTSFailure('HTTP 429'), (audio, .1)]) as request:
            path = Path(d) / 'result.wav'
            t.synthesize('text', path, ['a', 'b', 'c'], start_index=1)
            self.assertEqual([x.args[0] for x in request.call_args_list], ['b', 'c'])
            self.assertEqual(path.read_bytes(), audio)

    def test_failure_exhausts_keys_without_output(self):
        with tempfile.TemporaryDirectory() as d, \
             patch.object(t, 'request_one', side_effect=t.TTSFailure('HTTP 429')) as request:
            path = Path(d) / 'result.wav'
            with self.assertRaises(t.TTSFailure):
                t.synthesize('text', path, ['a', 'b', 'c'], start_index=2)
            self.assertEqual([x.args[0] for x in request.call_args_list], ['c', 'a', 'b'])
            self.assertFalse(path.exists())

    def test_deadline_prevents_starting_next_attempt(self):
        with patch.object(t.time, 'monotonic', side_effect=[0, 0, 46]), \
             patch.object(t, 'request_one', side_effect=t.TTSFailure('timeout')) as request:
            with self.assertRaises(t.TTSFailure):
                t.synthesize('text', '/unused', ['a', 'b'])
            self.assertEqual(request.call_count, 1)
            self.assertEqual(request.call_args.args[2], 25)

    def test_zero_budget_and_empty_keys_do_not_call_api(self):
        with patch.object(t, 'request_one') as request:
            for keys, budget in [([], 75), (['a'], 0)]:
                with self.assertRaises(t.TTSFailure): t.synthesize('text', '/unused', keys, budget=budget)
            request.assert_not_called()


if __name__ == '__main__': unittest.main()

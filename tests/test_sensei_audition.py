import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from news_prototype import sensei_audition as audition


class SenseiAuditionTests(unittest.TestCase):
    def exercise(self, outcomes):
        output = io.StringIO()
        voices = []
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            def synth(text, output_path, keys, start_index=0):
                voices.append((audition.gemini_tts.VOICE, text, start_index))
                output_path.write_bytes(b'wav')
            def convert(command, **kwargs):
                Path(command[-1]).write_bytes(b'ogg')
            before = audition.gemini_tts.VOICE, audition.gemini_tts.STYLE
            with patch.object(audition, 'load_token', return_value='secret-token'), \
                 patch.object(audition, 'load_keys', return_value=['secret-key']), \
                 patch.object(audition.shutil, 'which', return_value='/usr/bin/ffmpeg'), \
                 patch.object(audition.tempfile, 'mkdtemp', return_value=str(folder)), \
                 patch.object(audition.Path, 'mkdir'), \
                 patch.object(audition.gemini_tts, 'synthesize', side_effect=synth), \
                 patch.object(audition.subprocess, 'run', side_effect=convert), \
                 patch.object(audition, 'send_voice', side_effect=outcomes) as send, \
                 contextlib.redirect_stdout(output):
                audition.main()
            self.assertEqual((audition.gemini_tts.VOICE, audition.gemini_tts.STYLE), before)
            self.assertNotIn('secret-token', output.getvalue())
            self.assertNotIn('secret-key', output.getvalue())
            return voices, send.call_count, output.getvalue()

    def test_three_voices_speak_the_same_text(self):
        voices, calls, output = self.exercise([True, True, True])
        self.assertEqual([v[0] for v in voices], list(audition.VOICES))
        self.assertEqual(len({v[1] for v in voices}), 1)
        self.assertEqual(calls, 3)
        self.assertIn('Отправлено проб: 3', output)

    def test_uncertain_send_stops_without_generating_or_sending_next(self):
        voices, calls, output = self.exercise([OSError('secret-token')])
        self.assertEqual(len(voices), 1)
        self.assertEqual(calls, 1)
        self.assertIn('Проверьте чат перед повтором', output)


if __name__ == '__main__':
    unittest.main()

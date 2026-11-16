import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from news_prototype import podcast_audio_trial as trial


def write_example(path: Path, frames: int = 240):
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(trial.RATE)
        wav.writeframes(b'\x01\x00' * frames)


class PodcastAudioTrialTests(unittest.TestCase):
    def test_assemble_with_silence_between_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            one, two, result = root / 'one.wav', root / 'two.wav', root / 'result.wav'
            write_example(one, 240)
            write_example(two, 480)
            trial.assemble_wav([one, two], result)
            with wave.open(str(result), 'rb') as wav:
                gap_frames = round(trial.RATE * trial.GAP_MS / 1000)
                self.assertEqual(wav.getnframes(), 240 + gap_frames + 480)
                wav.readframes(240)
                self.assertEqual(wav.readframes(gap_frames), bytes(2 * gap_frames))

    def test_four_expected_voices_and_one_send(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = io.StringIO()
            voices = []
            def synth(text, path, keys, start_index=0):
                voices.append(trial.gemini_tts.VOICE)
                write_example(path)
            def convert(command, **kwargs):
                path = Path(command[-1])
                if path.suffix == '.wav':
                    write_example(path)
                else:
                    path.write_bytes(b'ogg')
            before = trial.gemini_tts.VOICE, trial.gemini_tts.STYLE
            with patch.object(trial, 'BASE', root), \
                 patch.object(trial, 'load_token', return_value='private-token'), \
                 patch.object(trial, 'load_keys', return_value=['private-key']), \
                 patch.object(trial.shutil, 'which', return_value='/usr/bin/ffmpeg'), \
                 patch.object(trial.tempfile, 'mkdtemp', return_value=str(root)), \
                 patch.object(trial.gemini_tts, 'synthesize', side_effect=synth), \
                 patch.object(trial.subprocess, 'run', side_effect=convert), \
                 patch.object(trial, 'send_audio', return_value=True) as send, \
                 contextlib.redirect_stdout(output):
                trial.main()
            self.assertEqual(voices, ['Algenib', 'Iapetus', 'Algenib', 'Iapetus'])
            self.assertEqual(send.call_count, 1)
            self.assertEqual((trial.gemini_tts.VOICE, trial.gemini_tts.STYLE), before)
            self.assertTrue((root / 'dialogue.wav').is_file())
            self.assertNotIn('private-token', output.getvalue())
            self.assertNotIn('private-key', output.getvalue())

    def test_tts_failure_stops_without_delivery_or_voice_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(trial, 'BASE', root), \
                 patch.object(trial, 'load_token', return_value='token'), \
                 patch.object(trial, 'load_keys', return_value=['key']), \
                 patch.object(trial.shutil, 'which', return_value='/usr/bin/ffmpeg'), \
                 patch.object(trial.tempfile, 'mkdtemp', return_value=str(root)), \
                 patch.object(trial.gemini_tts, 'synthesize', side_effect=trial.gemini_tts.TTSFailure('unavailable')), \
                 patch.object(trial, 'send_audio') as send, \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit):
                    trial.main()
            send.assert_not_called()

    def test_uncertain_delivery_no_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = io.StringIO()
            def synth(text, path, keys, start_index=0):
                write_example(path)
            def convert(command, **kwargs):
                path = Path(command[-1])
                if path.suffix == '.wav':
                    write_example(path)
                else:
                    path.write_bytes(b'ogg')
            with patch.object(trial, 'BASE', root), \
                 patch.object(trial, 'load_token', return_value='private-token'), \
                 patch.object(trial, 'load_keys', return_value=['key']), \
                 patch.object(trial.shutil, 'which', return_value='/usr/bin/ffmpeg'), \
                 patch.object(trial.tempfile, 'mkdtemp', return_value=str(root)), \
                 patch.object(trial.gemini_tts, 'synthesize', side_effect=synth), \
                 patch.object(trial.subprocess, 'run', side_effect=convert), \
                 patch.object(trial, 'send_audio', side_effect=OSError('private-token')) as send, \
                 contextlib.redirect_stdout(output):
                trial.main()
            self.assertEqual(send.call_count, 1)
            self.assertNotIn('private-token', output.getvalue())
            self.assertIn('Проверьте чат', output.getvalue())
            self.assertTrue((root / 'dialogue.ogg').exists())


if __name__ == '__main__':
    unittest.main()

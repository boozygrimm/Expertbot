import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from video_prototype import voice_compare


class VoiceCompareTests(unittest.TestCase):
    @patch('video_prototype.voice_compare.telegram_call')
    @patch('video_prototype.voice_compare.send_wav')
    @patch('video_prototype.voice_compare.subprocess.run')
    @patch.dict(os.environ, {'BOT_TOKEN': 'secret', 'VIDEO_WORKER_KEY': 'key', 'STORAGE_CHANNEL_ID': '123'})
    def test_synthesizes_once_then_renders_same_wav_and_sends_both(self, run, send_wav, send_video):
        with tempfile.TemporaryDirectory() as temp:
            voice_compare.main(['--text', 'Привет, кожаный мешок!', '--chat-id', '-1002', '--output-dir', temp])
            self.assertEqual(run.call_count, 2)
            tts, renderer = [call.args[0] for call in run.call_args_list]
            wav = tts[tts.index('--output') + 1]
            self.assertEqual(renderer[renderer.index('--audio') + 1], wav)
            self.assertEqual(Path(wav), send_wav.call_args.args[2])
            self.assertEqual(send_video.call_args.args[1], 'sendVideoNote')
            self.assertEqual(send_video.call_args.args[2], {'chat_id': -1002})
            for call in run.call_args_list:
                self.assertNotIn('BOT_TOKEN', call.kwargs['env'])
                self.assertNotIn('VIDEO_WORKER_KEY', call.kwargs['env'])
                self.assertNotIn('STORAGE_CHANNEL_ID', call.kwargs['env'])

    @patch('video_prototype.voice_compare.telegram_call')
    @patch('video_prototype.voice_compare.send_wav', side_effect=RuntimeError('unknown'))
    @patch('video_prototype.voice_compare.subprocess.run')
    @patch.dict(os.environ, {'BOT_TOKEN': 'secret'})
    def test_unknown_document_send_is_not_retried_and_video_is_not_sent(self, run, send_wav, send_video):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(RuntimeError):
                voice_compare.main(['--text', 'Привет!', '--chat-id', '-1002', '--output-dir', temp])
            send_wav.assert_called_once()
            send_video.assert_not_called()


if __name__ == '__main__':
    unittest.main()

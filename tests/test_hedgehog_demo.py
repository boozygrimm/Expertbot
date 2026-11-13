import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from video_prototype import hedgehog_demo


class DemoTests(unittest.TestCase):
    @patch.dict(os.environ, {'BOT_TOKEN': 'token'})
    @patch('video_prototype.hedgehog_demo.telegram_call')
    @patch('video_prototype.hedgehog_demo.render')
    @patch('video_prototype.hedgehog_demo.subprocess.run')
    def test_existing_audio_renders_and_sends_without_synthesis(self, run, render, send):
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / 'existing.wav'
            audio.touch()
            args = ['hedgehog_demo.py', '--audio', str(audio), '--plate', 'plate.png',
                    '--chat-id', '-1002', '--output-dir', folder]
            with patch('sys.argv', args):
                hedgehog_demo.main()
            run.assert_not_called()
            self.assertEqual(render.call_args.args[0], audio)
            self.assertEqual(send.call_args.args[1], 'sendVideoNote')
            self.assertEqual(send.call_args.args[2], {'chat_id': -1002})


if __name__ == '__main__':
    unittest.main()

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from video_prototype import multi_gaze_demo


class DemoTests(unittest.TestCase):
    @patch.dict(os.environ, {'BOT_TOKEN': 'secret', 'VIDEO_WORKER_KEY': 'key', 'STORAGE_CHANNEL_ID': '-100'})
    @patch('video_prototype.multi_gaze_demo.telegram_call')
    @patch('video_prototype.multi_gaze_demo.render')
    @patch('video_prototype.multi_gaze_demo.subprocess.run')
    def test_one_synthesis_render_and_send(self, synth, render, send):
        with tempfile.TemporaryDirectory() as folder:
            plate, portrait = Path(folder)/'plate.png', Path(folder)/'portrait.png'
            plate.touch(); portrait.touch()
            args = ['demo', '--text', 'Новая фраза', '--plate', str(plate),
                    '--chat-id', '-1002', '--output-dir', folder, '--portrait', str(portrait)]
            with patch('sys.argv', args):
                multi_gaze_demo.main()
            synth.assert_called_once()
            self.assertEqual(synth.call_args.kwargs['check'], True)
            self.assertNotIn('BOT_TOKEN', synth.call_args.kwargs['env'])
            self.assertNotIn('VIDEO_WORKER_KEY', synth.call_args.kwargs['env'])
            render.assert_called_once()
            self.assertEqual(render.call_args.args[-2], portrait)
            send.assert_called_once()
            self.assertEqual(send.call_args.args[1], 'sendVideoNote')

if __name__ == '__main__':
    unittest.main()

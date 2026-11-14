import os
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch
from video_prototype import random_guest_demo as demo
from video_prototype.guest_planner import select_guests


class RandomGuestDemoTests(unittest.TestCase):
    def seed_with(self, needs_sensei):
        return next(str(i) for i in range(100) if ('pai_mei' in select_guests(random.Random(str(i)))) == needs_sensei)

    @patch.dict(os.environ, {'BOT_TOKEN':'secret','VIDEO_WORKER_KEY':'worker','STORAGE_CHANNEL_ID':'-100'})
    @patch('video_prototype.random_guest_demo.telegram_call')
    @patch('video_prototype.random_guest_demo.render')
    @patch('video_prototype.random_guest_demo.subprocess.run')
    def test_one_synthesis_render_send_and_secret_isolation(self, synth, render, send):
        with tempfile.TemporaryDirectory() as folder:
            plate = Path(folder)/'plate.png'; plate.touch()
            seed = self.seed_with(False)
            args = ['demo','--text','Новая фраза','--plate',str(plate),'--chat-id','-1002',
                    '--output-dir',folder,'--seed',seed]
            with patch('sys.argv',args): demo.main()
            synth.assert_called_once()
            env = synth.call_args.kwargs['env']
            for secret in ('BOT_TOKEN','VIDEO_WORKER_KEY','STORAGE_CHANNEL_ID'):
                self.assertNotIn(secret,env)
            render.assert_called_once()
            self.assertEqual(render.call_args.args[-2],seed)
            self.assertEqual(render.call_args.args[-1].name,'scene.json')
            send.assert_called_once()
            self.assertEqual(send.call_args.args[1],'sendVideoNote')

    @patch.dict(os.environ, {'BOT_TOKEN':'secret'})
    @patch('video_prototype.random_guest_demo.subprocess.run')
    @patch('video_prototype.random_guest_demo.telegram_call')
    def test_missing_sensei_is_detected_before_synthesis_and_send(self, send, synth):
        with tempfile.TemporaryDirectory() as folder:
            plate = Path(folder)/'plate.png'; plate.touch()
            args = ['demo','--text','Фраза','--plate',str(plate),'--chat-id','-1002',
                    '--output-dir',folder,'--seed',self.seed_with(True)]
            with patch('sys.argv',args), patch('sys.stderr'):
                with self.assertRaises(SystemExit): demo.main()
            synth.assert_not_called(); send.assert_not_called()


if __name__ == '__main__':
    unittest.main()

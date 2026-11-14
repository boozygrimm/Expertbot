import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from video_prototype import edge_long_trial as t


class EdgeLongTests(unittest.TestCase):
    def test_split_preserves_words_and_punctuation(self):
        text = 'Первая фраза. И ещё одна фраза с достаточно длинным продолжением!'
        parts = t.split_text(text, 20)
        self.assertEqual(' '.join(parts), text)
        self.assertTrue(all(len(part) <= 20 for part in parts))

    def test_one_bad_piece_prevents_all_sending(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(t.os.environ, {'BOT_TOKEN': 'token'}), \
             patch.object(t, 'synthesize', side_effect=RuntimeError('offline')), \
             patch.object(t, 'send_document') as send:
            self.assertEqual(t.main(['--output-dir', d, '--chat-id', '123']), 1)
            send.assert_not_called()

    def test_bad_send_stops_following_send(self):
        def generate(text, path, env): path.write_bytes(b'fake')
        with tempfile.TemporaryDirectory() as d, patch.dict(t.os.environ, {'BOT_TOKEN': 'token'}), \
             patch.object(t, 'synthesize', side_effect=generate), \
             patch.object(t, 'join_parts'), patch.object(t, 'make_lower'), \
             patch.object(t, 'send_document', side_effect=RuntimeError('unconfirmed')) as send:
            self.assertEqual(t.main(['--output-dir', d, '--chat-id', '123']), 1)
            self.assertEqual(send.call_count, 1)

if __name__ == '__main__': unittest.main()

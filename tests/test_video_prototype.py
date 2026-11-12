import contextlib
import io
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from video_prototype import pipeline, render


class RenderTests(unittest.TestCase):
    def make_wav(self, path, seconds=1, channels=1):
        with wave.open(str(path), 'wb') as out:
            out.setnchannels(channels)
            out.setsampwidth(2)
            out.setframerate(24000)
            out.writeframes(b'\0\0' * 24000 * seconds * channels)

    def test_silence_produces_closed_mouth(self):
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / 'audio.wav'
            self.make_wav(audio)
            levels = render.audio_levels(audio)
            self.assertEqual(len(levels), 24)
            self.assertFalse(any(render.mouth_states(levels)))

    def test_bad_audio_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / 'stereo.wav'
            self.make_wav(audio, channels=2)
            with self.assertRaisesRegex(ValueError, 'моно'):
                render.audio_levels(audio)
            self.make_wav(audio, seconds=60)
            with self.assertRaisesRegex(ValueError, '60'):
                render.audio_levels(audio)

    def test_hysteresis_and_blinks(self):
        self.assertEqual(list(render.mouth_states([0, 20, 10, 5, 100, 0])),
                         [False, True, True, False, True, False])
        self.assertFalse(render.blink_at(0))
        self.assertTrue(render.blink_at(32))
        self.assertTrue(render.blink_at(96))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.frames = self.folder / 'frames'
        self.frames.mkdir()
        for name in ('closed.png', 'open.png', 'blink.png'):
            (self.frames / name).write_bytes(b'dummy')
        self.audio = self.folder / 'speech.wav'
        self.audio.write_bytes(b'dummy')
        self.output = self.folder / 'clips'

    def args(self, *extra):
        return pipeline.parse_args(['--audio', str(self.audio), '--frames-dir', str(self.frames),
                                    '--output-dir', str(self.output), *extra])

    def fake_render(self, command, **kwargs):
        self.assertIn('render.py', command[1])
        Path(command[command.index('--output') + 1]).write_bytes(b'fake mp4')

    @patch('video_prototype.pipeline.subprocess.run')
    @patch('video_prototype.pipeline.telegram_call')
    @patch.dict('os.environ', {'BOT_TOKEN': 'test-token'})
    def test_channel_upload_then_chat_uses_file_id(self, telegram, subprocess_run):
        subprocess_run.side_effect = self.fake_render
        telegram.side_effect = [{'video_note': {'file_id': 'cached-id'}}, {}]
        pipeline.run(self.args('--channel-id', '-1001', '--chat-id', '-1002'))
        self.assertEqual(telegram.call_args_list[0].args[2], {'chat_id': -1001})
        self.assertEqual(telegram.call_args_list[1].args[2], {'chat_id': -1002, 'video_note': 'cached-id'})
        saved = json.loads(next(self.output.glob('clip-*/result.json')).read_text())
        self.assertEqual(saved['file_id'], 'cached-id')
        self.assertNotIn('test-token', json.dumps(saved))

    @patch('video_prototype.pipeline.subprocess.run')
    @patch('video_prototype.pipeline.telegram_call')
    @patch.dict('os.environ', {'BOT_TOKEN': 'test-token'})
    def test_reply_and_thread_apply_only_to_target_chat(self, telegram, subprocess_run):
        subprocess_run.side_effect = self.fake_render
        telegram.side_effect = [{'video_note': {'file_id': 'cached-id'}}, {}]
        pipeline.run(self.args('--channel-id', '-1001', '--chat-id', '-1002',
                               '--reply-to-message-id', '42', '--message-thread-id', '7'))
        self.assertEqual(telegram.call_args_list[0].args[2], {'chat_id': -1001})
        fields = telegram.call_args_list[1].args[2]
        self.assertEqual(fields['chat_id'], -1002)
        self.assertEqual(fields['message_thread_id'], 7)
        self.assertEqual(json.loads(fields['reply_parameters']), {'message_id': 42})

    def test_reply_needs_chat(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.args('--channel-id', '-1001', '--reply-to-message-id', '42')

    @patch('video_prototype.pipeline.subprocess.run')
    @patch('video_prototype.pipeline.telegram_call')
    @patch.dict('os.environ', {'BOT_TOKEN': 'test-token'})
    def test_file_id_remains_if_chat_fails(self, telegram, subprocess_run):
        subprocess_run.side_effect = self.fake_render
        telegram.side_effect = [{'video_note': {'file_id': 'cached-id'}}, RuntimeError('offline')]
        with self.assertRaisesRegex(RuntimeError, 'offline'):
            pipeline.run(self.args('--channel-id', '-1001', '--chat-id', '-1002'))
        self.assertEqual(json.loads(next(self.output.glob('clip-*/result.json')).read_text())['file_id'], 'cached-id')

    @patch('video_prototype.pipeline.subprocess.run')
    @patch('video_prototype.pipeline.telegram_call')
    def test_local_only(self, telegram, subprocess_run):
        subprocess_run.side_effect = self.fake_render
        pipeline.run(self.args('--no-send'))
        telegram.assert_not_called()
        self.assertTrue(next(self.output.glob('clip-*/video.mp4')).exists())

    @patch('video_prototype.pipeline.subprocess.run')
    def test_text_runs_qwen_then_renderer(self, subprocess_run):
        reference = self.folder / 'reference.wav'
        reference.write_bytes(b'reference')
        calls = []
        environments = []
        def fake_run(command, **kwargs):
            calls.append(command)
            environments.append(kwargs.get('env'))
            Path(command[command.index('--output') + 1]).write_bytes(b'generated')
        subprocess_run.side_effect = fake_run
        args = pipeline.parse_args(['--text', 'Проверка', '--reference', str(reference),
            '--frames-dir', str(self.frames), '--output-dir', str(self.output), '--no-send'])
        pipeline.run(args)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0][1].endswith('speech.py'))
        self.assertTrue(calls[1][1].endswith('render.py'))
        self.assertIn('Проверка', calls[0])
        self.assertEqual(calls[0][calls[0].index('--max-new-tokens') + 1], '768')
        self.assertNotIn('BOT_TOKEN', environments[0])
        self.assertEqual(calls[1][calls[1].index('--audio') + 1],
                         calls[0][calls[0].index('--output') + 1])

    @patch('video_prototype.pipeline.subprocess.run')
    def test_stress_is_opt_in_and_preserves_reference_text(self, subprocess_run):
        reference = self.folder / 'reference.wav'
        reference.write_bytes(b'reference')
        calls = []
        def fake_run(command, **kwargs):
            calls.append(command)
            Path(command[command.index('--output') + 1]).write_bytes(b'generated')
        subprocess_run.side_effect = fake_run
        args = pipeline.parse_args(['--text', 'Укуси, Бендер!', '--reference', str(reference),
            '--reference-text', 'точная запись', '--frames-dir', str(self.frames),
            '--output-dir', str(self.output), '--no-send', '--stress-marks', '--seed', '42'])
        pipeline.run(args)
        self.assertEqual(calls[0][calls[0].index('--text') + 1], 'Укуси́, Бе́ндер!')
        self.assertEqual(calls[0][calls[0].index('--reference-text') + 1], 'точная запись')
        self.assertEqual(calls[0][calls[0].index('--seed') + 1], '42')

    def test_must_choose_channel_or_local_only(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.args()

    @patch('video_prototype.pipeline.urllib.request.urlopen')
    def test_upload_multipart(self, urlopen):
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        urlopen.return_value = Response(b'{"ok":true,"result":{"video_note":{"file_id":"ok"}}}')
        video = self.folder / 'clip.mp4'
        video.write_bytes(b'video-data')
        result = pipeline.telegram_call('test-token', 'sendVideoNote', {'chat_id': -1001}, video)
        self.assertEqual(result['video_note']['file_id'], 'ok')
        request = urlopen.call_args.args[0]
        self.assertIn(b'video-data', request.data)
        self.assertIn(b'name="chat_id"', request.data)
        self.assertIn(b'name="video_note"', request.data)


if __name__ == '__main__':
    unittest.main()

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from video_prototype import pipeline


class PipelineGeminiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.frames = self.root / 'frames'; self.frames.mkdir()
        for name in ('closed.png', 'open.png', 'blink.png'): (self.frames / name).touch()
        self.args = pipeline.parse_args(['--text', 'Привет', '--no-send', '--frames-dir', str(self.frames),
                                         '--output-dir', str(self.root / 'clips')])

    def test_gemini_success_skips_tera_and_secrets_not_given_to_renderer(self):
        def generate(text, output, keys, index):
            self.assertEqual(keys, ['key1', 'key2'])
            self.assertEqual(index, 1)
            output.write_bytes(b'WAV')
        with patch.dict(os.environ, {'BENDER_TTS_PROVIDER': 'gemini', 'BENDER_TTS_START_INDEX': '1',
                                     'GEMINI_TTS_KEYS': 'key1,key2', 'BOT_TOKEN': 'bot'}, clear=True), \
             patch.object(pipeline.gemini_tts, 'synthesize', side_effect=generate) as gemini, \
             patch.object(pipeline.subprocess, 'run') as run:
            pipeline.run(self.args)
            gemini.assert_called_once()
            self.assertEqual(run.call_count, 1)
            self.assertTrue(run.call_args.args[0][1].endswith('render.py'))
            for secret in ('GEMINI_TTS_KEYS', 'BOT_TOKEN'):
                self.assertNotIn(secret, run.call_args.kwargs['env'])

    def test_gemini_failure_falls_back_to_tera_without_leaking_keys(self):
        with patch.dict(os.environ, {'BENDER_TTS_PROVIDER': 'gemini', 'GEMINI_TTS_KEYS': 'key1,key2'}, clear=True), \
             patch.object(pipeline.gemini_tts, 'synthesize', side_effect=pipeline.gemini_tts.TTSFailure('quota')), \
             patch.object(pipeline.subprocess, 'run') as run:
            pipeline.run(self.args)
            self.assertEqual(run.call_count, 2)
            self.assertTrue(run.call_args_list[0].args[0][1].endswith('tera_speech.py'))
            for call in run.call_args_list:
                self.assertNotIn('GEMINI_TTS_KEYS', call.kwargs['env'])

    def test_unset_provider_stays_tera_and_bad_provider_fails_closed(self):
        for provider in ('', 'wrong'):
            with self.subTest(provider=provider), \
                 patch.dict(os.environ, {'BENDER_TTS_PROVIDER': provider} if provider else {}, clear=True), \
                 patch.object(pipeline.gemini_tts, 'synthesize') as gemini, \
                 patch.object(pipeline.subprocess, 'run') as run:
                if provider:
                    with self.assertRaises(ValueError): pipeline.run(self.args)
                    run.assert_not_called()
                else:
                    pipeline.run(self.args)
                    self.assertTrue(run.call_args_list[0].args[0][1].endswith('tera_speech.py'))
                gemini.assert_not_called()

if __name__ == '__main__': unittest.main()

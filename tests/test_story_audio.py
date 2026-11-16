from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

try:
    import imageio_ffmpeg
    from news_prototype import story_audio, story_video
    from tests.test_podcast_video_trial import make_segment
except ImportError:
    story_audio = None


@unittest.skipIf(story_audio is None, 'Pillow and imageio-ffmpeg are optional for isolated tests')
class StoryAudioTests(unittest.TestCase):
    def setUp(self):
        self.script = {'turns': [{'speaker':'Бендер', 'text':'Одна новость.'},
                                 {'speaker':'Киберсенсей', 'text':'Уточнение.'}],
                       'ticker':'ПРОБА'}

    def test_invalid_script_does_not_truncate_or_start(self):
        for turns in ([], [{'speaker':'Бендер', 'text':'x'*481}],
                      [{'speaker':'Другой', 'text':'Текст'}], [{'speaker':[], 'text':'Текст'}]):
            with self.assertRaises(ValueError): story_audio.validate_script({'turns':turns})

    def test_renders_per_role_and_reuses_no_existing_directory(self):
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        calls = []
        def synthesize(text, target, keys, start_index):
            calls.append((story_audio.gemini_tts.VOICE, text))
            make_segment(target)
        with tempfile.TemporaryDirectory() as temp, patch.object(story_audio.gemini_tts, 'synthesize', side_effect=synthesize):
            folder = Path(temp) / 'job'
            previous = story_audio.gemini_tts.VOICE, story_audio.gemini_tts.STYLE
            result = story_audio.prepare(self.script, folder, ff, ['fake-key'])
            self.assertEqual(result, folder / 'story.json')
            self.assertEqual([call[0] for call in calls], ['Algenib', 'Iapetus'])
            self.assertEqual((story_audio.gemini_tts.VOICE, story_audio.gemini_tts.STYLE), previous)
            audio, turns, ticker = story_video.load_story(folder)
            self.assertTrue(audio.exists()); self.assertEqual(ticker, 'ПРОБА')
            self.assertEqual([s for _,s in turns], ['Бендер', 'Киберсенсей'])
            with self.assertRaises(FileExistsError): story_audio.prepare(self.script, folder, ff, ['fake-key'])
            self.assertEqual(len(calls), 2)

    def test_failure_retains_successful_work_and_no_manifest(self):
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        def synthesize(text, target, keys, start_index):
            if start_index: raise story_audio.gemini_tts.TTSFailure('failed')
            make_segment(target)
        with tempfile.TemporaryDirectory() as temp, patch.object(story_audio.gemini_tts, 'synthesize', side_effect=synthesize) as synth:
            folder = Path(temp) / 'job'
            previous = story_audio.gemini_tts.VOICE, story_audio.gemini_tts.STYLE
            with self.assertRaises(story_audio.gemini_tts.TTSFailure):
                story_audio.prepare(self.script, folder, ff, ['fake'])
            self.assertEqual(synth.call_count, 2)
            self.assertTrue((folder / '1-normalized.wav').exists())
            self.assertFalse((folder / 'story.json').exists())
            self.assertEqual((story_audio.gemini_tts.VOICE, story_audio.gemini_tts.STYLE), previous)


if __name__ == '__main__': unittest.main()

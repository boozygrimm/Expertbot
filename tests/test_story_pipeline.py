import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from news_prototype import story_pipeline
except ImportError:
    story_pipeline = None


@unittest.skipIf(story_pipeline is None, 'Pillow optional')
class StoryPipelineTests(unittest.TestCase):
    def test_success_writes_status_and_never_sends(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'job'
            def prepare(script, path, ffmpeg, keys): path.mkdir()
            def render(path, output, ffmpeg, *, image_regions=None): output.write_bytes(b'fake video')
            with patch.object(story_pipeline.story_audio, 'prepare', side_effect=prepare), \
                 patch.object(story_pipeline.story_video, 'render', side_effect=render), \
                 patch.object(story_pipeline.subprocess, 'run') as decode:
                output=story_pipeline.run({},folder,'ffmpeg',['fake'])
            self.assertTrue(output.is_file()); self.assertEqual(decode.call_count,1)
            self.assertEqual(json.loads((folder/'status.json').read_text())['stage'], 'ready_for_review')

    def test_failure_leaves_no_finished_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'job'
            def prepare(script, path, ffmpeg, keys): path.mkdir()
            with patch.object(story_pipeline.story_audio, 'prepare', side_effect=prepare), \
                 patch.object(story_pipeline.story_video, 'render', side_effect=RuntimeError('bad render')):
                with self.assertRaises(RuntimeError): story_pipeline.run({},folder,'ffmpeg',['fake'])
            self.assertFalse((folder/'video.mp4').exists())
            self.assertEqual(json.loads((folder/'status.json').read_text())['stage'], 'render_failed')


if __name__ == '__main__': unittest.main()

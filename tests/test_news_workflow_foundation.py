import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

try:
    from news_prototype import manual_news, story_video, podcast_audio_trial, podcast_video_trial
    from tests.test_podcast_video_trial import make_segment
except ImportError:
    story_video = None


@unittest.skipIf(story_video is None, 'Pillow is optional for isolated trials')
class NewsWorkflowFoundationTests(unittest.TestCase):
    def test_manual_story_requires_owner_and_private_chat(self):
        args = dict(user_id=11, admin_id=11, chat_type='private', message_id=42,
                    text='Событие', accent='обратить внимание на последствия',
                    url='https://example.com/story', now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        record = manual_news.make_candidate(**args)
        self.assertEqual(record['id'], 'manual:11:42')
        self.assertEqual(record['accent'], args['accent'])
        self.assertEqual(record['verification'], 'unverified')
        for overrides in ({'user_id': 12}, {'chat_type': 'group'}, {'user_id': True}):
            with self.assertRaises(PermissionError):
                manual_news.make_candidate(**(args | overrides))
        with self.assertRaises(ValueError):
            manual_news.make_candidate(**(args | {'url': 'file:///etc/passwd'}))
        self.assertIsNotNone(manual_news.make_candidate(**(args | {'text': ''}))['url'])

    def test_arbitrary_turn_sequence_and_audio_consistency(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            names = ['a.wav', 'b.wav', 'c.wav']
            for name in names:
                make_segment(root / name)
            podcast_audio_trial.assemble_wav([root / name for name in names], root / 'dialogue.wav')
            (root / 'story.json').write_text(json.dumps({'turns': [
                {'speaker': 'Киберсенсей', 'audio': 'a.wav'}, {'speaker': 'Бендер', 'audio': 'b.wav'},
                {'speaker': 'Киберсенсей', 'audio': 'c.wav'}], 'ticker': 'ПРОБА'}), encoding='utf-8')
            audio, turns, text = story_video.load_story(root)
            _, active, _ = podcast_video_trial.dialogue_timeline_from_parts(audio, turns)
            self.assertEqual(active[2], 'Киберсенсей')
            self.assertEqual(active[34], 'Бендер')
            self.assertEqual(active[66], 'Киберсенсей')
            self.assertEqual(text, 'ПРОБА')
            with patch.object(story_video.combined_trial, 'render') as render:
                story_video.render(root, root / 'video.mp4', '/ffmpeg')
                self.assertEqual(render.call_args.kwargs['timeline'][1], active)
            make_segment(root / 'b.wav', seconds=2)
            with self.assertRaises(ValueError):
                story_video.render(root, root / 'video.mp4', '/ffmpeg')

    def test_rejects_unsafe_paths_duplicates_and_output_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); make_segment(root / 'a.wav')
            podcast_audio_trial.assemble_wav([root / 'a.wav'], root / 'dialogue.wav')
            def manifest(turns):
                (root / 'story.json').write_text(json.dumps({'turns': turns}), encoding='utf-8')
            turn = {'speaker': 'Бендер', 'audio': 'a.wav'}
            manifest([turn, turn])
            with self.assertRaises(ValueError): story_video.load_story(root)
            manifest([{'speaker': 'Бендер', 'audio': '../a.wav'}])
            with self.assertRaises(ValueError): story_video.load_story(root)
            manifest([turn])
            with self.assertRaises(ValueError): story_video.render(root, root / 'dialogue.wav', '/ffmpeg')
            (root / 'link.wav').symlink_to(root / 'a.wav')
            manifest([{'speaker': 'Бендер', 'audio': 'link.wav'}])
            with self.assertRaises(ValueError): story_video.load_story(root)


if __name__ == '__main__': unittest.main()

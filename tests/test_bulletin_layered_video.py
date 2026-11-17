import array
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from news_prototype import (bender_trial, bulletin_layered_video as video,
                            bulletin_timing, bulletin_wide_screen, combined_trial,
                            podcast_audio_trial, podcast_video_trial)
from tests.test_bulletin_pipeline import payload
from tests.test_podcast_video_trial import make_segment


class BulletinLayeredVideoTests(unittest.TestCase):
    def test_window_boundaries_gaps_and_neutral_fallback(self):
        windows = [bulletin_timing.SectionWindow(1, 1.75, 2.75),
                   bulletin_timing.SectionWindow(2, 3.10, 4.10)]
        for time, expected in [(0, None), (1.749, None), (1.75, 0), (2.9, 0),
                               (3.099, 0), (3.10, 1), (4.099, 1), (4.10, None)]:
            self.assertEqual(video.section_index(windows, time), expected)
        windows = [bulletin_timing.SectionWindow(i + 1, i * 2 + 1., i * 2 + 2.) for i in range(20)]
        for i in range(20): self.assertEqual(video.section_index(windows, i * 2 + 1.5), i)

    def test_audio_is_frame_aligned_and_all_speech_samples_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            part = root / 'turn.wav'
            make_segment(part)
            audio = root / 'audio.wav'
            podcast_audio_trial.assemble_wav([part], audio)
            rate, original = podcast_video_trial.read_wav(audio)
            output = root / 'padded.wav'
            video.write_sound(audio, output, bender_trial.FPS)
            _, padded = podcast_video_trial.read_wav(output)
            start = combined_trial.INTRO_FRAMES * (rate // bender_trial.FPS)
            self.assertFalse(any(padded[:start]))
            self.assertEqual(padded[start:start + len(original)], original)
            self.assertFalse(any(padded[start + len(original):]))
            self.assertEqual(len(padded), (combined_trial.INTRO_FRAMES + bender_trial.FPS +
                                           combined_trial.TAIL_FRAMES) * rate // bender_trial.FPS)
            with self.assertRaises(ValueError): video.write_sound(audio, output, 1)

    def test_render_rejects_mismatched_sections_before_creating_output(self):
        data = payload()['sections']
        windows = [bulletin_timing.SectionWindow(11, 1, 2), bulletin_timing.SectionWindow(10, 2, 3)]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'out.mp4'
            with self.assertRaises(ValueError):
                video.render(output, 'invalid-ffmpeg', sections=data, windows=windows,
                             timeline=(Path('no.wav'), ['Бендер'], [False]))
            self.assertFalse(output.exists())

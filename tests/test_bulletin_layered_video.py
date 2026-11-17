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

    def test_click_only_in_verified_gap_without_changing_speech(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / 'first.wav', root / 'second.wav'
            make_segment(first); make_segment(second)
            audio = root / 'joined.wav'
            podcast_audio_trial.assemble_wav([first, second], audio)
            rate, original = podcast_video_trial.read_wav(audio)
            windows, _ = bulletin_timing.section_windows([(1, first), (2, second)])
            positions = video.click_positions(windows, rate)
            self.assertEqual(len(positions), 1)
            position = positions[0]
            effect = video.remote_click(rate)
            self.assertGreater(max(map(abs, effect)), 0)
            self.assertLessEqual(max(map(abs, effect)), 3500)
            self.assertTrue(rate <= position < position + len(effect) <= round(rate * 1.35))
            result = root / 'padded.wav'
            frames = (len(original) * bender_trial.FPS + rate - 1) // rate
            video.write_sound(audio, result, frames, clicks=positions)
            _, padded = podcast_video_trial.read_wav(result)
            intro = combined_trial.INTRO_FRAMES * (rate // bender_trial.FPS)
            dialogue = padded[intro:intro + len(original)]
            self.assertEqual(dialogue[:position], original[:position])
            self.assertEqual(dialogue[position:position + len(effect)], effect)
            self.assertEqual(dialogue[position + len(effect):], original[position + len(effect):])
            self.assertEqual(podcast_video_trial.read_wav(audio)[1], original)
            with self.assertRaises(ValueError):
                video.write_sound(audio, result, frames, clicks=[rate // 2])

    def test_clicks_only_between_stories_with_room_for_effect(self):
        intro = combined_trial.INTRO_FRAMES / bender_trial.FPS
        windows = [bulletin_timing.SectionWindow(i + 1, intro + i * 1.35, intro + i * 1.35 + 1)
                   for i in range(20)]
        self.assertEqual(len(video.click_positions(windows, 24000)), 19)
        short_gap = [bulletin_timing.SectionWindow(1, intro, intro + 1),
                     bulletin_timing.SectionWindow(2, intro + 1.02, intro + 2)]
        self.assertEqual(video.click_positions(short_gap, 24000), [])
        self.assertEqual(video.click_positions(windows[:1], 24000), [])

    def test_render_rejects_mismatched_sections_before_creating_output(self):
        data = payload()['sections']
        windows = [bulletin_timing.SectionWindow(11, 1, 2), bulletin_timing.SectionWindow(10, 2, 3)]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'out.mp4'
            with self.assertRaises(ValueError):
                video.render(output, 'invalid-ffmpeg', sections=data, windows=windows,
                             timeline=(Path('no.wav'), ['Бендер'], [False]))
            self.assertFalse(output.exists())

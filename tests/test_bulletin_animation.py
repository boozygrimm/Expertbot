from io import BytesIO
from pathlib import Path
import subprocess
import tempfile
import unittest
from PIL import Image, ImageChops
from news_prototype import bulletin_animation, bulletin_motion_preview, bulletin_wide_screen
from tests.test_bulletin_wide_screen import thumbnail


class BulletinAnimationTests(unittest.TestCase):
    def test_faces_change_only_in_expected_regions_and_preserve_alpha(self):
        frames = bulletin_animation.frames()
        self.assertEqual(len(frames), 8)
        quiet_bender, quiet_sensei = frames[False, False, False]
        speaking_bender, same_sensei = frames[True, False, False]
        blinking_bender, _ = frames[False, True, False]
        same_bender, speaking_sensei = frames[False, False, True]
        for left, right in ((quiet_bender, speaking_bender), (quiet_bender, blinking_bender),
                            (quiet_sensei, speaking_sensei)):
            self.assertIsNone(ImageChops.difference(left.getchannel('A'), right.getchannel('A')).getbbox())
        self.assertIsNone(ImageChops.difference(quiet_sensei.convert('RGB'), same_sensei.convert('RGB')).getbbox())
        self.assertIsNone(ImageChops.difference(quiet_bender.convert('RGB'), same_bender.convert('RGB')).getbbox())
        for first, second, box in ((quiet_bender, speaking_bender, bulletin_animation.BENDER_MOUTH),
                                   (quiet_bender, blinking_bender, bulletin_animation.BENDER_EYES),
                                   (quiet_sensei, speaking_sensei, bulletin_animation.SENSEI_MOUTH)):
            diff = ImageChops.difference(first.convert('RGB'), second.convert('RGB'))
            x0, y0, x1, y1 = diff.getbbox()
            self.assertGreaterEqual(x0, box[0] - 4); self.assertGreaterEqual(y0, box[1] - 4)
            self.assertLessEqual(x1, box[2] + 5); self.assertLessEqual(y1, box[3] + 5)

    def test_mouth_states_use_the_old_soft_teeth_references(self):
        frames = bulletin_animation.frames()
        closed = bulletin_animation._old_mouth_reference('bender_soft_closed.png')
        opened = bulletin_animation._old_mouth_reference('studio_mockup_soft.png')
        # The center of the feathered mask is fully opaque in both states.
        for state, reference in ((False, closed), (True, opened)):
            layer = frames[state, False, False][0]
            for point in ((273, 266), (274, 271), (275, 277)):
                self.assertEqual(layer.convert('RGB').getpixel(point), reference.getpixel(point))
        self.assertNotEqual(closed.getpixel((274, 271)), opened.getpixel((274, 271)))

    def test_animation_mouth_and_screen_stay_independent(self):
        frames = bulletin_animation.frames()
        quiet = bulletin_wide_screen.compose(thumbnail('red'), speaker_layers=frames[False, False, False])
        speaking = bulletin_wide_screen.compose(thumbnail('red'), speaker_layers=frames[True, False, False])
        changed_screen = bulletin_wide_screen.compose(thumbnail('blue'), speaker_layers=frames[True, False, False])
        self.assertEqual(quiet.getpixel((450, 150)), speaking.getpixel((450, 150)))
        self.assertNotEqual(speaking.getpixel((450, 150)), changed_screen.getpixel((450, 150)))
        self.assertNotEqual(quiet.crop((246,254,303,287)).tobytes(), speaking.crop((246,254,303,287)).tobytes())

    def test_blink_repeats_for_entire_long_bulletin(self):
        for start in (1.8, 5.3, 61.3, 299.3):
            self.assertTrue(bulletin_motion_preview.is_blink(start + .01))
        self.assertFalse(bulletin_motion_preview.is_blink(.5))

    def test_silent_video_is_generated_from_synthetic_timing(self):
        import imageio_ffmpeg
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'motion.mp4'
            bulletin_motion_preview.render_preview(output, imageio_ffmpeg.get_ffmpeg_exe())
            self.assertGreater(output.stat().st_size, 10000)
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error', '-xerror', '-i', str(output),
                            '-f', 'null', '-'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            def frame_at(seconds):
                data = subprocess.check_output([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error',
                    '-ss', str(seconds), '-i', str(output), '-frames:v', '1', '-f', 'image2pipe',
                    '-vcodec', 'png', 'pipe:1'])
                return Image.open(BytesIO(data)).convert('RGB')
            opening, blink, sensei = (frame_at(time) for time in (0.15, 1.86, 3.2))
            self.assertNotEqual(opening.crop((248, 209, 325, 249)).tobytes(),
                                blink.crop((248, 209, 325, 249)).tobytes())
            self.assertGreater(abs(opening.getpixel((450, 150))[0] - sensei.getpixel((450, 150))[0]), 20)
            self.assertGreater(sum(abs(a-b) for a, b in zip(opening.getpixel((610, 260)),
                                                           sensei.getpixel((610, 260)))), 20)
            with self.assertRaises(ValueError):
                bulletin_motion_preview.render_preview(output, imageio_ffmpeg.get_ffmpeg_exe())


if __name__ == '__main__': unittest.main()

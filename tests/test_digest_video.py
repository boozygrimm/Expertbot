from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from news_prototype import digest_video


class DigestVideoTests(unittest.TestCase):
    def test_rejects_missing_and_existing_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'a.mp4'; source.write_bytes(b'not an mp4')
            out = root / 'out.mp4'; out.write_bytes(b'keep')
            with self.assertRaises(ValueError): digest_video.assemble([source], out, '/ffmpeg')
            self.assertEqual(out.read_bytes(), b'keep')
            with self.assertRaises(ValueError): digest_video.assemble([root / 'missing.mp4'], root / 'new.mp4', '/ffmpeg')
            link = root / 'link.mp4'; link.symlink_to(source)
            with self.assertRaises(ValueError): digest_video.assemble([link], root / 'new.mp4', '/ffmpeg')

    def test_encoder_failure_leaves_no_result_or_partial(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'a.mp4'; source.write_bytes(b'broken')
            with patch.object(digest_video.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'ffmpeg')):
                with self.assertRaises(subprocess.CalledProcessError):
                    digest_video.assemble([source], root / 'result.mp4', '/ffmpeg')
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['a.mp4'])

    def test_real_ffmpeg_two_clips(self):
        try:
            import imageio_ffmpeg
        except ImportError:
            self.skipTest('imageio-ffmpeg is optional')
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = [root / 'first.mp4', root / 'second.mp4']
            for i, path in enumerate(inputs):
                subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', f'color=c={"red" if i == 0 else "blue"}:s=320x180:r=24:d=1',
                                '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=24000:duration=1',
                                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(path)], check=True)
            out = root / 'digest.mp4'
            digest_video.assemble(inputs, out, ffmpeg)
            self.assertGreater(out.stat().st_size, 1000)
            self.assertTrue(all(p.is_file() for p in inputs))
            subprocess.run([ffmpeg, '-v', 'error', '-xerror', '-i', str(out), '-f', 'null', '-'], check=True)


if __name__ == '__main__': unittest.main()

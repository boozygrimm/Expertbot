import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch
from news_prototype import digest_job


class DigestJobTests(unittest.TestCase):
    def test_uses_existing_videos_in_selected_order_and_commits_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); ids = [str(uuid.uuid4()) for _ in range(2)]
            for item in ids:
                work = root/item/'work'; work.mkdir(parents=True)
                (work/'status.json').write_text('{"stage":"ready_for_review"}')
                (work/'video.mp4').write_bytes(b'original')
            job = root/str(uuid.uuid4()); job.mkdir()
            request = job/'request.json'; request.write_text(json.dumps({'inputs': ids[::-1]}))
            def assemble(inputs, output, ffmpeg):
                self.assertEqual(inputs, [root/item/'work'/'video.mp4' for item in ids[::-1]])
                output.write_bytes(b'digest')
            with patch.object(digest_job.digest_video, 'assemble', side_effect=assemble) as mock:
                digest_job.run(request, root, job/'work', 'ffmpeg')
                mock.assert_called_once()
            self.assertEqual(json.loads((job/'work'/'status.json').read_text())['stage'], 'ready_for_review')
            self.assertTrue(all((root/item/'work'/'video.mp4').read_bytes() == b'original' for item in ids))

    def test_real_ffmpeg_worker_assembly(self):
        try:
            import imageio_ffmpeg
        except ImportError:
            self.skipTest('imageio-ffmpeg is optional')
        import subprocess
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); ids=[str(uuid.uuid4()) for _ in range(2)]
            for i, item in enumerate(ids):
                work=root/item/'work'; work.mkdir(parents=True)
                (work/'status.json').write_text('{"stage":"ready_for_review"}')
                subprocess.run([ffmpeg, '-v','error','-f','lavfi','-i',f'color=c={"red" if i == 0 else "blue"}:s=320x180:r=24:d=0.5',
                                '-f','lavfi','-i','sine=frequency=440:sample_rate=24000:duration=0.5',
                                '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(work/'video.mp4')],check=True)
            job=root/str(uuid.uuid4()); job.mkdir(); request=job/'request.json'
            request.write_text(json.dumps({'inputs':ids}))
            digest_job.run(request,root,job/'work',ffmpeg)
            self.assertGreater((job/'work'/'video.mp4').stat().st_size,1000)
            self.assertEqual(json.loads((job/'work'/'status.json').read_text())['stage'],'ready_for_review')

    def test_missing_or_not_ready_fails_before_creating_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); ids=[str(uuid.uuid4()) for _ in range(2)]
            job=root/str(uuid.uuid4()); job.mkdir(); request=job/'request.json'
            request.write_text(json.dumps({'inputs': ids}))
            with self.assertRaises((OSError, ValueError)):
                digest_job.run(request,root,job/'work')
            self.assertFalse((job/'work').exists())


if __name__ == '__main__': unittest.main()

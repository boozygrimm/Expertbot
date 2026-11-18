import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from mongomock_motor import AsyncMongoMockClient
from news_prototype import bulletin_video_candidate as candidates, owner_bulletin, owner_bulletin_sources
from news_prototype import telegram_public_source as source
from tests.test_bulletin_foundation import response
from tests.test_owner_drafts import SOURCE


class VideoCandidateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = AsyncMongoMockClient().test
        await owner_bulletin_sources.create(self.db, 123, 99, [
            {'url': 'https://t.me/s/ExampleNews/102?single=1', 'title': 'Пост'},
            {'url': 'https://example.com/news', 'title': 'Другая новость'}])
        async def fetch(url):
            return {'summary': SOURCE, 'telegram_video_status': 'available'}
        await owner_bulletin_sources.collect(self.db, 123, 99, fetch, AsyncMock())
        await owner_bulletin.prepare(self.db, 123, 99, AsyncMock(side_effect=response))
        self.item = await owner_bulletin.get(self.db, 123, 99)
        self.rev = self.item['revision']
        self.post = source.Post('https://t.me/ExampleNews/102', 'ExampleNews/102', SOURCE.strip(),
                                'available', 'https://cdn4.telesco.pe/file/secret?token=secret')
        self.bytes = b'a simulated checked video'

    def download(self, post, folder, **kwargs):
        self.assertEqual(post, self.post)
        folder.mkdir()
        path = folder / 'source.mp4'
        path.write_bytes(self.bytes)
        return source.Video(path, hashlib.sha256(self.bytes).hexdigest(), len(self.bytes), 53., 848, 476, True)

    async def acquire(self):
        with patch.object(source, 'fetch_post', return_value=self.post), \
             patch.object(source, 'download_video', side_effect=self.download):
            return await candidates.acquire(self.db, 123, 99, self.rev, 1, self.root / 'candidate')

    async def test_binds_exact_post_version_and_video_without_changing_payload(self):
        result = await self.acquire()
        self.assertEqual(result.post_url, 'https://t.me/ExampleNews/102')
        self.assertEqual(result.section_id, 1)
        self.assertEqual(result.video_sha256, hashlib.sha256(self.bytes).hexdigest())
        self.assertNotIn('secret', repr(result))
        await candidates.verify(self.db, result)
        item = await owner_bulletin.get(self.db, 123, 99)
        self.assertEqual(item, self.item)
        payload = await owner_bulletin.approved_payload(self.db, 123, 99, self.rev)
        self.assertNotIn('video', repr(payload))
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

    async def test_end_to_end_synthetic_post_and_checked_mp4(self):
        # No real network: exercise both exact-widget parsing and the complete
        # decoder with generated local media. ffprobe metadata is simulated.
        import imageio_ffmpeg
        from unittest.mock import Mock
        from tests.test_telegram_public_source import Response
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        synthetic = self.root / 'synthetic.mp4'
        subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24',
                        '-t', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(synthetic)], check=True)
        video_bytes = synthetic.read_bytes()
        html = (f'<div class="tgme_widget_message" data-post="ExampleNews/102">'
                f'<div class="tgme_widget_message_text js-message_text">{SOURCE.strip()}</div>'
                '<a class="tgme_widget_message_video_player" href="https://t.me/ExampleNews/102">'
                '<video src="https://cdn4.telesco.pe/file/video?token=private"></video></a></div>').encode()
        info = {'format': {'duration': '1', 'format_name': 'mov,mp4,m4a,3gp,3g2,mj2'},
                'streams': [{'codec_type': 'video', 'width': 320, 'height': 180}]}
        fake_probe = self.root / 'ffprobe'
        fake_probe.write_text('#!/usr/bin/env python3\nprint(' + repr(json.dumps(info)) + ')\n')
        fake_probe.chmod(0o700)
        def connection(host, target):
            data = html if host == 't.me' else video_bytes
            return Mock(), Response(data, str(len(data)))
        with patch.object(source.probe, 'get_response', side_effect=connection) as get:
            result = await candidates.acquire(self.db, 123, 99, self.rev, 1, self.root / 'candidate',
                                              ffmpeg=ffmpeg, ffprobe=str(fake_probe))
        self.assertEqual(get.call_count, 2)
        self.assertEqual(result.path.read_bytes(), video_bytes)
        self.assertEqual((result.width, result.height, result.has_audio), (320, 180, False))
        self.assertNotIn('private', repr(result))
        await candidates.verify(self.db, result)

    async def test_wrong_owner_version_section_or_ambiguous_status_never_fetches(self):
        with patch.object(source, 'fetch_post') as fetch:
            for args in ((124, 99, self.rev, 1), (123, 99, 'a' * 64, 1),
                         (123, 99, self.rev, 2), (123, 99, self.rev, True)):
                with self.subTest(args=args), self.assertRaises(ValueError):
                    await candidates.acquire(self.db, *args, self.root / 'candidate')
            fetch.assert_not_called()

    async def test_changed_caption_or_post_never_downloads(self):
        for post in (source.Post(self.post.url, self.post.post_id, SOURCE.strip() + ' изменено', 'available', self.post.video_url),
                     source.Post('https://t.me/ExampleNews/103', 'ExampleNews/103', SOURCE.strip(), 'available', self.post.video_url),
                     source.Post(self.post.url, self.post.post_id, SOURCE.strip(), 'ambiguous', None)):
            with self.subTest(post=post), patch.object(source, 'fetch_post', return_value=post), \
                 patch.object(source, 'download_video') as download, self.assertRaises(ValueError):
                await candidates.acquire(self.db, 123, 99, self.rev, 1, self.root / 'candidate')
            download.assert_not_called()

    async def test_file_substitution_and_symlink_are_rejected(self):
        result = await self.acquire()
        result.path.write_bytes(b'b simulated checked video')  # same size
        with self.assertRaisesRegex(ValueError, 'изменился'):
            await candidates.verify(self.db, result)
        target = self.root / 'target.mp4'; target.write_bytes(self.bytes)
        result.path.unlink(); result.path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'изменился'):
            await candidates.verify(self.db, result)

    async def test_edit_after_download_invalidates_candidate(self):
        result = await self.acquire()
        section = self.item['sections'][0]
        edited = owner_bulletin.editable_section(section).replace(section['ticker'], 'Другой заголовок')
        self.assertEqual(await owner_bulletin.change_section(self.db, 123, 99, self.rev, 1, text=edited), 'saved')
        with self.assertRaisesRegex(ValueError, 'изменилась'):
            await candidates.verify(self.db, result)

    async def test_edit_during_download_rejects_candidate_without_queueing(self):
        original = candidates._current_story
        calls = 0
        async def changing(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                section = self.item['sections'][0]
                edited = owner_bulletin.editable_section(section).replace(section['ticker'], 'Другой заголовок')
                await owner_bulletin.change_section(self.db, 123, 99, self.rev, 1, text=edited)
            return await original(*args)
        with patch.object(source, 'fetch_post', return_value=self.post), \
             patch.object(source, 'download_video', side_effect=self.download), \
             patch.object(candidates, '_current_story', side_effect=changing):
            with self.assertRaisesRegex(ValueError, 'изменилась'):
                await candidates.acquire(self.db, 123, 99, self.rev, 1, self.root / 'candidate')
        self.assertTrue((self.root / 'candidate/source.mp4').exists())  # quarantined, not authorized
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)

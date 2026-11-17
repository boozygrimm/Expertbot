import asyncio
from io import BytesIO
import json
import os
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from PIL import Image, ImageDraw
from mongomock_motor import AsyncMongoMockClient
from news_prototype import image_vision, image_focus, news_image_codec, news_picture, owner_drafts, owner_program, story_audio, story_editor
from tests.test_editor_client import Response, Session
from tests.test_owner_drafts import DRAFT, SOURCE, URL

REGIONS = [[0, 0, 1000, 450], [0, 550, 1000, 1000]]
DESCRIPTION = 'На изображении написано, что публикация описывает диету, и виден человек.'


def thumbnail():
    image = Image.new('RGB', (100, 200), 'red')
    ImageDraw.Draw(image).rectangle((0, 100, 99, 199), fill='blue')
    output = BytesIO(); image.save(output, format='PNG')
    return news_image_codec.make_thumbnail(output.getvalue())


def response(data):
    body = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(data, ensure_ascii=False)}]}}]}
    return Response(200, json.dumps(body, ensure_ascii=False).encode())


class VisionClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_request_contains_image_description_and_regions(self):
        calls = []
        session = Session(response({'description': DESCRIPTION, 'regions': REGIONS}), calls)
        with patch.object(image_vision.aiohttp, 'ClientSession', return_value=session) as factory:
            result = await image_vision.analyze(thumbnail(), 'secret', 'gemini-test')
        self.assertEqual(result, {'description': DESCRIPTION, 'regions': REGIONS})
        self.assertEqual(len(calls), 1)
        self.assertEqual(factory.call_args.kwargs['timeout'].total, 15)
        url, args = calls[0]
        self.assertNotIn('secret', url)
        self.assertFalse(args['allow_redirects'])
        self.assertEqual(args['json']['contents'][0]['parts'][1]['inlineData']['mimeType'], 'image/jpeg')

    async def test_invalid_regions_preserve_description_and_fall_back(self):
        calls = []
        session = Session(response({'description': DESCRIPTION, 'regions': [[0, 0, 1001, 20]]}), calls)
        with patch.object(image_vision.aiohttp, 'ClientSession', return_value=session):
            result = await image_vision.analyze(thumbnail(), 'secret', 'gemini-test')
        self.assertEqual(result, {'description': DESCRIPTION, 'regions': []})
        for bad in ([[0,0,1001,500]], [[False,0,500,500]], [[1,1,2,2]], [[900,0,100,500]], [1,2,3], []):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                image_focus.validate_regions(bad)

    async def test_failure_does_not_retry_or_expose_response(self):
        calls = []
        session = Session(Response(429, b'secret'), calls)
        with patch.object(image_vision.aiohttp, 'ClientSession', return_value=session):
            with self.assertRaises(ValueError) as error:
                await image_vision.analyze(thumbnail(), 'secret', 'gemini-test')
        self.assertEqual(len(calls), 1)
        self.assertNotIn('secret', str(error.exception))


class VisionFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient().test
        self.image = thumbnail()
        self.fetch = AsyncMock(return_value={'summary': SOURCE, 'media': {'kind': 'photo', 'url': 'https://cdn4.telesco.pe/file/x'}})
        self.fetch_image = AsyncMock(return_value=self.image)
        self.draft = AsyncMock(return_value=DRAFT)

    async def prepare(self, ident, vision):
        await owner_drafts.create(self.db, 123, ident, URL, '')
        outcome = await owner_drafts.process_new(self.db, 123, ident, self.fetch, self.draft, self.fetch_image, vision)
        self.assertEqual(outcome, 'draft_ready')
        return await owner_drafts.get(self.db, 123, ident)

    async def test_description_and_regions_flow_into_review_edits_and_program(self):
        vision = AsyncMock(return_value={'description': DESCRIPTION, 'regions': REGIONS})
        item = await self.prepare(10, vision)
        self.assertEqual(item['image_analysis'], 'analyzed')
        self.assertEqual(self.draft.await_args.args[0]['image_description'], DESCRIPTION)
        self.assertEqual(item['script']['image_regions'], REGIONS)
        vision.assert_awaited_once_with(self.image)
        checked = story_audio.validate_script(item['script'])
        self.assertEqual(checked['image_regions'], REGIONS)
        old = item['revision']
        parsed = owner_drafts.parse_edit(owner_drafts.editable_message(10, URL, item['script']).replace('Проверь свою', 'Покажи свою'))
        self.assertEqual(await owner_drafts.edit(self.db, 123, *parsed), 'saved')
        updated = await owner_drafts.get(self.db, 123, 10)
        self.assertEqual(updated['script']['image_regions'], REGIONS)
        await self.prepare(11, AsyncMock(return_value={'description': DESCRIPTION, 'regions': REGIONS}))
        await owner_program.create(self.db, 123, 50, [10, 11])
        program = await owner_program.get(self.db, 123, 50)
        self.assertEqual(program['stories'][0]['script']['image_regions'], REGIONS)
        self.assertEqual(await owner_drafts.remove_focus(self.db, 123, 10, old), 'stale')
        self.assertEqual(await owner_drafts.remove_focus(self.db, 123, 10, updated['revision']), 'saved')
        cleared = await owner_drafts.get(self.db, 123, 10)
        self.assertNotIn('image_regions', cleared['script'])
        self.assertEqual(cleared['script']['image_b64'], self.image)
        self.assertNotEqual(updated['revision'], cleared['revision'])
        self.assertEqual(await owner_program.approve(self.db, 123, 50, program['revision']), 'stale_draft')

    async def test_timeout_falls_back_without_second_vision_request(self):
        async def slow(_):
            await asyncio.sleep(1)
        vision = AsyncMock(side_effect=slow)
        with patch.object(image_vision, 'TIMEOUT_SECONDS', .01):
            item = await self.prepare(10, vision)
        self.assertEqual(item['image_analysis'], 'unavailable')
        self.assertNotIn('image_description', self.draft.await_args.args[0])
        self.assertNotIn('image_regions', item['script'])
        self.assertIn('image_b64', item['script'])
        vision.assert_awaited_once()

    async def test_invalid_regions_fall_back_without_losing_description(self):
        item = await self.prepare(10, AsyncMock(return_value={'description': DESCRIPTION, 'regions': [[0, 0, 2000, 500]]}))
        self.assertEqual(item['image_analysis'], 'analyzed')
        self.assertEqual(item['image_description'], DESCRIPTION)
        self.assertNotIn('image_regions', item['script'])

    async def test_old_worker_cannot_silently_drop_focus_in_program_or_story(self):
        from news_prototype import news_queue
        from types import SimpleNamespace
        item = await self.prepare(10, AsyncMock(return_value={'description': DESCRIPTION, 'regions': REGIONS}))
        queue = SimpleNamespace(head=AsyncMock(return_value=None))
        client = SimpleNamespace(supports_images=AsyncMock(return_value=True),
                                 supports_programs=AsyncMock(return_value=True),
                                 supports_image_focus=AsyncMock(return_value=False),
                                 submit=AsyncMock(return_value={'status':'running'}),
                                 submit_program=AsyncMock(return_value={'status':'running'}))
        for kind in ('story', 'program'):
            jobs = self.db['jobs_' + kind]
            if kind == 'story':
                await news_queue.enqueue_script(jobs, 'story', item['script'])
            else:
                await news_queue.enqueue_program(jobs, 'program', [item['script'], item['script']])
            await news_queue.run_idle_once(queue, jobs, client)
            self.assertEqual((await jobs.find_one({}))['stage'], 'queued')
            client.submit.assert_not_awaited(); client.submit_program.assert_not_awaited()
            client.supports_image_focus.return_value = True
            await news_queue.run_idle_once(queue, jobs, client)
            self.assertEqual((await jobs.find_one({}))['stage'], 'running')
            client.submit.reset_mock(); client.submit_program.reset_mock()
            client.supports_image_focus.return_value = False

    async def test_draft_handler_checks_focus_before_approval(self):
        from handlers import news_owner_drafts as handler
        item = await self.prepare(10, AsyncMock(return_value={'description': DESCRIPTION, 'regions': REGIONS}))
        message = SimpleNamespace(from_user=SimpleNamespace(id=123), chat=SimpleNamespace(type='private'),
                                  text=f'/news_approve 10 {item["revision"]}', answer=AsyncMock())
        client = SimpleNamespace(supports_images=AsyncMock(return_value=True), supports_image_focus=AsyncMock(return_value=False))
        with patch.object(handler, 'ADMIN_ID', 123), patch.object(handler.owner_trial, 'drafts_enabled', AsyncMock(return_value=True)), \
             patch.object(handler, 'db', SimpleNamespace(db=self.db)), \
             patch('news_prototype.news_worker_client.NewsWorkerClient', return_value=client):
            await handler.approve_draft(message)
        self.assertIn('Обновите воркер', message.answer.await_args.args[0])
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
        self.assertEqual((await owner_drafts.get(self.db, 123, 10))['stage'], 'draft_ready')

    async def test_program_handler_checks_focus_before_approval(self):
        from handlers import news_owner_drafts as handler
        from news_prototype import owner_program
        await self.prepare(10, AsyncMock(return_value={'description': DESCRIPTION, 'regions': REGIONS}))
        await self.prepare(11, AsyncMock(return_value={'description': DESCRIPTION, 'regions': REGIONS}))
        await owner_program.create(self.db, 123, 50, [10, 11])
        program = await owner_program.get(self.db, 123, 50)
        message = SimpleNamespace(from_user=SimpleNamespace(id=123), chat=SimpleNamespace(type='private'),
                                  text=f'/news_program_approve 50 {program["revision"]}', answer=AsyncMock())
        client = SimpleNamespace(supports_programs=AsyncMock(return_value=True), supports_image_focus=AsyncMock(return_value=False))
        with patch.object(handler, 'ADMIN_ID', 123), patch.object(handler.owner_trial, 'drafts_enabled', AsyncMock(return_value=True)), \
             patch.object(handler, 'db', SimpleNamespace(db=self.db)), \
             patch('news_prototype.news_worker_client.NewsWorkerClient', return_value=client):
            await handler.approve_program(message)
        self.assertIn('Обновите воркер', message.answer.await_args.args[0])
        self.assertEqual(await self.db.news_owner_trial_jobs.count_documents({}), 0)
        self.assertEqual((await owner_program.get(self.db, 123, 50))['stage'], 'ready_for_review')

    async def test_prepare_many_starts_analyses_concurrently(self):
        from handlers import news_owner_drafts as handler
        started = []
        ready = asyncio.Event()
        async def process(*args):
            started.append(args[2])
            if len(started) == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), .5)
            return 'draft_ready'
        message = SimpleNamespace(from_user=SimpleNamespace(id=123), chat=SimpleNamespace(type='private'),
                                  text='/news_prepare_many\nhttps://t.me/A/1\nhttps://t.me/B/2',
                                  message_id=100, answer=AsyncMock())
        with patch.object(handler, 'ADMIN_ID', 123), patch.object(handler.owner_trial, 'drafts_enabled', AsyncMock(return_value=True)), \
             patch.object(handler, 'db', SimpleNamespace(db=self.db)), \
             patch.object(handler.owner_drafts, 'process_new', side_effect=process), \
             patch.object(handler, 'show', new_callable=AsyncMock) as show:
            await handler.prepare_many(message)
        self.assertEqual(len(started), 2)
        self.assertEqual(show.await_count, 2)

    async def test_remove_image_removes_its_focus_regions(self):
        item = await self.prepare(10, AsyncMock(return_value={'description': DESCRIPTION, 'regions': REGIONS}))
        self.assertEqual(await owner_drafts.remove_image(self.db, 123, 10, item['revision']), 'saved')
        script = (await owner_drafts.get(self.db, 123, 10))['script']
        self.assertNotIn('image_b64', script)
        self.assertNotIn('image_regions', script)
        story_audio.validate_script(script)


class FocusRenderingTests(unittest.TestCase):
    def test_regions_affect_preview_and_actual_views(self):
        data = thumbnail()
        views = news_picture.prepare_views(data, REGIONS)
        self.assertEqual(len(views), 3)
        self.assertGreater(views[1].getpixel((342, 194))[0], 180)
        self.assertGreater(views[2].getpixel((342, 194))[2], 180)
        annotated = news_image_codec.review_preview(data, REGIONS)
        self.assertNotEqual(annotated, news_image_codec.decode_thumbnail(data))
        with Image.open(BytesIO(annotated)) as photo:
            self.assertEqual(photo.size, (100, 200))

    def test_editor_accepts_evidence_from_labeled_vision_description(self):
        candidate = {'summary': SOURCE, 'image_description': DESCRIPTION}
        turns = [{'speaker': 'Бендер', 'text': 'На изображении упоминается диета.',
                  'kind': 'fact', 'evidence': 'публикация описывает диету'},
                 {'speaker': 'Киберсенсей', 'text': 'Проверим.', 'kind': 'comment', 'evidence': ''}]
        system, material = story_editor.prompt_for(candidate)
        self.assertIn('может ошибаться', system)
        self.assertEqual(json.loads(material)['image_description'], DESCRIPTION)
        result = story_editor.parse_draft(json.dumps({'decision':'draft', 'turns': turns, 'ticker':'ДИЕТА'}, ensure_ascii=False), candidate)
        self.assertEqual(result['verification'], 'needs_review')
        with self.assertRaises(ValueError):
            story_editor.parse_draft(json.dumps({'decision':'draft', 'turns': turns, 'ticker':'ДИЕТА'}, ensure_ascii=False), {'summary': SOURCE})


if __name__ == '__main__': unittest.main()

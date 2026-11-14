import asyncio
import base64
import io
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from PIL import Image

os.environ.setdefault('MONGO_URI', 'mongodb://localhost:27017')
os.environ.setdefault('TELEGRAM_TOKEN', '123456:TESTTOKEN')

from handlers import image_gen, image_providers as providers


def image_bytes():
    output = io.BytesIO()
    Image.new('RGB', (16, 16), 'red').save(output, format='PNG')
    return output.getvalue()


class ProviderHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.image = image_bytes()
        self.calls = []
        self.primary_status = 200
        self.primary_result = {'success': True, 'result': {'image': base64.b64encode(self.image).decode()}}
        self.backup_status = 200
        self.backup_image = self.image
        self.app = web.Application()
        self.app.router.add_post('/cloud/{account}', self.cloud)
        self.app.router.add_post('/ovh', self.ovh)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.env = patch.dict(os.environ, {'CLOUDFLARE_ACCOUNT_ID': 'a' * 32, 'CLOUDFLARE_AI_TOKEN': 'secret'})
        self.env.start()
        self.url1 = patch.object(providers, 'CLOUDFLARE_URL', f'http://127.0.0.1:{port}/cloud/{{account}}')
        self.url2 = patch.object(providers, 'OVH_URL', f'http://127.0.0.1:{port}/ovh')
        self.lock = patch.object(providers, '_ovh_lock', asyncio.Lock())
        self.timestamp = patch.object(providers, '_last_ovh_request', None)
        for p in (self.url1, self.url2, self.lock, self.timestamp):
            p.start()
        self.addAsyncCleanup(self.runner.cleanup)
        self.addCleanup(self.env.stop)
        for p in (self.url1, self.url2, self.lock, self.timestamp):
            self.addCleanup(p.stop)

    async def cloud(self, request):
        self.calls.append(('cloud', request.headers.get('Authorization'), await request.post()))
        return web.json_response(self.primary_result, status=self.primary_status)

    async def ovh(self, request):
        self.calls.append(('ovh', request.headers.get('Authorization'), await request.json()))
        return web.Response(body=self.backup_image, status=self.backup_status)

    async def test_cloudflare_multipart_success_does_not_call_backup(self):
        self.assertEqual(await providers.generate_image('a cat'), self.image)
        self.assertEqual(len(self.calls), 1)
        name, auth, fields = self.calls[0]
        self.assertEqual((name, auth), ('cloud', 'Bearer secret'))
        self.assertEqual(dict(fields), {'prompt': 'a cat', 'width': '1024', 'height': '1024'})

    async def test_primary_quota_and_server_failure_call_keyless_backup(self):
        for status in (429, 500):
            with self.subTest(status=status):
                self.calls.clear()
                providers._last_ovh_request = None
                self.primary_status = status
                self.assertEqual(await providers.generate_image('cat'), self.image)
                self.assertEqual([call[0] for call in self.calls], ['cloud', 'ovh'])
                self.assertIsNone(self.calls[-1][1])
                self.assertEqual(self.calls[-1][2], {'prompt': 'cat'})

    async def test_request_rejection_does_not_bypass_filter(self):
        for status in (400, 403):
            with self.subTest(status=status):
                self.calls.clear()
                self.primary_status = status
                self.assertIsNone(await providers.generate_image('test'))
                self.assertEqual([call[0] for call in self.calls], ['cloud'])

    async def test_200_explicit_error_is_not_bypassed(self):
        self.primary_result = {'success': False, 'errors': [{'code': 3030}]}
        self.assertIsNone(await providers.generate_image('test'))
        self.assertEqual([call[0] for call in self.calls], ['cloud'])

    async def test_network_failure_uses_backup(self):
        with patch.object(providers, 'fetch_cloudflare', new=AsyncMock(
            side_effect=providers.ImageProviderError('network error')
        )):
            self.assertEqual(await providers.generate_image('test'), self.image)
        self.assertEqual([call[0] for call in self.calls], ['ovh'])

    async def test_malformed_primary_response_uses_backup(self):
        self.primary_result = {'result': None}
        self.assertEqual(await providers.generate_image('test'), self.image)
        self.assertEqual([call[0] for call in self.calls], ['cloud', 'ovh'])

    async def test_invalid_primary_image_uses_backup(self):
        self.primary_result = {'result': {'image': base64.b64encode(b'html').decode()}}
        self.assertEqual(await providers.generate_image('test'), self.image)
        self.assertEqual([call[0] for call in self.calls], ['cloud', 'ovh'])

    async def test_missing_primary_config_goes_to_backup(self):
        with patch.dict(os.environ, {'CLOUDFLARE_AI_TOKEN': ''}):
            self.assertEqual(await providers.generate_image('cat'), self.image)
        self.assertEqual([call[0] for call in self.calls], ['ovh'])

    async def test_both_fail_returns_none(self):
        self.primary_status = 429
        self.backup_image = b'<html>not an image</html>'
        self.assertIsNone(await providers.generate_image('test'))

    async def test_ovh_requests_wait_at_least_35_seconds_even_after_failure(self):
        providers._last_ovh_request = 100
        with patch.object(providers, 'time', SimpleNamespace(monotonic=lambda: 105)), \
             patch.object(providers.asyncio, 'sleep', new=AsyncMock()) as sleep:
            self.assertEqual(await providers.fetch_ovh('cat'), self.image)
        sleep.assert_awaited_once_with(30)
        self.assertEqual(providers._last_ovh_request, 105)

    async def test_primary_redirect_is_not_followed(self):
        self.primary_status = 302
        self.assertIsNone(await providers.generate_image('test'))
        self.assertEqual([call[0] for call in self.calls], ['cloud'])

    async def test_oversized_body_is_rejected(self):
        self.primary_status = 429
        self.backup_image = b'x' * 100
        with patch.object(providers, 'MAX_IMAGE_BYTES', 10):
            self.assertIsNone(await providers.generate_image('test'))


class RolloutTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_or_unknown_mode_keeps_pollinations(self):
        for mode in ('', 'legacy', 'typo'):
            with self.subTest(mode=mode), patch.dict(os.environ, {'IMAGE_PROVIDER_MODE': mode}), \
                 patch.object(image_gen, 'fetch_image_pollinations', new=AsyncMock(return_value=b'old')) as old, \
                 patch.object(providers, 'generate_image', new_callable=AsyncMock) as new:
                self.assertEqual(await image_gen.generate_image('cat'), b'old')
                old.assert_awaited_once_with('cat')
                new.assert_not_awaited()

    async def test_explicit_mode_uses_new_chain_not_pollinations(self):
        with patch.dict(os.environ, {'IMAGE_PROVIDER_MODE': 'cloudflare'}), \
             patch.object(image_gen, 'fetch_image_pollinations', new_callable=AsyncMock) as old, \
             patch.object(providers, 'generate_image', new=AsyncMock(return_value=b'new')) as new:
            self.assertEqual(await image_gen.generate_image('cat'), b'new')
            new.assert_awaited_once_with('cat')
            old.assert_not_awaited()


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.id_patch = patch.object(image_gen, 'ADMIN_ID', 123)
        self.lock_patch = patch.object(image_gen, '_probe_lock', asyncio.Lock())
        self.id_patch.start()
        self.lock_patch.start()
        self.addCleanup(self.id_patch.stop)
        self.addCleanup(self.lock_patch.stop)
        self.message = SimpleNamespace(chat=SimpleNamespace(id=123, type='private'),
                                       from_user=SimpleNamespace(id=123),
                                       answer=AsyncMock(), answer_photo=AsyncMock())

    async def test_private_admin_checks_both_independently_without_accounting(self):
        image = image_bytes()
        with patch.object(providers, 'fetch_cloudflare', new=AsyncMock(return_value=image)) as cloud, \
             patch.object(providers, 'fetch_ovh', new=AsyncMock(return_value=image)) as ovh, \
             patch.object(image_gen, 'db') as db, \
             patch.object(image_gen, 'fetch_image_pollinations', new_callable=AsyncMock) as old:
            await image_gen.cmd_image_probe(self.message)
        cloud.assert_awaited_once()
        ovh.assert_awaited_once_with(cloud.await_args.args[0])
        self.assertEqual(self.message.answer_photo.await_count, 2)
        captions = [c.kwargs['caption'] for c in self.message.answer_photo.await_args_list]
        self.assertIn('Cloudflare', captions[0])
        self.assertIn('OVHcloud', captions[1])
        db.game_data.find_one.assert_not_called()
        db.economy.insert_one.assert_not_called()
        old.assert_not_awaited()

    async def test_non_admin_group_and_missing_user_cannot_trigger_calls(self):
        cases = [SimpleNamespace(id=456, type='private'), SimpleNamespace(id=123, type='group')]
        for chat in cases:
            self.message.chat = chat
            with patch.object(providers, 'fetch_cloudflare', new_callable=AsyncMock) as cloud, \
                 patch.object(providers, 'fetch_ovh', new_callable=AsyncMock) as ovh:
                await image_gen.cmd_image_probe(self.message)
            cloud.assert_not_awaited()
            ovh.assert_not_awaited()
        self.message.chat = SimpleNamespace(id=123, type='private')
        for user in (None, SimpleNamespace(id=456)):
            self.message.from_user = user
            with patch.object(providers, 'fetch_cloudflare', new_callable=AsyncMock) as cloud:
                await image_gen.cmd_image_probe(self.message)
            cloud.assert_not_awaited()

    async def test_error_does_not_mask_backup_and_does_not_expose_secret(self):
        with patch.object(providers, 'fetch_cloudflare', new=AsyncMock(
            side_effect=providers.ImageProviderError('secret token and details')
        )), patch.object(providers, 'fetch_ovh', new=AsyncMock(return_value=image_bytes())) as ovh:
            await image_gen.cmd_image_probe(self.message)
        ovh.assert_awaited_once()
        self.message.answer_photo.assert_awaited_once()
        texts = ' '.join(str(c.args[0]) for c in self.message.answer.await_args_list)
        self.assertNotIn('secret', texts)
        self.assertIn('Cloudflare', texts)

    async def test_duplicate_probe_is_rejected(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        async def delayed(_):
            entered.set()
            await release.wait()
            return image_bytes()
        with patch.object(providers, 'fetch_cloudflare', new=AsyncMock(side_effect=delayed)), \
             patch.object(providers, 'fetch_ovh', new=AsyncMock(return_value=image_bytes())) as ovh:
            first = asyncio.create_task(image_gen.cmd_image_probe(self.message))
            await entered.wait()
            await image_gen.cmd_image_probe(self.message)
            release.set()
            await first
        self.assertIn('уже выполняется', ' '.join(str(c.args[0]) for c in self.message.answer.await_args_list))
        ovh.assert_awaited_once()


class ImageHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.message = SimpleNamespace(
            chat=SimpleNamespace(id=123), from_user=SimpleNamespace(id=456, first_name='Test'),
            reply=AsyncMock(), answer_photo=AsyncMock())
        self.status = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
        self.message.reply.return_value = self.status
        self.db = SimpleNamespace(game_data=SimpleNamespace(find_one=AsyncMock(return_value=None), update_one=AsyncMock()),
                                  economy=SimpleNamespace(aggregate=MagicMock(), insert_one=AsyncMock()))
        self.sleep = patch.object(image_gen.asyncio, 'sleep', new=AsyncMock())
        self.db_patch = patch.object(image_gen, 'db', self.db)
        self.prompt = patch.object(image_gen, 'get_smart_prompt', new=AsyncMock(return_value='translated'))
        self.lock = patch.object(image_gen, 'generation_lock', asyncio.Lock())
        for p in (self.sleep, self.db_patch, self.prompt, self.lock):
            p.start()
            self.addCleanup(p.stop)

    async def test_free_image_records_only_after_delivery(self):
        async def delivery(**kwargs):
            self.db.game_data.update_one.assert_not_awaited()
        self.message.answer_photo.side_effect = delivery
        with patch.object(image_gen, 'generate_image', new=AsyncMock(return_value=image_bytes())):
            await image_gen.process_image_request(self.message, 'кот')
        self.message.answer_photo.assert_awaited_once()
        self.db.game_data.update_one.assert_awaited_once()
        self.assertEqual(self.db.game_data.update_one.await_args.args[1], {'$inc': {'count': 1}})
        self.db.economy.insert_one.assert_not_awaited()
        self.status.delete.assert_awaited_once()

    async def test_paid_image_records_250_after_delivery(self):
        self.db.game_data.find_one.return_value = {'count': 2}
        self.db.economy.aggregate.return_value.to_list = AsyncMock(return_value=[{'total_score': 500}])
        with patch.object(image_gen, 'generate_image', new=AsyncMock(return_value=image_bytes())):
            await image_gen.process_image_request(self.message, 'кот')
        self.db.economy.insert_one.assert_awaited_once()
        self.assertEqual(self.db.economy.insert_one.await_args.args[0]['score'], -250)
        self.db.game_data.update_one.assert_not_awaited()

    async def test_failed_generation_does_not_charge(self):
        with patch.object(image_gen, 'generate_image', new=AsyncMock(return_value=None)):
            await image_gen.process_image_request(self.message, 'кот')
        self.message.answer_photo.assert_not_awaited()
        self.db.game_data.update_one.assert_not_awaited()
        self.db.economy.insert_one.assert_not_awaited()
        self.assertIn('очки/лимиты сохранены', self.status.edit_text.await_args.args[0].lower())

    async def test_failed_paid_generation_does_not_charge(self):
        self.db.game_data.find_one.return_value = {'count': 2}
        self.db.economy.aggregate.return_value.to_list = AsyncMock(return_value=[{'total_score': 500}])
        with patch.object(image_gen, 'generate_image', new=AsyncMock(return_value=None)):
            await image_gen.process_image_request(self.message, 'кот')
        self.db.economy.insert_one.assert_not_awaited()
        self.message.answer_photo.assert_not_awaited()

    async def test_unconfirmed_telegram_does_not_write_accounting(self):
        self.message.answer_photo.side_effect = OSError('telegram unavailable')
        with patch.object(image_gen, 'generate_image', new=AsyncMock(return_value=image_bytes())):
            await image_gen.process_image_request(self.message, 'кот')
        self.db.game_data.update_one.assert_not_awaited()
        self.assertIn('Проверь чат', self.status.edit_text.await_args.args[0])

    async def test_db_failure_after_delivery_is_not_reported_as_delivery_failure(self):
        self.db.game_data.update_one.side_effect = OSError('db unavailable')
        with patch.object(image_gen, 'generate_image', new=AsyncMock(return_value=image_bytes())):
            await image_gen.process_image_request(self.message, 'кот')
        self.message.answer_photo.assert_awaited_once()
        self.assertIn('Картинка отправлена', self.status.edit_text.await_args.args[0])
        self.status.delete.assert_not_awaited()

    async def test_queued_requests_recheck_last_free_attempt(self):
        state = {'count': 1, 'balance': 250}
        entered = asyncio.Event()
        release = asyncio.Event()
        second_queued = asyncio.Event()

        async def find_usage(_):
            return {'count': state['count']}

        async def update_usage(*args, **kwargs):
            state['count'] += 1

        async def balance(*args, **kwargs):
            return [{'total_score': state['balance']}]

        async def charge(entry):
            state['balance'] += entry['score']

        async def generate(_):
            if not entered.is_set():
                entered.set()
                await release.wait()
            return image_bytes()

        second_status = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
        async def reply_second(*args, **kwargs):
            second_queued.set()
            return second_status
        second = SimpleNamespace(chat=self.message.chat, from_user=self.message.from_user,
                                 reply=AsyncMock(side_effect=reply_second), answer_photo=AsyncMock())
        self.db.game_data.find_one.side_effect = find_usage
        self.db.game_data.update_one.side_effect = update_usage
        self.db.economy.aggregate.return_value.to_list = AsyncMock(side_effect=balance)
        self.db.economy.insert_one.side_effect = charge
        with patch.object(image_gen, 'generate_image', new=AsyncMock(side_effect=generate)) as render:
            first_task = asyncio.create_task(image_gen.process_image_request(self.message, 'one'))
            await entered.wait()
            second_task = asyncio.create_task(image_gen.process_image_request(second, 'two'))
            await second_queued.wait()
            self.assertEqual(self.db.game_data.find_one.await_count, 1)
            release.set()
            await asyncio.gather(first_task, second_task)
        self.assertEqual(render.await_count, 2)
        self.assertEqual(state, {'count': 2, 'balance': 0})
        self.db.game_data.update_one.assert_awaited_once()
        self.db.economy.insert_one.assert_awaited_once()
        self.assertIn('Бесплатная', self.message.answer_photo.await_args.kwargs['caption'])
        self.assertIn('250', second.answer_photo.await_args.kwargs['caption'])

    async def test_queued_paid_request_rechecks_balance(self):
        state = {'balance': 250}
        entered = asyncio.Event()
        release = asyncio.Event()
        second_queued = asyncio.Event()
        self.db.game_data.find_one.return_value = {'count': 2}

        async def balance(*args, **kwargs):
            return [{'total_score': state['balance']}]

        async def charge(entry):
            state['balance'] += entry['score']

        async def generate(_):
            entered.set()
            await release.wait()
            return image_bytes()

        second_status = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
        async def reply_second(*args, **kwargs):
            second_queued.set()
            return second_status
        second = SimpleNamespace(chat=self.message.chat, from_user=self.message.from_user,
                                 reply=AsyncMock(side_effect=reply_second), answer_photo=AsyncMock())
        self.db.economy.aggregate.return_value.to_list = AsyncMock(side_effect=balance)
        self.db.economy.insert_one.side_effect = charge
        with patch.object(image_gen, 'generate_image', new=AsyncMock(side_effect=generate)) as render:
            first_task = asyncio.create_task(image_gen.process_image_request(self.message, 'one'))
            await entered.wait()
            second_task = asyncio.create_task(image_gen.process_image_request(second, 'two'))
            await second_queued.wait()
            release.set()
            await asyncio.gather(first_task, second_task)
        render.assert_awaited_once()
        self.db.economy.insert_one.assert_awaited_once()
        self.assertEqual(state['balance'], 0)
        second.answer_photo.assert_not_awaited()
        self.assertIn('Твой баланс: `0`', second_status.edit_text.await_args.args[0])

    async def test_cannot_check_usage_does_not_generate(self):
        self.db.game_data.find_one.side_effect = OSError('db unavailable')
        with patch.object(image_gen, 'generate_image', new_callable=AsyncMock) as render:
            await image_gen.process_image_request(self.message, 'кот')
        render.assert_not_awaited()
        self.assertIn('Не удалось проверить лимит', self.status.edit_text.await_args.args[0])

    async def test_cannot_check_balance_does_not_generate(self):
        self.db.game_data.find_one.return_value = {'count': 2}
        self.db.economy.aggregate.return_value.to_list = AsyncMock(side_effect=OSError('db unavailable'))
        with patch.object(image_gen, 'generate_image', new_callable=AsyncMock) as render:
            await image_gen.process_image_request(self.message, 'кот')
        render.assert_not_awaited()
        self.assertIn('Не удалось проверить баланс', self.status.edit_text.await_args.args[0])

    async def test_insufficient_balance_does_not_generate(self):
        self.db.game_data.find_one.return_value = {'count': 2}
        self.db.economy.aggregate.return_value.to_list = AsyncMock(return_value=[{'total_score': 100}])
        with patch.object(image_gen, 'generate_image', new_callable=AsyncMock) as generate:
            await image_gen.process_image_request(self.message, 'кот')
        generate.assert_not_awaited()
        self.message.answer_photo.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()

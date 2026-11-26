"""Фоновые задачи: падение обязано попасть в лог, а не исчезнуть."""
import asyncio
import logging
import unittest
from unittest.mock import Mock, patch

import background


class SpawnTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.addCleanup(setattr, background, '_tasks', set())
        background._tasks.clear()

    async def test_result_is_returned_and_reference_is_released(self):
        ran = []

        async def work():
            ran.append(True)
            return 42

        task = background.spawn(work(), name='ok')
        self.assertIsInstance(task, asyncio.Task)
        self.assertEqual(await task, 42)
        await background.wait_background()
        self.assertEqual(ran, [True])
        self.assertEqual(background.pending(), 0)

    async def test_failure_is_logged_with_the_task_name_and_original_error(self):
        async def boom():
            raise RuntimeError('архив пуст')

        with self.assertLogs('background', level='ERROR') as captured:
            task = background.spawn(boom(), name='nightly')
            with self.assertRaises(RuntimeError):
                await task
            await background.wait_background()
        self.assertIn('nightly', captured.output[0])
        self.assertIn('архив пуст', captured.output[0])

    async def test_on_error_callback_receives_the_exception(self):
        seen = Mock()

        async def boom():
            raise ValueError('нет данных')

        task = background.spawn(boom(), name='recover', on_error=seen)
        with self.assertLogs('background', level='ERROR'):
            with self.assertRaises(ValueError):
                await task
        seen.assert_called_once()
        self.assertIsInstance(seen.call_args.args[0], ValueError)

    async def test_a_broken_on_error_callback_does_not_escape(self):
        def broken(_error):
            raise KeyError('и это тоже')

        async def boom():
            raise ValueError('первая ошибка')

        with self.assertLogs('background', level='ERROR'):
            task = background.spawn(boom(), name='nested', on_error=broken)
            with self.assertRaises(ValueError):
                await task

    async def test_cancelled_task_is_not_reported_as_a_failure(self):
        started = asyncio.Event()

        async def forever():
            started.set()
            await asyncio.sleep(30)

        task = background.spawn(forever(), name='cancelled')
        await started.wait()
        task.cancel()
        with self.assertNoLogs('background', level='ERROR'):
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_wait_background_times_out_on_a_task_that_never_finishes(self):
        async def forever():
            await asyncio.sleep(30)

        task = background.spawn(forever(), name='stuck')
        with self.assertRaises(TimeoutError):
            await background.wait_background(timeout=0.01)
        task.cancel()

class SpawnWithoutLoopTests(unittest.TestCase):
    """Без запущенного цикла spawn обязан закрыть корутину, а не оставить её висеть."""

    def test_spawn_closes_the_coroutine_instead_of_leaking_it(self):
        async def work():
            return 1

        coro = work()
        with self.assertRaises(RuntimeError):
            background.spawn(coro)
        self.assertIsNone(coro.cr_frame, 'корутина должна быть закрыта, иначе RuntimeWarning')
        self.assertIsNone(coro.cr_await)


if __name__ == '__main__':
    unittest.main()

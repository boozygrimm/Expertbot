"""Фоновые задачи, которые не теряют свои исключения.

`asyncio.create_task(...)` без ссылки на задачу — тихая ловушка: если корутина
упадёт, трейсбек уйдёт в лог как «Task exception was never retrieved», и никто
об этом не узнает. Именно так в `handlers/viselitsa.py` годами жил `NameError`
в таймауте хода виселицы.

`spawn()` держит ссылку до завершения, пишет ошибку в лог с понятным
контекстом и опционально зовёт `on_error`. Задачи можно дождаться в тестах
через `wait_background()` — иначе проверка гонки зависела бы от `sleep`.
"""
import asyncio
import logging

logger = logging.getLogger(__name__)

_tasks: set[asyncio.Task] = set()


def spawn(coro, *, name: str = '', on_error=None) -> asyncio.Task | None:
    """Запустить корутину фоном, сохранив ссылку и залогировав падение."""
    try:
        task = asyncio.create_task(coro, name=name or None)
    except RuntimeError:
        # Нет запущенного цикла: закрываем корутину, чтобы не было
        # «coroutine was never awaited», и честно сообщаем вызывающему.
        coro.close()
        raise
    _tasks.add(task)

    def finished(done: asyncio.Task) -> None:
        _tasks.discard(done)
        if done.cancelled():
            return
        error = done.exception()
        if error is None:
            return
        logger.error('Фоновая задача %s упала: %s', name or done.get_name(),
                     error, exc_info=error)
        if on_error is not None:
            try:
                on_error(error)
            except Exception:  # обработчик ошибки не должен ронять цикл
                logger.exception('Обработчик ошибки фоновой задачи %s упал', name)

    task.add_done_callback(finished)
    return task


def pending() -> int:
    """Сколько фоновых задач ещё не завершилось."""
    return len([task for task in _tasks if not task.done()])


async def wait_background(timeout: float = 5.0) -> None:
    """Дождаться текущих фоновых задач (для тестов и аккуратной остановки)."""
    while True:
        active = [task for task in _tasks if not task.done()]
        if not active:
            return
        await asyncio.wait(active, timeout=timeout)
        if any(not task.done() for task in active):
            raise TimeoutError(f'Фоновые задачи не завершились за {timeout} с')

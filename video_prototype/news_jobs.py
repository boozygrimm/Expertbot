"""Opt-in news jobs sharing the existing video worker's single execution slot.

News jobs save their request and artifacts locally. They never upload or send
anything; an interrupted UUID is never silently replayed. Not enabled by default.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import uuid


class NewsBusy(Exception):
    pass


class NewsConflict(Exception):
    pass


def validate_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError('Неверный идентификатор')
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError('Неверный идентификатор') from None
    return value


def validate_request(value: object) -> tuple[str, dict]:
    if not isinstance(value, dict) or set(value) != {'request_id', 'script'}:
        raise ValueError('Неверная новостная задача')
    request_id = validate_id(value['request_id'])
    # Lazy import: the ordinary worker still runs when news assets are absent.
    from news_prototype.story_audio import validate_script
    return request_id, validate_script(value['script'])


MAX_PROGRAM_STORIES = 4
MAX_PROGRAM_BYTES = 2_000_000


def validate_program(value: object) -> tuple[str, list[dict]]:
    if not isinstance(value, dict) or set(value) != {'request_id', 'scripts'}:
        raise ValueError('Неверная задача выпуска')
    request_id = validate_id(value['request_id'])
    scripts = value['scripts']
    if not isinstance(scripts, list) or not 2 <= len(scripts) <= MAX_PROGRAM_STORIES:
        raise ValueError('Нужно от двух до четырёх сюжетов')
    from news_prototype.story_audio import validate_script
    checked = [validate_script(script) for script in scripts]
    if any(not script['ticker'] or {t['speaker'] for t in script['turns']} != {'Бендер', 'Киберсенсей'}
           for script in checked):
        raise ValueError('В каждом сюжете нужны оба ведущих и строка')
    return request_id, checked


def validate_digest(value: object) -> tuple[str, list[str]]:
    if not isinstance(value, dict) or set(value) != {'request_id', 'inputs'}:
        raise ValueError('Неверная задача дайджеста')
    request_id = validate_id(value['request_id'])
    inputs = value['inputs']
    if not isinstance(inputs, list) or len(inputs) < 2:
        raise ValueError('Нужны хотя бы два разных сюжета')
    checked = [validate_id(item) for item in inputs]
    if len(set(checked)) != len(checked) or request_id in checked:
        raise ValueError('Повторяющиеся сюжеты')
    return request_id, checked


class NewsJobs:
    def __init__(self, shared, root: Path, run_job, run_digest=None, run_program=None):
        self.shared, self.root, self.run_job, self.run_digest = shared, Path(root), run_job, run_digest
        self.run_program = run_program
        self.states = {}
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError('Неверная папка новостных задач')
        self.shared.external_busy = self.has_unresolved

    def has_unresolved(self) -> bool:
        """Fail closed after restart until interrupted news jobs are reconciled.

        Called under the shared lock. A child may still be alive after a worker
        crash, so an incomplete durable reservation must block *all* new jobs.
        Only a finished checked video or the parent-written failure marker is
        considered terminal. There is intentionally no automatic replay/cleanup.
        """
        try:
            for folder in self.root.iterdir():
                try:
                    validate_id(folder.name)
                except ValueError:
                    continue
                if folder.is_symlink() or not folder.is_dir():
                    return True
                failure = folder / 'failed'
                if not failure.is_symlink() and failure.is_file():
                    continue
                work = folder / 'work'
                marker = work / 'status.json'
                if work.is_symlink() or marker.is_symlink() or marker.stat().st_size > 4096:
                    return True
                info = json.loads(marker.read_text(encoding='utf-8'))
                if info.get('stage') != 'ready_for_review':
                    return True
                fd, _ = self.open_video(folder.name)
                os.close(fd)
        except (OSError, ValueError, TypeError, AttributeError):
            return True
        return False

    def _directory(self, request_id: str) -> Path:
        return self.root / validate_id(request_id)

    def status(self, request_id: str) -> dict | None:
        folder = self._directory(request_id)
        with self.shared.lock:
            state = self.states.get(request_id)
            if state:
                return {'request_id': request_id, 'status': state}
        if folder.is_symlink() or not folder.is_dir():
            return None
        failure = folder / 'failed'
        if failure.is_file() and not failure.is_symlink():
            return {'request_id': request_id, 'status': 'failed'}
        work = folder / 'work'
        if work.is_symlink() or not work.is_dir():
            return {'request_id': request_id, 'status': 'interrupted'}
        status_file = work / 'status.json'
        try:
            if status_file.is_symlink() or status_file.stat().st_size > 4096:
                raise ValueError('invalid status')
            stage = json.loads(status_file.read_text(encoding='utf-8'))['stage']
            if stage == 'ready_for_review':
                fd, _ = self.open_video(request_id)
                os.close(fd)
                state = 'ready'
            else:
                # Even render_failed can still have a running child until the
                # parent has observed its exit and written the failure marker.
                state = 'interrupted'
        except (KeyError, TypeError, ValueError, OSError):
            state = 'interrupted'
        return {'request_id': request_id, 'status': state}

    def submit(self, request_id: str, script: dict) -> tuple[dict, bool]:
        request_id, script = validate_request({'request_id': request_id, 'script': script})
        return self._submit(request_id, script, self.run_job)

    def submit_program(self, request_id: str, scripts: list[dict]) -> tuple[dict, bool]:
        request_id, scripts = validate_program({'request_id': request_id, 'scripts': scripts})
        if self.run_program is None:
            raise ValueError('Выпуск недоступен')
        return self._submit(request_id, {'scripts': scripts}, self.run_program)

    def submit_digest(self, request_id: str, inputs: list[str]) -> tuple[dict, bool]:
        request_id, inputs = validate_digest({'request_id': request_id, 'inputs': inputs})
        if self.run_digest is None:
            raise ValueError('Сборка дайджеста недоступна')
        for source_id in inputs:
            source_folder = self._directory(source_id)
            try:
                if source_folder.is_symlink() or (source_folder / 'request.json').is_symlink():
                    raise ValueError
                # Digests may only contain original story jobs, never other digests.
                request_file = source_folder / 'request.json'
                if request_file.stat().st_size > 600_000:
                    raise ValueError
                stored = json.loads(request_file.read_text(encoding='utf-8'))
                validate_request({'request_id': source_id, 'script': stored})
                if self.status(source_id) != {'request_id': source_id, 'status': 'ready'}:
                    raise ValueError
            except (OSError, ValueError, TypeError):
                raise ValueError('Сюжет недоступен или ещё не готов') from None
        return self._submit(request_id, {'inputs': inputs}, self.run_digest)

    def _submit(self, request_id: str, payload: dict, job_runner) -> tuple[dict, bool]:
        folder = self._directory(request_id)
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        with self.shared.lock:
            if request_id in self.shared.jobs:
                raise NewsConflict('Идентификатор уже используется')
            if folder.exists() or folder.is_symlink():
                if folder.is_symlink() or not folder.is_dir():
                    raise NewsConflict('Идентификатор уже используется')
                request = folder / 'request.json'
                try:
                    if request.is_symlink() or request.stat().st_size > MAX_PROGRAM_BYTES or request.read_bytes() != canonical:
                        raise NewsConflict('Идентификатор уже используется')
                except OSError:
                    raise NewsConflict('Результат предыдущего запуска неизвестен') from None
                # Read durable status outside the lock to avoid recursive locking.
                existing = True
            else:
                existing = False
                if self.shared.active is not None or self.has_unresolved():
                    raise NewsBusy('Воркер занят или есть незавершённая новостная задача')
                # Persistent reservation precedes any TTS or response to caller.
                folder.mkdir(mode=0o700)
                with (folder / 'request.json').open('xb') as target:
                    target.write(canonical)
                    target.flush()
                    os.fsync(target.fileno())
                self.states[request_id] = 'running'
                self.shared.active = request_id
                try:
                    threading.Thread(target=self._execute, args=(request_id, folder, job_runner), daemon=True).start()
                except RuntimeError:
                    self.shared.active = None
                    self.states[request_id] = 'interrupted'
                    raise
        if existing:
            return self.status(request_id), False
        return {'request_id': request_id, 'status': 'running'}, True

    def _execute(self, request_id: str, folder: Path, job_runner) -> None:
        try:
            job_runner(request_id, folder)
            # Never report success unless the renderer committed a checked file.
            work = folder / 'work'
            status = json.loads((work / 'status.json').read_text(encoding='utf-8'))
            if status.get('stage') != 'ready_for_review':
                raise ValueError('Не подтверждена готовность видео')
            fd, _ = self.open_video(request_id)
            os.close(fd)
            result = 'ready'
        except Exception as exc:
            print(f'Новостная задача {request_id}: ошибка {type(exc).__name__}', flush=True)
            result = 'failed'
            try:
                marker = folder / 'failed.tmp'
                marker.write_text('failed', encoding='utf-8')
                marker.replace(folder / 'failed')
            except OSError:
                pass
        with self.shared.lock:
            # Completed states live on disk, so memory stays bounded.
            self.states.pop(request_id, None)
            self.shared.active = None
        self.shared.schedule_cleanup()

    def open_video(self, request_id: str) -> tuple[int, int]:
        folder = self._directory(request_id)
        work = folder / 'work'
        if folder.is_symlink() or work.is_symlink() or not folder.is_dir() or not work.is_dir():
            raise FileNotFoundError('Видео недоступно')
        fd = os.open(work / 'video.mp4', os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
                raise FileNotFoundError('Видео недоступно')
            return fd, info.st_size
        except BaseException:
            os.close(fd)
            raise


def runner(repo_root: Path):
    """Run TTS/render in a child process; do not send its output to Telegram."""
    repo_root = Path(repo_root)
    def run(request_id: str, folder: Path) -> None:
        env = os.environ.copy()
        for name in ('BOT_TOKEN', 'VIDEO_WORKER_KEY', 'STORAGE_CHANNEL_ID', 'GEMINI_KEYS',
                     'GEMINI_API_KEYS', 'GEMINI_API_KEY'):
            env.pop(name, None)
        command = [sys.executable, '-u', '-m', 'news_prototype.story_pipeline',
                   '--script', str(folder / 'request.json'), '--output-dir', str(folder / 'work')]
        with (folder / 'worker.log').open('ab') as log:
            process = subprocess.run(command, cwd=repo_root, env=env, stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True)
        if process.returncode:
            raise RuntimeError('Не удалось создать новостной сюжет')
    return run


def program_runner(repo_root: Path):
    """Render approved stories sequentially and assemble one final video."""
    repo_root = Path(repo_root)
    def run(request_id: str, folder: Path) -> None:
        env = os.environ.copy()
        for name in ('BOT_TOKEN', 'VIDEO_WORKER_KEY', 'STORAGE_CHANNEL_ID', 'GEMINI_KEYS',
                     'GEMINI_API_KEYS', 'GEMINI_API_KEY'):
            env.pop(name, None)
        command = [sys.executable, '-u', '-m', 'news_prototype.program_job',
                   '--request', str(folder / 'request.json'), '--output-dir', str(folder / 'work')]
        with (folder / 'worker.log').open('ab') as log:
            process = subprocess.run(command, cwd=repo_root, env=env, stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True)
        if process.returncode:
            raise RuntimeError('Не удалось создать выпуск')
    return run


def digest_runner(repo_root: Path, root: Path):
    """Assemble on the worker's shared slot, without TTS or Telegram access."""
    repo_root, root = Path(repo_root), Path(root)
    def run(request_id: str, folder: Path) -> None:
        env = os.environ.copy()
        for name in ('BOT_TOKEN', 'VIDEO_WORKER_KEY', 'STORAGE_CHANNEL_ID', 'GEMINI_KEYS',
                     'GEMINI_API_KEYS', 'GEMINI_API_KEY', 'GEMINI_TTS_KEYS'):
            env.pop(name, None)
        command = [sys.executable, '-u', '-m', 'news_prototype.digest_job',
                   '--request', str(folder / 'request.json'), '--root', str(root),
                   '--output-dir', str(folder / 'work')]
        with (folder / 'worker.log').open('ab') as log:
            process = subprocess.run(command, cwd=repo_root, env=env, stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True)
        if process.returncode:
            raise RuntimeError('Не удалось собрать дайджест')
    return run

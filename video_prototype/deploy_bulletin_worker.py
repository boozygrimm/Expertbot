"""Guarded single-host bulletin worker switch; does not touch bot settings or jobs.

The pinned source archive is rechecked and extracted to a fresh directory.
There is no reliable idle/maintenance API in the old worker: operators must keep
clients quiet; child-process and recent-file checks reduce but cannot eliminate
an admission race. On an ambiguous post-restart result, do NOT restart again.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.request
import uuid

UNIT = 'bender-video.service'
BASE = Path('/data/video-prototype')
SMOKE = Path('/etc/systemd/system/bender-video.service.d/smoke.conf')
COMMIT = '44ccf03d183282e42174f3623d340f5c7076b5ee'
ARCHIVE_SHA = '0efeb50d105fc981cc68c4182955692b846dfb6d13ab4ed17f0e56cd06e94b29'
OLD = BASE / 'news-vision-8HnuhB' / 'Expertbot-6283a72b0dc8f307ece4d6c4cb975c5d95a320e8'
PYTHON = BASE / 'news-worker-stage-c55ke7g3/runtime/bin/python'
NEWS_ROOT = BASE / 'news-jobs'
JOBS = BASE / 'jobs'
PROC = Path('/proc')
EXPECTED_DROPINS = {'smoke.conf', 'tts-gemini.conf', 'zz-news-owner-trial.conf'}
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_EXTRACT = 100 * 1024 * 1024


class SafetyError(Exception):
    pass


def command(*args, timeout=30, cwd=None):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                          check=True, timeout=timeout, cwd=cwd).stdout.decode().strip()


def prop(name):
    return command('systemctl', 'show', UNIT, '-p', name, '--value')


def read_file(path: Path, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise SafetyError('Файл отсутствует или небезопасен')
    return path.read_bytes()


def extract_source(stage: Path, dest: Path) -> Path:
    if stage.is_symlink() or not stage.is_dir() or stage.parent != BASE or not re.fullmatch(r'bulletin-stage-[A-Za-z0-9]{8}', stage.name):
        raise SafetyError('Неожиданная подготовленная папка')
    blob = read_file(stage / 'source.tar.gz', MAX_ARCHIVE)
    if hashlib.sha256(blob).hexdigest() != ARCHIVE_SHA:
        raise SafetyError('Архив не прошёл проверку')
    root = 'Expertbot-' + COMMIT
    seen = set(); total = 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz') as tar:
        members = tar.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or not path.parts or path.parts[0] != root or '..' in path.parts
                    or not (member.isdir() or member.isfile()) or path in seen):
                raise SafetyError('Недопустимый элемент архива')
            total += member.size
            if total > MAX_EXTRACT:
                raise SafetyError('Архив слишком большой')
            seen.add(path)
        for member in members:
            path = PurePosixPath(member.name)
            target = dest.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(mode=0o755, parents=True, exist_ok=True)
                target.chmod(0o755)
                continue
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            parent = target.parent
            while parent != dest:
                parent.chmod(0o755)
                parent = parent.parent
            stream = tar.extractfile(member)
            if stream is None:
                raise SafetyError('Не удалось прочитать архив')
            mode = 0o755 if member.mode & 0o111 else 0o644
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), mode)
            os.fchmod(fd, mode)
            with os.fdopen(fd, 'wb') as out:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk: break
                    out.write(chunk)
    code = dest / root
    if not (code / 'video_prototype/worker.py').is_file():
        raise SafetyError('Нет воркера в архиве')
    return code


def process_snapshot():
    if prop('ActiveState') != 'active' or prop('SubState') != 'running' or prop('User') != 'user':
        raise SafetyError('Служба не в ожидаемом состоянии')
    pid = int(prop('MainPID'))
    if pid <= 0:
        raise SafetyError('Не найден PID')
    proc = PROC / str(pid)
    args = [s.decode('utf-8') for s in proc.joinpath('cmdline').read_bytes().split(b'\0') if s]
    if len(args) < 2 or args[0] != str(PYTHON) or str(OLD / 'video_prototype/worker.py') not in args:
        raise SafetyError('Работает другая версия воркера')
    def option(name, default):
        values = [arg.split('=', 1)[1] for arg in args if arg.startswith(name + '=')]
        values += [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == name]
        if len(values) > 1: raise SafetyError('Неоднозначный параметр запуска')
        return values[0] if values else default
    if option('--output-dir', str(JOBS)) != str(JOBS):
        raise SafetyError('Неожиданный каталог задач')
    port = int(option('--port', '8088'))
    if not 1 <= port <= 65535: raise SafetyError('Неожиданный порт')
    env = dict(value.split(b'=', 1) for value in proc.joinpath('environ').read_bytes().split(b'\0') if b'=' in value)
    key = env.get(b'VIDEO_WORKER_KEY', b'').decode()
    if len(key) < 24 or env.get(b'NEWS_WORKER_ENABLED') != b'1':
        raise SafetyError('Настройки службы не подтверждены')
    return pid, port, key


def quiet(pid: int, *, recent_seconds: int = 120):
    """Conservative observations, not an atomic lock against new requests."""
    for proc in PROC.glob('[0-9]*'):
        try:
            if not proc.name.isdigit(): continue
            status = proc.joinpath('status').read_text()
            parent = next(int(line.split()[1]) for line in status.splitlines() if line.startswith('PPid:'))
            if parent == pid:
                raise SafetyError('Есть дочерний процесс воркера')
            if int(proc.name) == pid: continue
            args = proc.joinpath('cmdline').read_bytes().split(b'\0')
            if any((arg.endswith(b'/video_prototype/worker.py') or arg.endswith(b'/video_prototype/pipeline.py')
                    or arg in (b'news_prototype.story_pipeline', b'news_prototype.program_job',
                               b'news_prototype.bulletin_job')) for arg in args):
                raise SafetyError('Найден другой процесс видео или новостей')
        except (FileNotFoundError, ProcessLookupError, StopIteration):
            continue
    if JOBS.is_symlink() or not JOBS.is_dir():
        raise SafetyError('Каталог видео недоступен')
    now = time.time()
    for folder in JOBS.iterdir():
        try:
            if str(uuid.UUID(folder.name)) != folder.name: continue
        except ValueError: continue
        if folder.is_symlink() or not folder.is_dir(): raise SafetyError('Небезопасная папка видео')
        log = folder / 'worker.log'
        if log.is_symlink(): raise SafetyError('Небезопасный журнал видео')
        if now - folder.stat().st_mtime < recent_seconds or (log.exists() and now - log.stat().st_mtime < recent_seconds):
            raise SafetyError('Недавно работала видеозадача; подождите и проверьте снова')
        # The existing worker starts retention cleanup on restart. Do not cause
        # even a normal 30-day cleanup without an explicit separate decision.
        # A day of headroom also excludes folders near the threshold.
        if log.is_file() and now - max(folder.stat().st_mtime, log.stat().st_mtime) >= 29 * 24 * 60 * 60:
            raise SafetyError('Есть старые видео, которые перезапуск может удалить; нужна отдельная проверка')
    if NEWS_ROOT.is_symlink() or not NEWS_ROOT.is_dir():
        raise SafetyError('Каталог новостей недоступен')
    for folder in NEWS_ROOT.iterdir():
        try:
            if str(uuid.UUID(folder.name)) != folder.name: continue
        except ValueError: continue
        if folder.is_symlink() or not folder.is_dir(): raise SafetyError('Небезопасная папка новостей')
        failed = folder/'failed'; work = folder/'work'; status = work/'status.json'; video = work/'video.mp4'
        if failed.is_file() and not failed.is_symlink(): continue
        try:
            ready = (not work.is_symlink() and not status.is_symlink() and status.stat().st_size <= 4096
                     and json.loads(status.read_text()).get('stage') == 'ready_for_review'
                     and not video.is_symlink() and video.is_file() and video.stat().st_size > 0)
        except (OSError, ValueError, TypeError, AttributeError):
            ready = False
        if not ready: raise SafetyError('Есть неподтверждённая новостная задача')
    if int(prop('MainPID')) != pid:
        raise SafetyError('Процесс службы изменился')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


def health(port: int, key: str) -> set[str]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    req = urllib.request.Request(f'http://127.0.0.1:{port}/health', headers={'Authorization': 'Bearer ' + key})
    with opener.open(req, timeout=3) as response:
        data = json.loads(response.read(65537))
    capabilities = data.get('capabilities') if isinstance(data, dict) else None
    if data.get('status') != 'ok' or not isinstance(capabilities, list) or any(not isinstance(x, str) for x in capabilities):
        raise SafetyError('Health не подтвердился')
    return set(capabilities)


def changed_smoke(data: bytes, new: Path) -> bytes:
    lines = data.decode('utf-8').splitlines(keepends=True)
    match = []
    needle = str(OLD / 'video_prototype/worker.py')
    for i, line in enumerate(lines):
        if line.strip().startswith('ExecStart=') and needle in line:
            if line.count(needle) != 1 or line.rstrip().endswith('\\'):
                raise SafetyError('Команда службы неоднозначна')
            match.append(i)
    if len(match) != 1: raise SafetyError('Команда службы изменилась')
    i = match[0]
    lines[i] = lines[i].replace(needle, str(new / 'video_prototype/worker.py'), 1)
    return ''.join(lines).encode('utf-8')


def replace_smoke(data: bytes):
    info = SMOKE.stat()
    if SMOKE.is_symlink() or not stat.S_ISREG(info.st_mode): raise SafetyError('Файл службы небезопасен')
    fd, tmp = tempfile.mkstemp(prefix='.bulletin-switch-', dir=SMOKE.parent)
    try:
        os.fchmod(fd, stat.S_IMODE(info.st_mode)); os.fchown(fd, info.st_uid, info.st_gid)
        with os.fdopen(fd, 'wb') as out:
            fd = -1; out.write(data); out.flush(); os.fsync(out.fileno())
        os.replace(tmp, SMOKE)
    finally:
        if fd >= 0: os.close(fd)
        if os.path.exists(tmp): os.unlink(tmp)


def deploy(stage: Path):
    if os.geteuid() != 0: raise SafetyError('Нужен запуск через sudo')
    if BASE.is_symlink() or not BASE.is_dir(): raise SafetyError('Каталог установки недоступен')
    dropins = prop('DropInPaths').split()
    if ({Path(p).name for p in dropins} != EXPECTED_DROPINS or
            any(Path(p).parent != SMOKE.parent or Path(p).is_symlink() for p in dropins)):
        raise SafetyError('Список настроек службы изменился')
    old_config = read_file(SMOKE, 128_000)
    pid, port, key = process_snapshot()
    old_caps = health(port, key)
    if not {'animated-sign-v1', 'voice-notes-v1', 'news-story-v1'} <= old_caps:
        raise SafetyError('Прежний воркер не подтвердил возможности')
    quiet(pid)
    target = Path(tempfile.mkdtemp(prefix='bulletin-code-', dir=BASE)); target.chmod(0o755)
    code = extract_source(stage, target)
    command('runuser', '-u', 'user', '--', str(PYTHON), '-m', 'news_prototype.bulletin_preflight', cwd=code, timeout=40)
    new_config = changed_smoke(old_config, code)
    backup = Path(tempfile.mkdtemp(prefix='bulletin-backup-', dir=BASE)); backup.chmod(0o700)
    backup_file = backup/'smoke.conf'
    with backup_file.open('xb') as f:
        os.chmod(backup_file, 0o600); f.write(old_config); f.flush(); os.fsync(f.fileno())
    print('Резервная копия настроек:', backup, flush=True)
    # All these checks repeat immediately before touching systemd; a request can
    # still race after them, so operators must keep clients quiet.
    quiet(pid)
    if process_snapshot()[:2] != (pid, port) or read_file(SMOKE, 128_000) != old_config:
        raise SafetyError('Служба или настройки изменились')
    if not old_caps <= health(port, key):
        raise SafetyError('Возможности воркера изменились')
    replaced = restart_attempted = False
    try:
        replace_smoke(new_config); replaced = True
        command('systemctl', 'daemon-reload')
        restart_attempted = True
        command('systemctl', 'restart', UNIT, timeout=90)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                if (prop('ActiveState') == 'active' and int(prop('MainPID')) != pid and
                        old_caps | {'news-bulletin-v1'} <= health(port, key)):
                    print('Воркер обновлён: прежние возможности и news-bulletin-v1 подтверждены.', flush=True)
                    print('Рендер и публикация не запускались.', flush=True)
                    return
            except Exception:
                pass
            time.sleep(1)
        raise SafetyError('Новый health не подтвердился')
    except Exception:
        if replaced and not restart_attempted:
            try:
                replace_smoke(old_config); command('systemctl', 'daemon-reload')
                print('Настройка восстановлена без перезапуска.', flush=True)
            except Exception:
                print('Восстановление настройки не подтверждено.', flush=True)
        elif restart_attempted:
            print('После попытки перезапуска состояние неизвестно. Не перезапускайте повторно вслепую.', flush=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    args = parser.parse_args()
    try:
        deploy(args.stage)
    except Exception as exc:
        print('Обновление не подтверждено:', str(exc) if isinstance(exc, SafetyError) else type(exc).__name__)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

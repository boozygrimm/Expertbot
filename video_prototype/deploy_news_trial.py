"""Guarded, single-machine switch for the owner-only news trial on InstaBox.

Run as root only after the owner accepts a possible interruption of media jobs.
Never starts a second worker or prints service credentials. The old bot may
still submit ordinary work during the restart. Interrupted/uncertain results
must not be resubmitted blindly. This is deliberately pinned to one installation.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

UNIT = 'bender-video.service'
BASE = Path('/data/video-prototype')
STAGE = BASE / 'news-worker-stage-c55ke7g3'
COMMIT = '69df4097daa7ebb7d99e26d691666aa768627eb9'
CODE = STAGE / ('Expertbot-' + COMMIT)
RUNTIME = STAGE / 'runtime/bin/python'
BACKUP = BASE / 'news-rollback-lininx16'
SERVICE = Path('/etc/systemd/system/bender-video.service')
DROPIN = Path('/etc/systemd/system/bender-video.service.d/smoke.conf')
TTS = DROPIN.with_name('tts-gemini.conf')
NEWS = DROPIN.with_name('zz-news-owner-trial.conf')
OLD_CODE = '/data/video-prototype/code-voice-prod-be74202'
OLD_PYTHON = '/usr/bin/python3'
ARCHIVE_SHA = '654d6611add6162ecbd8da396f5b44eb6e0bc61d677c635dece5bae31ad83673'
EXPECTED = {
    SERVICE: '89ea8797ddf7122403c8fcab6fe40a9a0e986b8088bdf1cf27be78b222ad7635',
    DROPIN: '9cc2dc52c82ee1bc6deb7240fec41b0782f3569b772137270cd183b3a20cd1c6',
    TTS: '7909b0846ee13f155befaa722429e4aee5bb30d40a699bc7c85e5eb32d906a0e',
}
NEWS_CONTENT = b'[Service]\nEnvironment=NEWS_WORKER_ENABLED=1\n'


class DeploymentError(Exception):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_read(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise DeploymentError('Ожидаемый файл отсутствует или является ссылкой')
    return path.read_bytes()


def command(*args):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=30).stdout.strip()


def prop(name):
    return command('systemctl', 'show', UNIT, '-p', name, '--value')


def verify_source():
    """Check the complete extracted source against the pinned archive."""
    import tarfile
    archive = safe_read(STAGE / 'source.tar.gz')
    if digest(archive) != ARCHIVE_SHA or CODE.is_symlink() or not CODE.is_dir():
        raise DeploymentError('Архив или подготовленный каталог изменился')
    rootname = 'Expertbot-' + COMMIT
    expected_files = set()
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
        for member in tar.getmembers():
            p = PurePosixPath(member.name)
            if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0] != rootname:
                raise DeploymentError('Недопустимый путь в архиве')
            if member.isdir():
                continue
            if not member.isfile():
                raise DeploymentError('Недопустимый файл в архиве')
            relative = p.parts[1:]
            if not relative:
                raise DeploymentError('Недопустимый элемент архива')
            target = CODE.joinpath(*relative)
            if target in expected_files:
                raise DeploymentError('Повторный файл в архиве')
            expected_files.add(target)
            for parent in (target, *target.parents):
                if parent == CODE:
                    break
                if parent.is_symlink():
                    raise DeploymentError('Ссылка в подготовленном коде')
            content = tar.extractfile(member)
            if content is None or digest(content.read()) != digest(safe_read(target)):
                raise DeploymentError('Подготовленный код изменился')
    for root, dirs, files in os.walk(CODE, followlinks=False):
        for name in dirs:
            if (Path(root) / name).is_symlink():
                raise DeploymentError('Ссылка в подготовленном коде')
        for name in files:
            if Path(root) / name not in expected_files:
                raise DeploymentError('Лишний файл в подготовленном коде')
    if not RUNTIME.is_file() or not os.access(RUNTIME, os.X_OK):
        raise DeploymentError('Не найден подготовленный Python')


def updated_smoke(data):
    """Preserve the complete systemd command and change only the two pinned paths."""
    text = data.decode('utf-8')
    lines = text.splitlines(keepends=True)
    found = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith('ExecStart=') or not stripped[len('ExecStart='):].strip():
            continue
        if stripped.endswith('\\') or line.count(OLD_CODE) != 1 or line.count(OLD_PYTHON) != 1:
            raise DeploymentError('Команда запуска отличается от ожидаемой')
        lines[i] = line.replace(OLD_CODE, str(CODE), 1).replace(OLD_PYTHON, str(RUNTIME), 1)
        found += 1
    if found != 1:
        raise DeploymentError('Не найдена единственная ожидаемая команда запуска')
    return ''.join(lines).encode('utf-8')


def process_info():
    if prop('ActiveState') != 'active' or prop('SubState') != 'running':
        raise DeploymentError('Текущий воркер не работает')
    pid = int(prop('MainPID'))
    if pid <= 0 or prop('User') != 'user':
        raise DeploymentError('Неожиданный пользователь или PID службы')
    proc = Path('/proc') / str(pid)
    argv = [x.decode('utf-8') for x in (proc / 'cmdline').read_bytes().split(b'\0') if x]
    if not argv or argv[0] != OLD_PYTHON or OLD_CODE + '/video_prototype/worker.py' not in argv:
        raise DeploymentError('Запущенный воркер отличается от ожидаемого')
    environment = dict(entry.split(b'=', 1) for entry in (proc / 'environ').read_bytes().split(b'\0') if b'=' in entry)
    key = environment.get(b'VIDEO_WORKER_KEY', b'').decode('utf-8')
    if len(key) < 24:
        raise DeploymentError('Не найден ключ текущего воркера')
    port, output = 8088, str(BASE / 'jobs')
    for name in ('--port', '--output-dir'):
        values = [arg.split('=', 1)[1] for arg in argv if arg.startswith(name + '=')]
        values += [argv[i+1] for i, arg in enumerate(argv[:-1]) if arg == name]
        if len(values) > 1:
            raise DeploymentError('Неоднозначные параметры воркера')
        if values:
            if name == '--port':
                port = int(values[0])
            else:
                output = values[0]
    if not 1 <= port <= 65535 or not Path(output).is_absolute():
        raise DeploymentError('Не удалось определить параметры воркера')
    news_root = Path(output).parent / 'news-jobs'
    if news_root.is_symlink() or (news_root.exists() and (not news_root.is_dir() or any(news_root.iterdir()))):
        raise DeploymentError('Новостной каталог уже занят; требуется ручная проверка')
    return pid, key, port


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def health(key, port, required, seconds=15):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        req = urllib.request.Request(f'http://127.0.0.1:{port}/health',
                                     headers={'Authorization': 'Bearer ' + key})
        try:
            with opener.open(req, timeout=2) as response:
                obj = json.loads(response.read(65537))
            if obj.get('status') == 'ok' and set(required).issubset(obj.get('capabilities', [])):
                return True
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        time.sleep(.5)
    return False


def replace_file(path, data):
    info = path.stat()
    fd, name = tempfile.mkstemp(prefix='.news-switch-', dir=path.parent)
    try:
        os.fchmod(fd, info.st_mode & 0o777)
        os.fchown(fd, info.st_uid, info.st_gid)
        with os.fdopen(fd, 'wb') as stream:
            fd = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if fd >= 0:
            os.close(fd)
        if os.path.exists(name):
            os.unlink(name)


def create_news_dropin():
    fd = os.open(NEWS, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o644)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(NEWS_CONTENT)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        NEWS.unlink(missing_ok=True)
        raise


def preflight():
    verify_source()
    for path, expected in EXPECTED.items():
        if digest(safe_read(path)) != expected or digest(safe_read(BACKUP / path.name)) != expected:
            raise DeploymentError('Конфигурация или резервная копия изменились')
    if NEWS.exists() or NEWS.is_symlink():
        raise DeploymentError('Новостной drop-in уже существует')
    if set(prop('DropInPaths').split()) != {str(DROPIN), str(TTS)}:
        raise DeploymentError('Список drop-in изменился')
    old = safe_read(DROPIN)
    new = updated_smoke(old)
    pid, key, port = process_info()
    original_caps = ('animated-sign-v1', 'voice-notes-v1')
    if not health(key, port, original_caps):
        raise DeploymentError('Текущий воркер не подтвердил health')
    if int(prop('MainPID')) != pid:
        raise DeploymentError('Процесс воркера изменился при проверке')
    return old, new, key, port


def deploy():
    old, new, key, port = preflight()
    original_caps = ('animated-sign-v1', 'voice-notes-v1')
    changed = news_created = restart_attempted = False
    try:
        replace_file(DROPIN, new)
        changed = True
        create_news_dropin()
        news_created = True
        command('systemctl', 'daemon-reload')
        restart_attempted = True
        command('systemctl', 'restart', UNIT)
        if prop('ActiveState') != 'active' or not health(key, port, (*original_caps, 'news-story-v1')):
            raise DeploymentError('Новый воркер не подтвердил health')
    except Exception:
        try:
            if changed:
                replace_file(DROPIN, old)
            if news_created:
                if safe_read(NEWS) != NEWS_CONTENT:
                    raise DeploymentError('Новостной drop-in изменился во время отката')
                NEWS.unlink()
            command('systemctl', 'daemon-reload')
            if restart_attempted:
                command('systemctl', 'restart', UNIT)
                if prop('ActiveState') != 'active' or not health(key, port, original_caps):
                    raise DeploymentError('Откат не подтверждён')
            print('Восстановлена прежняя конфигурация; прежний health подтверждён.' if restart_attempted
                  else 'Конфигурация восстановлена; перезапуск не начинался.')
        except Exception:
            print('ВНИМАНИЕ: автоматический откат не подтверждён. Проверьте службу вручную.')
        raise DeploymentError('Новое развёртывание не подтверждено') from None
    print('Воркер запущен: health подтверждает кружки, голос и news-story-v1.')
    print('Резервная копия:', BACKUP)


def main():
    if os.geteuid() != 0:
        print('Запускать только через sudo.')
        return 1
    if sys.argv[1:] not in ([], ['--check']):
        print('Используйте без параметров для переключения или --check для проверки.')
        return 1
    try:
        if sys.argv[1:] == ['--check']:
            preflight()
            print('Проверка пройдена; служба не изменялась и не перезапускалась.')
        else:
            deploy()
    except Exception as exc:
        # Never print arbitrary exception text from systemd, network, or environment.
        print('Переключение не подтверждено:', str(exc) if isinstance(exc, DeploymentError) else type(exc).__name__)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

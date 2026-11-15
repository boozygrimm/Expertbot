"""Switch the known InstaBox worker to a tested checkout, with rollback.

Run as root only during a coordinated quiet period: the old worker has no
atomic drain API, so an idle check cannot exclude a new request immediately
after the check. Never print secrets or modify the TTS configuration.
"""
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

if __package__:
    from .deploy_sign_worker import replace_file
else:
    from deploy_sign_worker import replace_file

UNIT = 'bender-video.service'
DROPIN = Path('/etc/systemd/system/bender-video.service.d/smoke.conf')
OLD = '/data/video-prototype/code-sign-prod-e1af119'
TARGET = Path('/data/video-prototype/code-voice-prod-be74202')
BACKUP = DROPIN.with_name('smoke.conf.before-voice-be74202')
LOCK = Path('/run/lock/bender-voice-deploy.lock')
HEALTH = 'http://127.0.0.1:8088/health'


class DeploymentError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def command(*args):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, timeout=30).stdout.strip()


def property_value(name):
    return command('systemctl', 'show', UNIT, '-p', name, '--value')


def active_pid():
    if property_value('ActiveState') != 'active' or property_value('SubState') != 'running':
        raise DeploymentError('Служба не запущена')
    try:
        pid = int(property_value('MainPID'))
    except ValueError:
        raise DeploymentError('Не удалось определить процесс службы') from None
    if pid <= 0:
        raise DeploymentError('Не удалось определить процесс службы')
    return pid


def worker_key(pid):
    env = Path(f'/proc/{pid}/environ').read_bytes()
    for entry in env.split(b'\0'):
        name, sep, value = entry.partition(b'=')
        if sep and name == b'VIDEO_WORKER_KEY' and len(value) >= 24:
            return value.decode('utf-8')
    raise DeploymentError('Не удалось прочитать ключ работающего воркера')


def assert_idle(pid):
    group = property_value('ControlGroup')
    if not group.startswith('/') or '..' in Path(group).parts:
        raise DeploymentError('Не удалось определить группу процессов службы')
    procs = Path('/sys/fs/cgroup') / group.lstrip('/') / 'cgroup.procs'
    try:
        processes = {int(line) for line in procs.read_text().splitlines()}
    except (OSError, ValueError):
        raise DeploymentError('Не удалось проверить группу процессов службы') from None
    if processes != {pid}:
        raise DeploymentError('Служба занята или её состояние изменилось; переключение отменено')
    # Also refuse known pipeline processes outside this service (manual tests).
    result = subprocess.run(['pgrep', '-f', '[/]video_prototype/pipeline.py'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    if result.returncode == 0:
        raise DeploymentError('Найден активный pipeline; переключение отменено')
    if result.returncode != 1:
        raise DeploymentError('Не удалось проверить процессы pipeline')
    if active_pid() != pid:
        raise DeploymentError('Служба изменилась во время проверки')


def health_ok(key, *, voice, wait=15):
    deadline = time.monotonic() + wait
    opener = urllib.request.build_opener(NoRedirect())
    while time.monotonic() < deadline:
        request = urllib.request.Request(HEALTH, headers={'Authorization': 'Bearer ' + key})
        try:
            with opener.open(request, timeout=2) as response:
                result = json.loads(response.read(65537))
            capabilities = result.get('capabilities')
            if (result.get('status') == 'ok' and isinstance(capabilities, list)
                    and 'animated-sign-v1' in capabilities
                    and (not voice or 'voice-notes-v1' in capabilities)):
                return True
        except (urllib.error.URLError, OSError, TimeoutError, ValueError, TypeError, AttributeError):
            pass
        time.sleep(.5)
    return False


def verify_checkout(target, expected_commit):
    if target != TARGET or target.is_symlink() or not (target / 'video_prototype/worker.py').is_file():
        raise DeploymentError('Не найден ожидаемый каталог нового воркера')
    if len(expected_commit) != 40 or any(c not in '0123456789abcdef' for c in expected_commit):
        raise DeploymentError('Некорректный commit')
    git = ('git', '-c', 'safe.directory=' + str(target), '-C', str(target))
    if command(*git, 'rev-parse', 'HEAD') != expected_commit:
        raise DeploymentError('Commit каталога не совпадает с ожидаемым')
    if command(*git, 'status', '--porcelain', '--untracked-files=all'):
        raise DeploymentError('В каталоге кода есть непроверенные изменения')


def deploy(expected_commit):
    target = TARGET
    verify_checkout(target, expected_commit)
    if DROPIN.is_symlink() or not DROPIN.is_file() or BACKUP.exists() or BACKUP.is_symlink():
        raise DeploymentError('Drop-in недоступен или резервная копия уже существует')
    previous = DROPIN.read_text(encoding='utf-8')
    if OLD not in previous or str(target) in previous:
        raise DeploymentError('Конфигурация воркера отличается от ожидаемой')
    pid = active_pid()
    key = worker_key(pid)
    if not health_ok(key, voice=False):
        raise DeploymentError('Текущий воркер не прошёл проверку health')
    assert_idle(pid)
    shutil.copy2(DROPIN, BACKUP)
    print('Резервная копия конфигурации создана.', flush=True)
    changed = False
    restart_started = False
    try:
        # Recheck after taking the backup. No atomic drain is available on the
        # old worker; users must still keep the studio quiet during this step.
        assert_idle(pid)
        replace_file(DROPIN, previous.replace(OLD, str(target)))
        changed = True
        assert_idle(pid)
        command('systemctl', 'daemon-reload')
        assert_idle(pid)
        restart_started = True
        command('systemctl', 'restart', UNIT)
        active_pid()
        if not health_ok(key, voice=True):
            raise DeploymentError('Новый воркер не подтвердил обе возможности')
    except Exception as exc:
        if changed:
            try:
                replace_file(DROPIN, previous)
                command('systemctl', 'daemon-reload')
                if restart_started:
                    command('systemctl', 'restart', UNIT)
                    active_pid()
                    if not health_ok(key, voice=False):
                        raise DeploymentError('Проверка старого воркера не прошла')
                    print('Прежний воркер восстановлен и ответил на health.', flush=True)
                else:
                    print('Конфигурация восстановлена; служба не перезапускалась.', flush=True)
            except Exception:
                print('Откат не подтверждён. Резервная копия сохранена; проверь службу вручную.', flush=True)
        raise DeploymentError('Переключение не подтверждено') from exc
    print('Новый воркер активен: health подтвердил кружки и голосовые.', flush=True)
    print('Резервная копия:', BACKUP, flush=True)


def main():
    if len(sys.argv) != 2:
        print('Укажи полный commit подготовленного каталога.', file=sys.stderr)
        return 2
    try:
        with LOCK.open('a') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            deploy(sys.argv[1])
    except DeploymentError as exc:
        # DeploymentError messages are fixed strings and never contain secrets.
        print(f'Обновление не завершено: {exc}', file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        # Never echo command output, URLs, environment or secret contents.
        print(f'Обновление не завершено: {type(exc).__name__}. Проверь состояние службы.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

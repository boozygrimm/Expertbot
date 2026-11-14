"""Switch the known InstaBox worker drop-in to a tested version, with rollback.

Run on InstaBox as root, never on Render. Does not change TTS configuration.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

UNIT = 'bender-video.service'
DROPIN = Path('/etc/systemd/system/bender-video.service.d/smoke.conf')
ENV_FILE = Path('/data/video-prototype/.env')
OLD = '/data/video-prototype/code-gemini-ad21b23'


def systemctl(*args):
    subprocess.run(['systemctl', *args], check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=30)


def read_worker_key(path):
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line.startswith('export '):
            line = line[7:].lstrip()
        name, sep, value = line.partition('=')
        if sep and name.strip() == 'VIDEO_WORKER_KEY':
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            if len(value) >= 24:
                return value
    raise ValueError('Не удалось прочитать ключ воркера из сохранённого файла')


def check_health(key):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        request = urllib.request.Request('http://127.0.0.1:8088/health',
                                         headers={'Authorization': 'Bearer ' + key})
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                result = json.load(response)
            if result.get('status') == 'ok' and 'animated-sign-v1' in result.get('capabilities', []):
                return True
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError):
            pass
        time.sleep(.5)
    return False


def replace_file(path, content):
    """Write a full same-directory replacement and retain file ownership/mode."""
    info = path.stat()
    fd, name = tempfile.mkstemp(prefix='.sign-worker-', dir=path.parent)
    try:
        os.fchmod(fd, info.st_mode & 0o777)
        os.fchown(fd, info.st_uid, info.st_gid)
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            fd = -1
            out.write(content)
        os.replace(name, path)
    finally:
        if fd >= 0:
            os.close(fd)
        if os.path.exists(name):
            os.unlink(name)


def deploy(target, *, dropin=DROPIN, env_file=ENV_FILE, target_prefix='/data/video-prototype/code-sign-prod-'):
    target = str(Path(target).resolve())
    if not target.startswith(target_prefix) or not (Path(target) / 'video_prototype/worker.py').is_file():
        raise ValueError('Новый каталог кода отсутствует или имеет неподходящий путь')
    if dropin.is_symlink() or not dropin.is_file():
        raise ValueError('Не найден ожидаемый drop-in службы')
    previous = dropin.read_text(encoding='utf-8')
    if OLD not in previous:
        raise ValueError('В drop-in не найден ожидаемый путь прежнего воркера; изменения не внесены')
    key = read_worker_key(env_file)
    backup = dropin.with_name(dropin.name + '.before-sign')
    if backup.exists():
        raise ValueError('Резервная копия before-sign уже существует; изменения не внесены')
    systemctl('is-active', '--quiet', UNIT)
    shutil.copy2(dropin, backup)
    modified = False
    try:
        replace_file(dropin, previous.replace(OLD, target))
        modified = True
        systemctl('daemon-reload')
        systemctl('restart', UNIT)
        systemctl('is-active', '--quiet', UNIT)
        if not check_health(key):
            raise RuntimeError('Новый воркер не подтвердил доступность таблички')
    except BaseException:
        if modified:
            try:
                replace_file(dropin, previous)
                systemctl('daemon-reload')
                systemctl('restart', UNIT)
                systemctl('is-active', '--quiet', UNIT)
                print('При ошибке восстановлен прежний drop-in и служба перезапущена.', flush=True)
            except Exception:
                print('Автоматический откат не подтвердился; резервная копия drop-in сохранена.', flush=True)
        raise
    print('Воркер обновлён, health подтвердил поддержку таблички.', flush=True)
    print(f'Резервная копия: {backup}', flush=True)


def main():
    if len(sys.argv) != 2:
        print('Нужен один путь к новому каталогу кода', file=sys.stderr)
        return 2
    try:
        deploy(sys.argv[1])
    except (ValueError, OSError, subprocess.SubprocessError, RuntimeError) as exc:
        print(f'Обновление не завершено: {type(exc).__name__}. Проверь сохранённую копию и состояние службы.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

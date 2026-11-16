"""Fetch verified trial files and rerender the saved InstaBox speech."""
import argparse
import hashlib
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import urllib.request

REF = '14d80ed3e5fef1116688dc93628d4754d979d94a'
URL = f'https://codeload.github.com/boozygrimm/Expertbot/tar.gz/{REF}'
PYTHON = Path('/data/video-prototype/.venv/bin/python')
FILES = {
    'news_prototype/studio_mockup_soft.png': '8ad7c9af29fa3d144d204a5984b6a71fee5dc003c056ed2c5240b8e545ceabc0',
    'news_prototype/bender_soft_closed.png': '1e96e781df1094d5940b2f00373b8139c1b7d8186e5991071b7f72c8a46ec87c',
    'news_prototype/bender_blink_reference.png': 'cbbcea025f7a23977de59faaf3baf68e7405ca3d4e1331ef42917de67587710f',
    'news_prototype/bender_trial.py': 'b1f55c25e23b23156b02073efb0cd99255f20bd29ef5fa150c05d05e7a26fabc',
    'news_prototype/run_soft_teeth_trial.py': 'b40a88e2658ce13119be5e9f9c8f3666015876514593abf41f9fdb47bd3ed5f5',
    'news_prototype/run_bender_trial.py': '74fcc4dd2115d0347980366879ca705fce34bf0bc10f37d13043d2a2443253d6',
    'news_prototype/send_press_trial.py': '69af20f5cd360784952a0e622a723cb0eb63b0ee5417ffd9d5e621bc3d1c6608',
    'video_prototype/gemini_tts.py': 'e0540cecebd141648068736e6ca1e8e9fe7badc502083afdf4be88da4b966b9b',
}


def unpack_checked(archive: bytes, root: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
        for relative, expected in FILES.items():
            member = tar.getmember(f'Expertbot-{REF}/{relative}')
            if not member.isfile() or member.size > 6_000_000:
                raise ValueError('Неожиданный файл в архиве')
            source = tar.extractfile(member)
            if source is None:
                raise ValueError('Не удалось извлечь файл')
            data = source.read()
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError('Файл не прошёл проверку')
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Проверить архив без запуска рендера и отправки')
    args = parser.parse_args()
    if not args.check and not PYTHON.is_file():
        raise SystemExit('Не найден Python студии на InstaBox; ничего не отправлено.')
    print('Скачиваю и проверяю файлы отдельной пробы...', flush=True)
    try:
        request = urllib.request.Request(URL, headers={'User-Agent': 'news-soft-teeth-trial'})
        with urllib.request.urlopen(request, timeout=90) as response:
            archive = response.read(60_000_001)
        if len(archive) > 60_000_000:
            raise ValueError('Архив слишком большой')
        with tempfile.TemporaryDirectory(prefix='news-soft-src-') as directory:
            root = Path(directory)
            unpack_checked(archive, root)
            print('Файлы проверены.', flush=True)
            if not args.check:
                subprocess.run([str(PYTHON), '-m', 'news_prototype.run_soft_teeth_trial'], cwd=root, check=True)
    except (OSError, ValueError, KeyError, tarfile.TarError, subprocess.CalledProcessError):
        raise SystemExit('Не удалось подготовить или выполнить пробу. Если отправка уже начиналась, сначала проверьте чат.') from None


if __name__ == '__main__':
    main()

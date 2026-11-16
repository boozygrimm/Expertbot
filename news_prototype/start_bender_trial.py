"""Fetch integrity-checked trial files and run the isolated InstaBox demo."""
import argparse
import hashlib
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import urllib.request

REF = 'd5a88e8b5cb04c064a4c149c700d5816355702bf'
URL = f'https://codeload.github.com/boozygrimm/Expertbot/tar.gz/{REF}'
PYTHON = Path('/data/video-prototype/.venv/bin/python')
FILES = {
    'news_prototype/studio_mockup_corrected.png': 'cd9722ad9dc5178f911b1a86c74e0c2a56e661a55e5e75ef6a154faf563edc6b',
    'news_prototype/bender_closed_reference.png': '0ef01c1dce2a6595b455574b37acfc34ca10ff295281b9d8f40225de6e8543fd',
    'news_prototype/bender_blink_reference.png': 'cbbcea025f7a23977de59faaf3baf68e7405ca3d4e1331ef42917de67587710f',
    'news_prototype/bender_trial.py': 'b24da8799b730aaaf6029e087439142a9058fec1f4a028802c304366ceab6a4f',
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
    parser.add_argument('--check', action='store_true', help='Проверить архив, не запуская озвучку или отправку')
    args = parser.parse_args()
    if not args.check and not PYTHON.is_file():
        raise SystemExit('Не найден Python студии на InstaBox; ничего не отправлено.')
    print('Скачиваю и проверяю файлы отдельной пробы...', flush=True)
    try:
        request = urllib.request.Request(URL, headers={'User-Agent': 'news-bender-trial'})
        with urllib.request.urlopen(request, timeout=90) as response:
            archive = response.read(60_000_001)
        if len(archive) > 60_000_000:
            raise ValueError('Архив слишком большой')
        with tempfile.TemporaryDirectory(prefix='news-bender-src-') as directory:
            root = Path(directory)
            unpack_checked(archive, root)
            print('Файлы проверены.', flush=True)
            if not args.check:
                subprocess.run([str(PYTHON), '-m', 'news_prototype.run_bender_trial'], cwd=root, check=True)
    except (OSError, ValueError, KeyError, tarfile.TarError, subprocess.CalledProcessError):
        raise SystemExit('Не удалось подготовить или выполнить пробу. Если отправка уже начиналась, сначала проверьте чат.') from None


if __name__ == '__main__':
    main()

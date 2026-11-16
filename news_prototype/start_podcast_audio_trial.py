"""Fetch verified trial files and run the isolated two-voice audio demo."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile
import urllib.request

REF = '8739863d129f92bc3b2cb806ac5b60114ab99473'
PYTHON = Path('/data/video-prototype/.venv/bin/python')
FILES = {
    'news_prototype/podcast_audio_trial.py': '891ba2743f9aaf847dcb59c62af41b67a37de0f35d6e78ad48cd4973e97ea09f',
    'news_prototype/run_bender_trial.py': '74fcc4dd2115d0347980366879ca705fce34bf0bc10f37d13043d2a2443253d6',
    'news_prototype/send_press_trial.py': '69af20f5cd360784952a0e622a723cb0eb63b0ee5417ffd9d5e621bc3d1c6608',
    'video_prototype/gemini_tts.py': 'e0540cecebd141648068736e6ca1e8e9fe7badc502083afdf4be88da4b966b9b',
}


def download_files(root: Path) -> None:
    for relative, expected in FILES.items():
        url = f'https://api.github.com/repos/boozygrimm/Expertbot/contents/{relative}?ref={REF}'
        request = urllib.request.Request(url, headers={
            'Accept': 'application/vnd.github.raw+json', 'User-Agent': 'podcast-audio-trial'})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(1_000_001)
        if len(data) > 1_000_000 or hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('Файл не прошёл проверку')
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Проверить файлы без синтеза и отправки')
    args = parser.parse_args()
    if not args.check and not PYTHON.is_file():
        raise SystemExit('Не найден Python студии на InstaBox; ничего не отправлено.')
    print('Скачиваю и проверяю файлы отдельной пробы диалога...', flush=True)
    try:
        with tempfile.TemporaryDirectory(prefix='news-dialogue-src-') as directory:
            root = Path(directory)
            download_files(root)
            print('Файлы проверены.', flush=True)
            if not args.check:
                subprocess.run([str(PYTHON), '-m', 'news_prototype.podcast_audio_trial'], cwd=root, check=True)
    except (OSError, ValueError, subprocess.CalledProcessError):
        raise SystemExit('Не удалось выполнить пробу. Если отправка уже начиналась, сначала проверьте чат.') from None


if __name__ == '__main__':
    main()

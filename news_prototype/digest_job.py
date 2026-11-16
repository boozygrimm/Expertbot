"""Worker child: assemble previously completed story videos, never generate speech."""
import argparse
import json
from pathlib import Path
from news_prototype import digest_video, story_pipeline
from video_prototype.news_jobs import validate_digest


def run(request: Path, root: Path, output: Path, ffmpeg: str = 'ffmpeg') -> None:
    if request.is_symlink() or request.stat().st_size > 100_000:
        raise ValueError('Неверная задача')
    data = json.loads(request.read_text(encoding='utf-8'))
    _, ids = validate_digest({'request_id': request.parent.name, **data})
    paths = []
    for item in ids:
        folder = root / item
        work = folder / 'work'
        status = work / 'status.json'
        if folder.is_symlink() or work.is_symlink() or status.is_symlink() or status.stat().st_size > 4096:
            raise ValueError('Недоступный сюжет')
        if json.loads(status.read_text(encoding='utf-8')).get('stage') != 'ready_for_review':
            raise ValueError('Сюжет ещё не готов')
        paths.append(work / 'video.mp4')
    output.mkdir(mode=0o700)
    story_pipeline._state(output, 'rendering')
    digest_video.assemble(paths, output / 'video.mp4', ffmpeg)
    story_pipeline._state(output, 'ready_for_review')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    run(args.request, args.root, args.output_dir, args.ffmpeg)


if __name__ == '__main__':
    main()

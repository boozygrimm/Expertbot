"""Worker child for one approved versioned bulletin; never sends or publishes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from news_prototype import bulletin_pipeline
from news_prototype.run_bender_trial import load_keys
from video_prototype.news_jobs import MAX_BULLETIN_BYTES, validate_bulletin


def run(request: Path, output: Path, ffmpeg: str, keys: list[str]) -> Path:
    if request.is_symlink() or request.stat().st_size > MAX_BULLETIN_BYTES:
        raise ValueError('Неверная задача вестника')
    data = json.loads(request.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or set(data) != {'bulletin'}:
        raise ValueError('Неверная задача вестника')
    _, bulletin = validate_bulletin({'request_id': request.parent.name, **data})
    return bulletin_pipeline.run(bulletin, output, ffmpeg, keys)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    run(args.request, args.output_dir, args.ffmpeg, load_keys())


if __name__ == '__main__':
    main()

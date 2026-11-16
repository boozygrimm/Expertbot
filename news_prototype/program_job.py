"""One approved owner program: render stories in order, then assemble one MP4.

This worker child never sends or publishes any intermediate video. A reservation
for the entire program exists before this process starts; failures are retained
and never automatically replayed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from news_prototype import digest_video, story_pipeline
from news_prototype.run_bender_trial import load_keys
from video_prototype.news_jobs import MAX_PROGRAM_BYTES, validate_program


def run(request: Path, output: Path, ffmpeg: str, keys: list[str]) -> Path:
    if request.is_symlink() or request.stat().st_size > MAX_PROGRAM_BYTES:
        raise ValueError('Неверная задача выпуска')
    data = json.loads(request.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or set(data) != {'scripts'}:
        raise ValueError('Неверная задача выпуска')
    _, scripts = validate_program({'request_id': request.parent.name, **data})
    output.mkdir(mode=0o700)
    story_pipeline._state(output, 'rendering')
    try:
        videos = [story_pipeline.run(script, output / f'story-{i}', ffmpeg, keys)
                  for i, script in enumerate(scripts, 1)]
        digest_video.assemble(videos, output / 'video.mp4', ffmpeg)
        story_pipeline._state(output, 'ready_for_review')
        return output / 'video.mp4'
    except Exception:
        story_pipeline._state(output, 'render_failed')
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    run(args.request, args.output_dir, args.ffmpeg, load_keys())


if __name__ == '__main__':
    main()

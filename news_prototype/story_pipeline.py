"""Run one explicit local script through voice and widescreen rendering.

This is not an unattended worker: no queue, network news search or delivery is
started here. A fresh directory is required, failures retain intermediate data,
and rerunning never overwrites an existing job or repeats it automatically.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
from news_prototype import story_audio, story_video
from news_prototype.run_bender_trial import load_keys


def _state(folder: Path, stage: str) -> None:
    tmp = folder / 'status.json.tmp'
    tmp.write_text(json.dumps({'stage': stage}, ensure_ascii=False), encoding='utf-8')
    tmp.replace(folder / 'status.json')


def run(script: dict, folder: Path, ffmpeg: str, keys: list[str]) -> Path:
    # The exclusive mkdir in prepare prevents a second invocation from
    # overwriting a previous result; failed folders are retained for inspection.
    story_audio.prepare(script, folder, ffmpeg, keys)
    _state(folder, 'audio_ready')
    temporary, output = folder / 'video.partial.mp4', folder / 'video.mp4'
    try:
        if 'image_b64' in script:
            from news_prototype.news_image_codec import decode_thumbnail
            image = decode_thumbnail(script['image_b64'])
            fd = os.open(folder / 'news-image.jpg', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as target:
                target.write(image)
        _state(folder, 'rendering')
        story_video.render(folder, temporary, ffmpeg, image_regions=script.get('image_regions'))
        subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i', str(temporary),
                        '-f', 'null', '-'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        temporary.replace(output)
        _state(folder, 'ready_for_review')
    except Exception:
        _state(folder, 'render_failed')
        raise
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--script', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    if args.script.stat().st_size > 600_000:
        raise SystemExit('Сценарий слишком большой')
    script = json.loads(args.script.read_text(encoding='utf-8'))
    print('Используется квота Gemini. Готовый ролик не публикуется автоматически.', flush=True)
    path = run(script, args.output_dir, args.ffmpeg, load_keys())
    print(f'Сохранён сюжет для проверки: {path}', flush=True)


if __name__ == '__main__': main()

"""Combined isolated studio video with a sample scrolling desk ticker."""
from __future__ import annotations

import argparse
from pathlib import Path
from news_prototype import combined_trial, desk_ticker


def render(folder: Path, output: Path, ffmpeg: str) -> None:
    assets = desk_ticker.prepare()
    def add_ticker(frame, timestamp):
        # Keep text still while the screen powers up, then scroll smoothly.
        return desk_ticker.apply(frame, assets, max(0.0, timestamp - 1.48))
    combined_trial.render(folder, output, ffmpeg, screen_overlay=add_ticker)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dialogue-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    render(args.dialogue_dir, args.output, args.ffmpeg)
    print(f'Проба бегущей строки готова: {args.output}', flush=True)


if __name__ == '__main__':
    main()

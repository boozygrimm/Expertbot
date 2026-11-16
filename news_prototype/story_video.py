"""Offline renderer for an explicitly prepared two-speaker news story.

Input is a local manifest plus previously synthesized 24 kHz mono PCM turns.
No network, TTS, editorial generation, publication or bot integration here.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from news_prototype import combined_trial, desk_ticker, podcast_video_trial

FILENAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\.wav$')
SPEAKERS = {'Бендер', 'Киберсенсей'}


def load_story(folder: Path):
    manifest = folder / 'story.json'
    if manifest.stat().st_size > 100_000:
        raise ValueError('Слишком большой манифест')
    data = json.loads(manifest.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('turns'), list) or not data['turns']:
        raise ValueError('Нет реплик')
    # Bounds individual input complexity, not the duration of a finished digest.
    if len(data['turns']) > 100:
        raise ValueError('Слишком много реплик в одном сюжете')
    turns, used = [], set()
    for item in data['turns']:
        if not isinstance(item, dict):
            raise ValueError('Некорректная реплика')
        speaker, name = item.get('speaker'), item.get('audio')
        if not isinstance(speaker, str) or speaker not in SPEAKERS or not isinstance(name, str) or not FILENAME.fullmatch(name) or name == 'dialogue.wav' or name in used:
            raise ValueError('Неверное имя аудио или ведущий')
        # Never follow an input symlink outside the designated story directory.
        path = folder / name
        if path.is_symlink() or not path.is_file() or path.resolve().parent != folder.resolve():
            raise ValueError('Не найден файл реплики')
        turns.append((path, speaker)); used.add(name)
    audio = folder / 'dialogue.wav'
    if audio.is_symlink() or not audio.is_file():
        raise ValueError('Не найден общий диалог')
    ticker = data.get('ticker')
    if ticker is not None and (not isinstance(ticker, str) or not ticker.strip() or len(ticker) > 200):
        raise ValueError('Некорректный текст строки')
    return audio, turns, ticker


def render(folder: Path, output: Path, ffmpeg: str) -> None:
    audio, turns, text = load_story(folder)
    inputs = [audio, folder / 'story.json', *(path for path, _ in turns)]
    if output.resolve() in {path.resolve() for path in inputs}:
        raise ValueError('Файл результата совпадает с исходным материалом')
    timeline = podcast_video_trial.dialogue_timeline_from_parts(audio, turns)
    overlay = None
    if text:
        assets = desk_ticker.prepare(text)
        overlay = lambda frame, timestamp: desk_ticker.apply(frame, assets, max(0.0, timestamp - 1.48))
    combined_trial.render(folder, output, ffmpeg, timeline=timeline, screen_overlay=overlay)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--story-dir', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    render(args.story_dir, args.output, args.ffmpeg)
    print(f'Сюжет сохранён: {args.output}')


if __name__ == '__main__':
    main()

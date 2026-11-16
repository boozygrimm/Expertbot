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
    ticker = desk_ticker.prepare(text) if text else None
    image_file = folder / 'news-image.jpg'
    if output.resolve() == image_file.resolve():
        raise ValueError('Файл результата совпадает с изображением')
    panel = views = limits = None
    if image_file.exists() or image_file.is_symlink():
        if image_file.is_symlink() or not image_file.is_file() or image_file.stat().st_size > 180_000:
            raise ValueError('Недопустимое изображение новости')
        import base64
        from news_prototype import news_picture
        views = news_picture.prepare_views(base64.b64encode(image_file.read_bytes()).decode('ascii'))
        panel = views[0]
        duration = len(timeline[1]) / podcast_video_trial.bender.FPS
        limits = news_picture.window(duration, combined_trial.INTRO_FRAMES / podcast_video_trial.bender.FPS)

    def overlay(frame, timestamp):
        if panel is not None:
            frame = news_picture.apply(frame, panel, timestamp, limits, views=views)
        if ticker is not None:
            # Move the ticker below the portrait cutaway while it is visible.
            # This keeps the entire image unobstructed instead of cropping it.
            offset = round(56 * news_picture.opacity_at(timestamp, limits)) if panel is not None else 0
            frame = desk_ticker.apply(frame, ticker, max(0.0, timestamp - 1.48), offset_y=offset)
        return frame
    combined_trial.render(folder, output, ffmpeg, timeline=timeline,
                          screen_overlay=overlay if ticker is not None or panel is not None else None)


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

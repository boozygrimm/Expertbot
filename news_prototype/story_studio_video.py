"""Render a single approved story in the bulletin studio, with existing audio.

No provider calls or delivery. Audio is verified against the manifest before
rendering; image views are built from the owner's already reviewed thumbnail.
"""
from __future__ import annotations

import base64
from pathlib import Path
from news_prototype import (bulletin_layered_video, bulletin_timing, combined_trial,
                            news_picture, podcast_video_trial, story_video)


def render(folder: Path, output: Path, ffmpeg: str, *, image_regions: list | None = None) -> None:
    audio, turns, ticker = story_video.load_story(folder)
    image_file = folder / 'news-image.jpg'
    inputs = [audio, folder / 'story.json', image_file, *(path for path, _ in turns)]
    if output.resolve() in {path.resolve() for path in inputs}:
        raise ValueError('Файл результата совпадает с исходным материалом')
    timeline = podcast_video_trial.dialogue_timeline_from_parts(audio, turns)
    rate, samples = podcast_video_trial.read_wav(audio)
    intro = combined_trial.INTRO_FRAMES / combined_trial.bender.FPS
    duration = len(samples) / rate
    window = bulletin_timing.SectionWindow(1, intro, intro + duration)
    section = {'id': 1, 'ticker': ticker}
    views = limits = None
    if image_file.exists() or image_file.is_symlink():
        if image_file.is_symlink() or not image_file.is_file() or image_file.stat().st_size > 180_000:
            raise ValueError('Недопустимое изображение новости')
        value = base64.b64encode(image_file.read_bytes()).decode('ascii')
        views = news_picture.prepare_views(value, image_regions)
        limits = news_picture.window(duration, intro)
    elif image_regions:
        raise ValueError('Области без изображения')
    bulletin_layered_video.render(output, ffmpeg, sections=[section], windows=[window],
                                  timeline=timeline, picture_views=views, picture_window=limits)

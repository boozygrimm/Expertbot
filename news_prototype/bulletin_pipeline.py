"""Isolated experimental renderer for one compact, approved bulletin.

Used by the experimental versioned worker endpoint after owner confirmation.
Requires a new output directory and never retries speech, truncates a story or
publishes automatically.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from news_prototype import (bulletin_contract, bulletin_layered_video, bulletin_timing,
                            podcast_video_trial,
                            story_audio, story_pipeline, story_video)


def run(data: dict, folder: Path, ffmpeg: str, keys: list[str]) -> Path:
    bulletin = bulletin_contract.validate_bulletin(data)
    sections = bulletin['sections']
    turns = [turn for section in sections for turn in section['turns']]
    # Reuse existing TTS and audio timeline as one uninterrupted speech track.
    story_audio.prepare({'turns': turns, 'ticker': sections[0]['ticker']}, folder, ffmpeg, keys)
    story_pipeline._state(folder, 'audio_ready')
    section_ids = [section['id'] for section in sections for _ in section['turns']]
    parts = [(section_id, folder / f'{index}-normalized.wav') for index, section_id in enumerate(section_ids, 1)]
    temporary, output = folder / 'video.partial.mp4', folder / 'video.mp4'
    try:
        windows, _ = bulletin_timing.section_windows(parts)
        audio, timed_parts, _ = story_video.load_story(folder)
        timeline = podcast_video_trial.dialogue_timeline_from_parts(audio, timed_parts)
        story_pipeline._state(folder, 'rendering')
        bulletin_layered_video.render(temporary, ffmpeg, sections=sections, windows=windows, timeline=timeline)
        subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i', str(temporary),
                        '-f', 'null', '-'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        temporary.replace(output)
        story_pipeline._state(folder, 'ready_for_review')
    except bulletin_timing.BulletinTooLong:
        story_pipeline._state(folder, 'audio_too_long')
        raise
    except Exception:
        story_pipeline._state(folder, 'render_failed')
        raise
    return output

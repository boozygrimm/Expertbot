"""Isolated experimental renderer for one compact, approved bulletin.

Used by the experimental versioned worker endpoint after owner confirmation.
Requires a new output directory; never restarts a job, truncates a story or
publishes automatically. Individual TTS requests use the existing key rotation.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from news_prototype import (bulletin_contract, bulletin_dialogue_audio, bulletin_full_video_trial,
                            bulletin_layered_video, bulletin_timing, story_pipeline)


def run(data: dict, folder: Path, ffmpeg: str, keys: list[str]) -> Path:
    bulletin = bulletin_contract.validate_bulletin(data)
    sections = bulletin['sections']
    # One joint Algenib/Puck recording per news item, as in the owner-approved
    # full-bulletin trial. Reuse the same key rotation; no per-turn TTS calls.
    # prepare creates an exclusive work directory and retains partial audio on
    # failure; this path never resumes or silently repeats a previous job.
    try:
        audio, parts = bulletin_dialogue_audio.prepare(bulletin, folder, ffmpeg, keys)
    except bulletin_timing.BulletinTooLong:
        # The exclusive work folder now contains every completed recording,
        # but no video or subsequent TTS request is started.
        story_pipeline._state(folder, 'audio_too_long')
        raise
    story_pipeline._state(folder, 'audio_ready')
    temporary, output = folder / 'video.partial.mp4', folder / 'video.mp4'
    try:
        if [number for number, _ in parts] != [section['id'] for section in sections]:
            raise ValueError('Порядок записей и новостей не совпадает')
        windows, _ = bulletin_timing.section_windows(parts)
        # Full-section WAVs do not contain reliable speaker boundaries. The
        # approved preview's heuristic animates only unambiguous estimates;
        # speech and both presenters remain intact even without a split.
        timeline, _ = bulletin_full_video_trial.build_timeline(audio, parts, sections)
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

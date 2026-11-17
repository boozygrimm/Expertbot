"""Calculate bulletin section timing and enforce a real five-minute limit.

Durations are supplied from already synthesized WAV files, not a text guess.
Never clip audio, drop a story or automatically synthesize a second time.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from news_prototype import combined_trial, podcast_audio_trial, podcast_video_trial

MAX_SECONDS = 300


class BulletinTooLong(ValueError):
    pass


@dataclass(frozen=True)
class SectionWindow:
    section_id: int
    start: float
    end: float


def section_windows(parts: list[tuple[int, Path]], *, max_seconds: float = MAX_SECONDS) -> tuple[list[SectionWindow], float]:
    """Derive visual windows from actual PCM lengths, gaps and intro/tail frames."""
    if not parts or not isinstance(max_seconds, (int, float)) or isinstance(max_seconds, bool) or not math.isfinite(max_seconds) or max_seconds <= 0:
        raise ValueError('Неверная длительность')
    rate = podcast_audio_trial.RATE
    gap = round(rate * podcast_audio_trial.GAP_MS / 1000)
    cursor = 0
    ranges = []
    seen = set()
    for index, (section_id, path) in enumerate(parts):
        if not isinstance(section_id, int) or isinstance(section_id, bool) or section_id <= 0:
            raise ValueError('Неверный раздел')
        if not ranges or ranges[-1][0] != section_id:
            if section_id in seen:
                raise ValueError('Реплики новости должны идти подряд')
            seen.add(section_id)
            ranges.append([section_id, cursor, cursor])
        sample_rate, samples = podcast_video_trial.read_wav(Path(path))
        if sample_rate != rate:
            raise ValueError('Неверная частота аудио')
        cursor += len(samples)
        ranges[-1][2] = cursor
        if index < len(parts) - 1:
            cursor += gap
    fps = combined_trial.bender.FPS
    # The renderer rounds dialogue to whole frames and adds a fixed intro/tail.
    dialogue_frames = math.ceil(cursor * fps / rate)
    total = (combined_trial.INTRO_FRAMES + dialogue_frames + combined_trial.TAIL_FRAMES) / fps
    if total > max_seconds:
        raise BulletinTooLong(f'Озвучка с заставками занимает {total:.1f} с; максимум {max_seconds:.0f} с')
    intro = combined_trial.INTRO_FRAMES / fps
    windows = [SectionWindow(number, intro + start / rate, intro + end / rate) for number, start, end in ranges]
    return windows, total

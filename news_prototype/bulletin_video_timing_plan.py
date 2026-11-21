"""Experimental, offline timing plan for video inserts in a compact bulletin.

This does not choose an editorial pause, decode/mix media, render, authorize or
send anything. Explicit cuts must be reviewed in context: digital silence alone
cannot prove that a sentence or thought is complete. Coordinates use the saved
mono 24 kHz dialogue; the original PCM is retained exactly, including silence.
"""
from __future__ import annotations

from array import array
from dataclasses import dataclass

from news_prototype import bender_trial, bulletin_limits, bulletin_timing, combined_trial, podcast_audio_trial

FPS = bender_trial.FPS
RATE = podcast_audio_trial.RATE
SAMPLES_PER_FRAME = RATE // FPS
# Require 125 ms of digital silence on each side of the proposed cut.
GUARD_FRAMES = 3


@dataclass(frozen=True)
class Section:
    section_id: int
    start_sample: int
    end_sample: int  # exclusive, both coordinates in original dialogue


@dataclass(frozen=True)
class Insert:
    section_id: int
    cut_frame: int  # frame in original dialogue; original PCM is not removed
    clip_frames: int
    before_frames: int = 18
    hold_frames: int = 2
    after_frames: int = 18


@dataclass(frozen=True)
class Event:
    section_id: int
    clip_start: int  # absolute frame in final video including intro
    clip_end: int    # exclusive; after this, the last image is held
    return_frame: int
    resume_frame: int
    source_elapsed_at_clip: int  # frames since source was started for this section


@dataclass(frozen=True)
class Plan:
    events: tuple[Event, ...]
    total_frames: int
    # PCM with silent reservations; still needs source audio and remarks mixed.
    # This is a preview/plan, not a ready-to-render or approved audio track.
    samples: array


def _int(value: object, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def plan(samples: array, sections: list[Section], inserts: list[Insert]) -> Plan:
    """Reserve insertion time while preserving every original PCM sample.

    At most one insertion per section. Source playback is assumed to start at
    the first whole frame at or after the original section starts and continue
    during the lead-in and fullscreen interval. The future renderer must use a
    single decoder/timeline and separately align the source audio; this planner
    alone cannot guarantee audiovisual sync or verify file/usage rights.
    """
    if (RATE % FPS or not isinstance(samples, array) or samples.typecode != 'h'
            or not samples or len(samples) > RATE * bulletin_timing.MAX_SECONDS):
        raise ValueError('Неверный диалог')
    # One section is allowed on purpose: a reviewed single-story render needs
    # the same checked-silence rule, and this planner only reserves time inside
    # audio that was already synthesized.
    if (not isinstance(sections, list) or not 1 <= len(sections) <= bulletin_limits.MAX_STORIES
            or not isinstance(inserts, list) or len(inserts) > len(sections)):
        raise ValueError('Неверный состав выпуска')
    by_id = {}
    previous_end = 0
    for section in sections:
        if (not isinstance(section, Section) or not bulletin_limits.number(section.section_id)
                or not _int(section.start_sample) or not _int(section.end_sample)
                or section.start_sample < previous_end or section.end_sample <= section.start_sample
                or section.end_sample > len(samples) or section.section_id in by_id):
            raise ValueError('Неверные границы новостей')
        by_id[section.section_id] = section
        previous_end = section.end_sample
    checked = []
    seen = set()
    for entry in inserts:
        if (not isinstance(entry, Insert) or not bulletin_limits.number(entry.section_id)
                or entry.section_id not in by_id or entry.section_id in seen
                or not _int(entry.cut_frame, 1) or not _int(entry.clip_frames, 1)
                or not _int(entry.before_frames) or not _int(entry.hold_frames)
                or not _int(entry.after_frames)):
            raise ValueError('Неверные параметры вставки')
        section = by_id[entry.section_id]
        cut = entry.cut_frame * SAMPLES_PER_FRAME
        guard = GUARD_FRAMES * SAMPLES_PER_FRAME
        if (cut - guard < section.start_sample or cut + guard > section.end_sample
                or any(samples[cut - guard:cut + guard])):
            raise ValueError('В выбранном месте нет достаточной тишины')
        seen.add(entry.section_id)
        checked.append(entry)
    checked.sort(key=lambda entry: entry.cut_frame)
    base_frames = (len(samples) + SAMPLES_PER_FRAME - 1) // SAMPLES_PER_FRAME
    extra = sum(entry.before_frames + entry.clip_frames + entry.hold_frames + entry.after_frames for entry in checked)
    total = combined_trial.INTRO_FRAMES + base_frames + extra + combined_trial.TAIL_FRAMES
    if total > bulletin_timing.MAX_SECONDS * FPS:
        raise bulletin_timing.BulletinTooLong(f'Выпуск со вставками занимает {total / FPS:.1f} с; максимум 300 с')
    result = array('h')
    events = []
    previous_sample = previous_frames = 0
    for entry in checked:
        section = by_id[entry.section_id]
        cut = entry.cut_frame * SAMPLES_PER_FRAME
        result.extend(samples[previous_sample:cut])
        added = entry.before_frames + entry.clip_frames + entry.hold_frames + entry.after_frames
        result.extend(array('h', [0]) * (added * SAMPLES_PER_FRAME))
        start = combined_trial.INTRO_FRAMES + entry.cut_frame + previous_frames + entry.before_frames
        source_start = (section.start_sample + SAMPLES_PER_FRAME - 1) // SAMPLES_PER_FRAME
        events.append(Event(entry.section_id, start, start + entry.clip_frames,
                            start + entry.clip_frames + entry.hold_frames,
                            start + entry.clip_frames + entry.hold_frames + entry.after_frames,
                            entry.cut_frame - source_start + entry.before_frames))
        previous_sample = cut
        previous_frames += added
    result.extend(samples[previous_sample:])
    return Plan(tuple(events), total, result)

"""Insert one reviewed video fragment into a single-story studio render.

The fragment is already stored and checked by the worker (MP4, bounded length,
decodable video and audio). This module only reserves a silent pause in the
story's own dialogue and hands the exact audio and picture timelines to the
existing bulletin renderer, which draws the bulletin studio scene.

No download, TTS, queue, deletion or publication happens here. Speaker
animation for a whole-story recording is estimated, not verified diarization,
so the cut is accepted only inside digital silence.
"""
from __future__ import annotations

import base64
import shutil
import subprocess
import tempfile
from array import array
from pathlib import Path

from news_prototype import (bender_trial, bulletin_layered_video, bulletin_timing,
                            bulletin_video_timing_plan as timing, combined_trial, news_picture,
                            podcast_audio_trial, podcast_video_trial, story_video,
                            telegram_public_source)
from news_prototype.bulletin_video_pipeline import _mix, _source_audio

STORY_ID = 1
# Ten seconds on screen, matching the bulletin's default fragment length.
DEFAULT_CLIP_FRAMES = 240


def cut_frame(samples: array, turns: list[tuple[Path, str]]) -> int:
    """Return a cut position, in frames, inside the pause between two turns.

    Turns are recorded separately, so the gap between them is real digital
    silence and a complete sentence boundary. The recording is never altered
    and no heuristic guess about a sentence end is made; the fragment is simply
    placed where speech already stopped.
    """
    per_frame = timing.SAMPLES_PER_FRAME
    guard = timing.GUARD_FRAMES * per_frame
    gap = round(podcast_video_trial.RATE * podcast_audio_trial.GAP_MS / 1000)
    if not 0 < gap or gap - 2 * guard < per_frame:
        raise ValueError('Слишком короткая пауза между репликами')
    cursor = 0
    for path, _ in turns[:-1]:
        cursor += len(podcast_video_trial.read_wav(path)[1])
        # Prefer the pause exactly in the middle of the gap, whole frames only.
        candidate = (cursor + gap // 2) // per_frame * per_frame
        if (candidate - guard >= cursor and candidate + guard <= cursor + gap
                and not any(samples[candidate - guard:candidate + guard])):
            # The planner counts frames, not samples.
            return candidate // per_frame
        cursor += gap
    raise ValueError('В сюжете нет паузы между репликами для видеовставки')


def render(folder: Path, output: Path, ffmpeg: str, source: Path, *,
           clip_frames: int = DEFAULT_CLIP_FRAMES, image_regions: list | None = None) -> None:
    """Render one story and mix one already checked fragment into its silence."""
    if type(clip_frames) is not int or clip_frames <= 0:
        raise ValueError('Неверная длительность вставки')
    audio, turns, ticker = story_video.load_story(folder)
    output, source = Path(output), Path(source)
    if (source.is_symlink() or not source.is_file() or output.exists() or output.is_symlink()
            or output.resolve() in {audio.resolve(), source.resolve()}):
        raise ValueError('Недопустимый источник или результат')
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        raise ValueError('Для видеовставки нужен ffprobe')
    duration, _, _, has_audio = telegram_public_source.inspect(source, ffprobe, ffmpeg)
    timeline = podcast_video_trial.dialogue_timeline_from_parts(audio, turns)
    rate, samples = podcast_video_trial.read_wav(audio)
    entry = timing.Insert(STORY_ID, cut_frame(samples, turns), clip_frames)
    plan = timing.plan(samples, [timing.Section(STORY_ID, 0, len(samples))], [entry])
    if combined_trial.INTRO_FRAMES + len(timeline[1]) + combined_trial.TAIL_FRAMES > plan.total_frames:
        raise ValueError('Аудио и кадровый таймлайн не совпадают')
    event = plan.events[0]
    mixed = _mix(plan, entry, _source_audio(source, ffmpeg, duration, has_audio), duration, None,
                 event=event, samples=plan.samples)
    clicks = ((event.clip_start - combined_trial.INTRO_FRAMES) * timing.SAMPLES_PER_FRAME - round(.09 * timing.RATE),
              (event.return_frame - combined_trial.INTRO_FRAMES) * timing.SAMPLES_PER_FRAME)
    extra = entry.before_frames + entry.clip_frames + entry.hold_frames + entry.after_frames
    active = timeline[1][:entry.cut_frame] + [None] * extra + timeline[1][entry.cut_frame:]
    states = timeline[2][:entry.cut_frame] + [False] * extra + timeline[2][entry.cut_frame:]
    # The window must span every turn, not just the first one: passing the first
    # turn's file made the story end while the dialogue went on, so the screen
    # froze on one frame for the rest of the story. All turns belong to one
    # section, so they are passed in order.
    windows, _ = bulletin_timing.section_windows([(STORY_ID, path) for path, _ in turns])
    window = bulletin_timing.SectionWindow(STORY_ID, windows[0].start, windows[0].end + extra / timing.FPS)
    with tempfile.TemporaryDirectory(prefix='story-video-', dir=output.parent) as temporary:
        temporary = Path(temporary)
        dialogue, partial = temporary / 'dialogue.wav', temporary / 'video.mp4'
        bender_trial.write_wav(dialogue, mixed, timing.RATE)
        views, limits = _picture(folder, samples, rate, image_regions)
        bulletin_layered_video.render(
            partial, ffmpeg, sections=[{'id': STORY_ID, 'ticker': ticker}], windows=[window],
            timeline=(dialogue, active, states), picture_views=views, picture_window=limits,
            video={'section_id': STORY_ID, 'path': source, 'start': combined_trial.INTRO_FRAMES,
                   'fullscreen': (event.clip_start, event.return_frame)},
            extra_clicks=tuple(clicks))
        subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i', str(partial), '-f', 'null', '-'],
                       check=True, timeout=240, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       env=telegram_public_source.media_env())
        partial.replace(output)


def _picture(folder: Path, samples: array, rate: int, image_regions: list | None):
    """Reuse the reviewed picture exactly as the story studio does."""
    image_file = Path(folder) / 'news-image.jpg'
    if not (image_file.exists() or image_file.is_symlink()):
        if image_regions:
            raise ValueError('Области без изображения')
        return None, None
    if image_file.is_symlink() or not image_file.is_file() or image_file.stat().st_size > 180_000:
        raise ValueError('Недопустимое изображение новости')
    value = base64.b64encode(image_file.read_bytes()).decode('ascii')
    views = news_picture.prepare_views(value, image_regions)
    intro = combined_trial.INTRO_FRAMES / combined_trial.bender.FPS
    return views, news_picture.window(len(samples) / rate, intro)

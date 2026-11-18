"""Isolated experimental renderer for one compact, approved bulletin.

Used by the experimental versioned worker endpoint after owner confirmation.
Requires a new output directory; never restarts a job, truncates a story or
publishes automatically. Individual TTS requests use the existing key rotation.
"""
from __future__ import annotations

import hashlib
import math
import shutil
import subprocess
import tempfile
from contextlib import ExitStack
from pathlib import Path
from news_prototype import (bulletin_contract, bulletin_dialogue_audio, bulletin_full_video_trial,
                            bulletin_layered_video, bulletin_timing, bulletin_video_pipeline,
                            combined_trial, podcast_video_trial, story_pipeline, telegram_public_source)


def _acquire_reviewed_video(video: dict, parent: Path, ffmpeg: str) -> tuple[Path, str]:
    """Redownload only the exact reviewed public post; reject changed bytes.

    No signed URL or local path comes from the request. Media is downloaded
    before TTS, privately, into a fresh directory, and never silently replaced
    by different material. An approved hash is not itself proof of rights.
    """
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        raise ValueError('Для видео нужен ffprobe')
    post = telegram_public_source.fetch_post(video['post_url'])
    if (post.url != video['post_url'] or not post.caption_complete or post.video_status != 'available'
            or hashlib.sha256(post.caption.encode('utf-8')).hexdigest() != video['caption_sha256']):
        raise ValueError('Пост изменился после проверки')
    media = telegram_public_source.download_video(post, parent / 'source', ffmpeg=ffmpeg, ffprobe=ffprobe)
    if media.sha256 != video['video_sha256']:
        raise ValueError('Видео изменилось после проверки')
    return media.path, ffprobe


def run(data: dict, folder: Path, ffmpeg: str, keys: list[str]) -> Path:
    bulletin = bulletin_contract.validate_bulletin(data)
    folder = Path(folder)
    if folder.exists() or folder.is_symlink():
        raise FileExistsError('Папка выпуска уже существует')
    with ExitStack() as stack:
        sources = []
        for index, video in enumerate(bulletin_contract.selected_videos(bulletin)):
            private = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix=f'bulletin-source-{index}-', dir=folder.parent)))
            sources.append(_acquire_reviewed_video(video, private, ffmpeg))
        return _run(bulletin, folder, ffmpeg, keys, sources)



def _run(bulletin: dict, folder: Path, ffmpeg: str, keys: list[str], sources: list[tuple[Path, str]]) -> Path:
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
        videos = bulletin_contract.selected_videos(bulletin)
        specs = []
        intro = combined_trial.INTRO_FRAMES / combined_trial.bender.FPS
        for video, source in zip(videos, sources):
            remarks = None
            clip_frames = 240
            if video.get('remarks'):
                folder_name = 'video-remarks' if len(videos) == 1 else f'video-remarks-{video["section_id"]}'
                remarks = bulletin_dialogue_audio.prepare_remarks(video['remarks'], folder / folder_name, ffmpeg, keys)
                rate, samples = podcast_video_trial.read_wav(remarks)
                clip_frames = max(240, math.ceil((len(samples) / rate + 1.5) * combined_trial.bender.FPS))
            target = next(w for w in windows if w.section_id == video['section_id'])
            if 'cut_after' in video:
                target_index = next(i for i, s in enumerate(sections, 1) if s['id'] == target.section_id)
                rate, before = podcast_video_trial.read_wav(folder / f'{target_index}-before-normalized.wav')
                _, after = podcast_video_trial.read_wav(folder / f'{target_index}-after-normalized.wav')
                _, full = podcast_video_trial.read_wav(parts[target_index - 1][1])
                silence_end = len(before) + rate // 2
                if (full[:len(before)] != before or full[silence_end:] != after or not after
                        or any(full[len(before):silence_end])):
                    raise ValueError('Граница записей не совпадает со сценарием')
                cut_time = target.start - intro + len(before) / rate + .25
            else:
                cut_time = target.end - intro - .25
            specs.append({'section_id': target.section_id, 'cut_frame': round(cut_time * combined_trial.bender.FPS),
                          'clip_frames': clip_frames, 'remarks': remarks, 'source': source[0]})
        story_pipeline._state(folder, 'rendering')
        if not specs:
            timeline, _ = bulletin_full_video_trial.build_timeline(audio, parts, sections)
            bulletin_layered_video.render(temporary, ffmpeg, sections=sections, windows=windows, timeline=timeline)
        elif len(specs) == 1:
            spec = specs[0]
            bulletin_video_pipeline.render(bulletin, audio, parts, spec['source'], temporary, ffmpeg, sources[0][1],
                section_id=spec['section_id'], cut_frame=spec['cut_frame'], clip_frames=spec['clip_frames'], remarks=spec['remarks'])
        else:
            bulletin_video_pipeline.render_many(bulletin, audio, parts, temporary, ffmpeg, sources[0][1], specs=specs)
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

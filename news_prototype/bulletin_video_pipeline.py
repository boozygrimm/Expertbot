"""Build a real bulletin with one local video using already prepared dialogue.

No network, TTS, approval, queue, bot or publication. A caller must supply the
reviewed media, WAV files and an editorially checked pause. The regular image-
only worker remains unchanged. Output is created only if the entire release,
including the insert and intro/tail, fits in five minutes without cutting speech.
"""
from __future__ import annotations

from array import array
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from news_prototype import (bender_trial, bulletin_contract, bulletin_full_video_trial, bulletin_layered_video,
    bulletin_timing, bulletin_video_timing_plan as timing, combined_trial,
    podcast_video_trial, telegram_public_source, telegram_story_overlay_trial)


def _source_audio(path: Path, ffmpeg: str, duration: float, has_audio: bool) -> array:
    if not has_audio:
        return array('h')
    result = subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-protocol_whitelist', 'file,pipe',
        '-format_whitelist', 'mov,mp4,m4a,3gp,3g2,mj2', '-i', str(path), '-map', '0:a:0',
        '-af', 'aresample=async=1:first_pts=0', '-t', str(duration), '-ac', '1', '-ar', str(timing.RATE),
        '-f', 's16le', 'pipe:1'], check=True, timeout=240, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env=telegram_public_source.media_env())
    if len(result.stdout) % 2 or len(result.stdout) > 2 * timing.RATE * (telegram_public_source.MAX_SECONDS + 1):
        raise ValueError('Неверная аудиодорожка источника')
    data = array('h'); data.frombytes(result.stdout)
    if sys.byteorder != 'little':
        data.byteswap()
    return data


def _mix(plan: timing.Plan, entry: timing.Insert, source_audio: array, source_duration: float,
         remarks: array | None) -> array:
    """Fill only the reserved clip, never touching the original dialogue."""
    event = plan.events[0]
    if not math.isfinite(source_duration) or not 0 < source_duration <= telegram_public_source.MAX_SECONDS:
        raise ValueError('Неверная длительность источника')
    period = round(source_duration * timing.RATE)
    clip_length = entry.clip_frames * timing.SAMPLES_PER_FRAME
    if not period or len(source_audio) > timing.RATE * (telegram_public_source.MAX_SECONDS + 1):
        raise ValueError('Неверная аудиодорожка источника')
    cycle = array('h', [0]) * period
    size = min(period, len(source_audio))
    cycle[:size] = source_audio[:size]
    position = (event.source_elapsed_at_clip * timing.SAMPLES_PER_FRAME) % period
    clip = array('h')
    while len(clip) < clip_length:
        length = min(period - position, clip_length - len(clip))
        clip.extend(cycle[position:position + length])
        position = 0
    if remarks is not None:
        # Exactly the approved trial's one-second lead-in; never truncate a word.
        if len(remarks) + timing.RATE + timing.RATE // 2 > clip_length:
            raise ValueError('Реплики не помещаются во вставку')
        clip = telegram_story_overlay_trial.mix_with_ducking(clip, remarks, timing.RATE)
    mixed = array('h', plan.samples)
    start = (event.clip_start - combined_trial.INTRO_FRAMES) * timing.SAMPLES_PER_FRAME
    if start < 0 or any(mixed[start:start + len(clip)]):
        raise ValueError('Вставка пересекает исходный диалог')
    mixed[start:start + len(clip)] = clip
    return mixed


def render(data: dict, dialogue: Path, parts: list[tuple], source: Path,
           output: Path, ffmpeg: str, ffprobe: str, *, section_id: int, cut_frame: int,
           clip_frames: int = 240, remarks: Path | None = None) -> Path:
    """Render one video-bearing section inside an otherwise normal bulletin.

    ``parts`` is either one (section ID, WAV) per complete two-speaker news
    item or one (section ID, WAV, speaker) per turn. It is checked against the
    script and the saved joined dialogue. Speaker animation for whole-story
    audio is estimated, not verified diarization. ``cut_frame`` is relative to
    the original dialogue at 24 fps, inside digital silence. Remarks, if
    supplied, are an existing mono 24 kHz WAV, not newly generated.
    """
    sections = bulletin_contract.validate_bulletin(data)['sections']
    expected = [(s['id'], turn['speaker']) for s in sections for turn in s['turns']]
    if not isinstance(parts, list) or not parts or any(not isinstance(p, tuple) for p in parts):
        raise ValueError('Реплики и новости не совпадают')
    whole_stories = all(len(p) == 2 for p in parts)
    if whole_stories:
        if [p[0] for p in parts] != [s['id'] for s in sections]:
            raise ValueError('Реплики и новости не совпадают')
    elif any(len(p) != 3 for p in parts) or [(p[0], p[2]) for p in parts] != expected:
        raise ValueError('Реплики и новости не совпадают')
    dialogue, source, output = Path(dialogue), Path(source), Path(output)
    paths = [Path(p[1]) for p in parts]
    inputs = [dialogue, source, *paths] + ([Path(remarks)] if remarks is not None else [])
    if (any(p.is_symlink() or not p.is_file() for p in inputs) or output.exists() or output.is_symlink()
            or output.resolve() in {p.resolve() for p in inputs} or not output.parent.is_dir()):
        raise ValueError('Недопустимый исходный файл или результат')
    if not 0 < source.stat().st_size <= telegram_public_source.MAX_VIDEO:
        raise ValueError('Неверный размер исходного видео')
    if whole_stories:
        original, _ = bulletin_full_video_trial.build_timeline(dialogue, [(p[0], Path(p[1])) for p in parts], sections)
    else:
        original = podcast_video_trial.dialogue_timeline_from_parts(dialogue, [(Path(p[1]), p[2]) for p in parts])
    _, speech = podcast_video_trial.read_wav(dialogue)
    windows, _ = bulletin_timing.section_windows([(p[0], Path(p[1])) for p in parts])
    intro = combined_trial.INTRO_FRAMES / timing.FPS
    section_ranges = [timing.Section(w.section_id, round((w.start - intro) * timing.RATE),
                                     round((w.end - intro) * timing.RATE)) for w in windows]
    entry = timing.Insert(section_id, cut_frame, clip_frames)
    plan = timing.plan(speech, section_ranges, [entry])
    event = plan.events[0]
    # Check budget and silence before invoking either decoder or renderer.
    duration, _, _, has_audio = telegram_public_source.inspect(source, ffprobe, ffmpeg)
    source_audio = _source_audio(source, ffmpeg, duration, has_audio)
    comments = podcast_video_trial.read_wav(Path(remarks))[1] if remarks is not None else None
    mixed = _mix(plan, entry, source_audio, duration, comments)
    extra = entry.before_frames + entry.clip_frames + entry.hold_frames + entry.after_frames
    target_index = next(i for i, w in enumerate(windows) if w.section_id == section_id)
    shifted = []
    for i, w in enumerate(windows):
        before = extra / timing.FPS if i > target_index else 0
        after = extra / timing.FPS if i >= target_index else 0
        shifted.append(bulletin_timing.SectionWindow(w.section_id, w.start + before, w.end + after))
    active = original[1][:cut_frame] + [None] * extra + original[1][cut_frame:]
    states = original[2][:cut_frame] + [False] * extra + original[2][cut_frame:]
    if combined_trial.INTRO_FRAMES + len(active) + combined_trial.TAIL_FRAMES != plan.total_frames:
        raise ValueError('Аудио и кадровый таймлайн не совпадают')
    # Clicks are placed in reserved silence; write_sound refuses overlap.
    click1 = (event.clip_start - combined_trial.INTRO_FRAMES) * timing.SAMPLES_PER_FRAME - round(.09 * timing.RATE)
    click2 = (event.return_frame - combined_trial.INTRO_FRAMES) * timing.SAMPLES_PER_FRAME
    section_start = section_ranges[target_index].start_sample
    video_start = combined_trial.INTRO_FRAMES + (section_start + timing.SAMPLES_PER_FRAME - 1) // timing.SAMPLES_PER_FRAME
    with tempfile.TemporaryDirectory(prefix='bulletin-video-', dir=output.parent) as tmp:
        tmp = Path(tmp); audio = tmp / 'dialogue.wav'; partial = tmp / 'video.mp4'
        bender_trial.write_wav(audio, mixed, timing.RATE)
        bulletin_layered_video.render(partial, ffmpeg, sections=sections, windows=shifted,
            timeline=(audio, active, states), video={'section_id': section_id, 'path': source,
            'start': video_start, 'fullscreen': (event.clip_start, event.return_frame)}, extra_clicks=(click1, click2))
        subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i', str(partial), '-f', 'null', '-'],
                       check=True, timeout=240, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       env=telegram_public_source.media_env())
        os.link(partial, output)  # atomic no-overwrite, same filesystem
    return output


def main() -> int:
    """Local-only entry point for saved bulletin/audio and one reviewed video."""
    import argparse
    import json
    import shutil
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bulletin', type=Path, required=True, help='Сохранённый bulletin.json')
    parser.add_argument('--audio-dir', type=Path, required=True, help='dialogue.wav и N-normalized.wav')
    parser.add_argument('--source', type=Path, required=True, help='Проверенное локальное видео')
    parser.add_argument('--section', type=int, required=True, help='ID новости из bulletin.json')
    parser.add_argument('--cut-frame', type=int, required=True, help='Место паузы в исходном диалоге, кадры по 24/с')
    parser.add_argument('--clip-frames', type=int, default=240, help='Продолжительность вставки, по умолчанию 10 секунд')
    parser.add_argument('--remarks', type=Path, help='Уже записанные реплики поверх видео (моно WAV 24 кГц)')
    parser.add_argument('--parts-mode', choices=('section', 'turn'), default='section',
                        help='Одна запись на новость или одна запись на реплику')
    parser.add_argument('--output', type=Path, required=True, help='Новый MP4-файл')
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('--ffprobe', default='ffprobe')
    args = parser.parse_args()
    try:
        ffmpeg, ffprobe = shutil.which(args.ffmpeg), shutil.which(args.ffprobe)
        if not ffmpeg or not ffprobe:
            raise ValueError('Нужны ffmpeg и ffprobe')
        if (args.bulletin.is_symlink() or not args.bulletin.is_file()
                or args.bulletin.stat().st_size > 5_100_000
                or args.audio_dir.is_symlink() or not args.audio_dir.is_dir()):
            raise ValueError('Неверный сохранённый выпуск')
        data = bulletin_contract.validate_bulletin(json.loads(args.bulletin.read_text(encoding='utf-8')))
        if args.parts_mode == 'section':
            parts = [(section['id'], args.audio_dir / f'{i}-normalized.wav')
                     for i, section in enumerate(data['sections'], 1)]
        else:
            turns = [(section['id'], turn['speaker']) for section in data['sections'] for turn in section['turns']]
            parts = [(section, args.audio_dir / f'{i}-normalized.wav', speaker)
                     for i, (section, speaker) in enumerate(turns, 1)]
        output = render(data, args.audio_dir / 'dialogue.wav', parts, args.source, args.output,
                        ffmpeg, ffprobe, section_id=args.section, cut_frame=args.cut_frame,
                        clip_frames=args.clip_frames, remarks=args.remarks)
        print(f'Готово: {output}. Проверь видео, звук, место перехода и права; отправка не выполнялась.')
        return 0
    except (ValueError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        # Do not echo subprocess diagnostics or potential source URLs.
        if isinstance(exc, bulletin_timing.BulletinTooLong):
            print('Выпуск с вставкой превышает пять минут; речь не обрезана, видео не создано.')
        else:
            print('Сборка не выполнена: проверь файлы, выбранную паузу и длительность. Отправки и TTS не было.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

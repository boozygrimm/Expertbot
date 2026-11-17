"""Owner-only trial: a brief full-TV video with comments over the source audio.

Inserts one clip into a detected quiet point of an existing dialogue, preserves
all original speech samples, and uses one two-speaker TTS request for the two
new remarks. Silence and estimated sentence boundaries are not transcription;
the owner must review the timing and generated words. No production jobs,
queues, services or databases are modified.
"""
from __future__ import annotations

from array import array
import json
import math
from pathlib import Path
import shutil
import subprocess

from news_prototype import (bender_trial, bulletin_dialogue_audio, bulletin_duo_content_probe,
    bulletin_full_video_trial, bulletin_preflight, bulletin_timing, combined_trial,
    podcast_audio_trial, podcast_video_trial, telegram_story_fullscreen_trial as fullscreen,
    telegram_story_podcast_trial as story)
from video_prototype import gemini_tts

BASE = story.BASE
SOURCE = story.SOURCE
FOLDER = 'video-overlay-1'
FPS = bender_trial.FPS
RATE = podcast_audio_trial.RATE
GAP_FRAMES = 18
HOLD_FRAMES = 2
COMMENT_DELAY = 1.0
COMMENTS = {'turns': [
    {'speaker': 'Бендер', 'text': 'Ну вы гляньте, как это смотрится!'},
    {'speaker': 'Киберсенсей', 'text': 'Да, залипательно.'},
]}


class NoPause(ValueError):
    pass


def choose_pause(dialogue: Path, data: dict) -> int:
    """Pick a substantial quiet interval near an estimated turn boundary.

    This does not prove the pause is between sentences. Never discard or modify
    samples from the original dialogue; stop if no nearby quiet interval exists.
    """
    rate, speech = podcast_video_trial.read_wav(dialogue)
    levels = bender_trial.levels(speech, rate)
    boundaries = story.estimate_boundaries(levels, data['turns'])
    if len(boundaries) < 4:
        raise NoPause('Не удалось определить место вставки')
    target = boundaries[3]
    peak = max(levels, default=0)
    if peak <= 0:
        raise NoPause('Диалог пуст')
    threshold = min(peak * .15, max(35., peak * .035))
    candidates = []
    begin = None
    for n in range(len(levels) + 1):
        quiet = n < len(levels) and levels[n] <= threshold
        if quiet and begin is None:
            begin = n
        elif not quiet and begin is not None:
            if n - begin >= math.ceil(.30 * FPS):
                midpoint = (begin + n) // 2
                if FPS * 2 < midpoint < len(levels) - FPS * 2:
                    candidates.append(midpoint)
            begin = None
    nearby = [p for p in candidates if abs(p - target) <= max(3 * FPS, int(len(levels) * .12))]
    if not nearby:
        raise NoPause('Рядом с предполагаемой сменой реплик нет достаточно тихой паузы')
    return min(nearby, key=lambda p: abs(p - target))


def generate_comments(folder: Path, ffmpeg: str, keys: list[str]) -> Path:
    """One jointly generated two-voice clip using the existing key rotation."""
    folder.mkdir(mode=0o700, exist_ok=False)
    body = bulletin_dialogue_audio.request_body(COMMENTS)
    raw, normalized = folder / 'comments-raw.wav', folder / 'comments.wav'
    def send(key, _unused, timeout):
        return bulletin_dialogue_audio.request_one(key, body, timeout)
    gemini_tts.synthesize('', raw, keys, request_fn=send)
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-i', str(raw), '-ac', '1',
                    '-ar', str(RATE), '-c:a', 'pcm_s16le', str(normalized)],
                   check=True, timeout=120, env=podcast_audio_trial.normalized_env(),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rate, samples = podcast_video_trial.read_wav(normalized)
    if rate != RATE or not any(samples) or not 1.0 <= len(samples) / rate <= 50:
        raise ValueError('Озвучка коротких реплик пустая или подозрительной длины')
    return normalized


def mix_with_ducking(source: array, voice: array, offset: int) -> array:
    """Duck source near detected speech; never attenuate or cut voice samples."""
    if offset < 0 or offset + len(voice) > len(source) or not any(voice):
        raise ValueError('Неверная длина озвучки поверх видео')
    bin_size = RATE // 50  # 20 ms
    energies = []
    for begin in range(0, len(voice), bin_size):
        block = voice[begin:begin + bin_size]
        energies.append(math.sqrt(sum(x * x for x in block) / len(block)))
    peak = max(energies, default=0)
    threshold = max(40., min(350., peak * .05))
    activity = [False] * len(energies)
    for i, level in enumerate(energies):
        if level > threshold:
            for j in range(max(0, i - 8), min(len(energies), i + 21)):
                activity[j] = True
    gain = 1.0
    result = array('h')
    for i, sample in enumerate(source):
        voice_index = i - offset
        # Include lookahead before the first sample if the voice starts
        # immediately, so the background is already ducking at the onset.
        talking = (-8 * bin_size <= voice_index < len(voice)
                   and activity[max(0, voice_index // bin_size)])
        target = .22 if talking else 1.0
        seconds = .15 if target < gain else .35
        gain += (target - gain) / (RATE * seconds)
        spoken = voice[voice_index] if 0 <= voice_index < len(voice) else 0
        background = round(sample * gain)
        # Preserve speech exactly even if source + voice would otherwise clip.
        background = max(-32768 - spoken, min(32767 - spoken, background))
        result.append(spoken + background)
    return result


def assemble(dialogue: Path, source_audio: Path, comments: Path, output: Path,
             *, cut_frame: int, source_duration: float, data: dict, continuous_video: bool = False):
    (old_audio, active, states), _, _ = story.build_timeline(dialogue, data, 0)
    _, speech = podcast_video_trial.read_wav(old_audio)
    _, source = podcast_video_trial.read_wav(source_audio)
    _, remarks = podcast_video_trial.read_wav(comments)
    if not any(source) or not any(remarks) or RATE % FPS:
        raise ValueError('Нет звука источника или коротких реплик')
    per_frame = RATE // FPS
    if not 0 < cut_frame < len(active) or cut_frame * per_frame >= len(speech):
        raise ValueError('Неверное место вставки')
    voice_offset = round(COMMENT_DELAY * RATE)
    clip_frames = max(10 * FPS, math.ceil((voice_offset + len(remarks) + round(.5 * RATE)) / per_frame))
    if clip_frames / FPS > source_duration + .05:
        raise ValueError('Исходное видео слишком короткое для полных реплик')
    start = cut_frame + GAP_FRAMES
    end = start + clip_frames + HOLD_FRAMES
    resume = end + GAP_FRAMES
    total_frames = resume + len(active) - cut_frame
    total = (combined_trial.INTRO_FRAMES + total_frames + combined_trial.TAIL_FRAMES) / FPS
    if total > 300:
        raise bulletin_timing.BulletinTooLong(f'Выпуск с вставкой занимает {total:.1f} с; максимум 300 с')
    clip_samples = clip_frames * per_frame
    if continuous_video:
        # Use the source position at the fullscreen transition, not time zero.
        # The studio video loops at the video's duration. If audio ends first,
        # keep silence until that loop point instead of restarting it early.
        if not math.isfinite(source_duration) or source_duration <= 0:
            raise ValueError('Неверная длительность источника')
        period = round(source_duration * RATE)
        if period <= 0:
            raise ValueError('Неверная длительность источника')
        source_cycle = array('h', [0]) * period
        count = min(len(source), period)
        source_cycle[:count] = source[:count]
        position = (start * per_frame) % period
        source_part = array('h')
        while len(source_part) < clip_samples:
            count = min(period - position, clip_samples - len(source_part))
            source_part.extend(source_cycle[position:position + count])
            position = 0
    else:
        source_part = array('h', [0]) * clip_samples
        count = min(len(source), clip_samples)
        source_part[:count] = source[:count]
    mixed = mix_with_ducking(source_part, remarks, voice_offset)
    result = array('h', [0]) * (total_frames * per_frame)
    cut = cut_frame * per_frame
    result[:cut] = speech[:cut]
    result[start * per_frame:start * per_frame + len(mixed)] = mixed
    remaining = speech[cut:]
    result[resume * per_frame:resume * per_frame + len(remaining)] = remaining
    clicks = (start * per_frame - round(.09 * RATE), end * per_frame)
    if any(any(result[p:p + round(.055 * RATE)]) for p in clicks):
        raise ValueError('Щелчок пересекается с речью или звуком видео')
    bender_trial.write_wav(output, result, RATE)
    active = active[:cut_frame] + [None] * (resume - cut_frame) + active[cut_frame:]
    states = states[:cut_frame] + [False] * (resume - cut_frame) + states[cut_frame:]
    return (output, active, states), (start, end), clicks, total, clip_frames / FPS


def run(base: Path, source: Path, ffmpeg: str, ffprobe: str) -> Path:
    if base.is_symlink() or not base.is_dir():
        raise ValueError('Нет папки предыдущего подкаста')
    snapshot, audio_dir = base / 'script.json', base / 'audio'
    dialogue = audio_dir / 'dialogue.wav'
    if (snapshot.is_symlink() or not snapshot.is_file() or snapshot.stat().st_size > 100_000
            or audio_dir.is_symlink() or not audio_dir.is_dir()
            or dialogue.is_symlink() or not dialogue.is_file()):
        raise ValueError('Не найдены сохранённый сценарий или диалог')
    data = json.loads(snapshot.read_text(encoding='utf-8'))
    if data != story.script():
        raise ValueError('Сохранённый сценарий не совпадает с исходным')
    if source.is_symlink() or not source.is_file() or source.stat().st_size != story.SOURCE_SIZE:
        raise ValueError('Не найдено исходное видео поста')
    if bulletin_preflight.check(ffmpeg):
        raise ValueError('Не пройдена предварительная проверка')
    duration = story.source_duration(source, ffprobe)
    if duration < 10 or not fullscreen.has_audio(source, ffprobe):
        raise fullscreen.NoSourceAudio('В видео нет нужной аудиодорожки или оно короче десяти секунд')
    cut_frame = choose_pause(dialogue, data)
    env = bulletin_duo_content_probe.read_worker_env()
    keys = bulletin_duo_content_probe.worker_keys(env)
    if not keys:
        raise ValueError('Не найдены ключи TTS')
    folder = base / FOLDER
    folder.mkdir(mode=0o700, exist_ok=False)
    (folder / 'comments.json').write_text(json.dumps(COMMENTS, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Тихая пауза на {cut_frame / FPS:.2f} с исходного диалога; её положение приблизительное.', flush=True)
    clip_audio = folder / 'source-audio.wav'
    fullscreen.extract_audio(source, clip_audio, ffmpeg)
    print('Озвучиваю две новые реплики вместе с существующей ротацией ключей.', flush=True)
    comments = generate_comments(folder / 'comments', ffmpeg, keys)
    timeline, window, clicks, total, clip_seconds = assemble(dialogue, clip_audio, comments,
        folder / 'timeline.wav', cut_frame=cut_frame, source_duration=duration, data=data)
    print(f'Вставка: {clip_seconds:.1f} с, полный ролик: {total:.1f} с. Создаю видео.', flush=True)
    partial, output = folder / 'video.partial.mp4', folder / 'video.mp4'
    story.render(source, partial, ffmpeg, timeline, fullscreen=window, clicks=clicks)
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i', str(partial), '-f', 'null', '-'],
                   check=True, timeout=240, env=podcast_audio_trial.normalized_env(),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    partial.replace(output)
    print(f'Ролик сохранён: {output}', flush=True)
    print(bulletin_full_video_trial.send_video_once(output, folder, env,
          caption='Проба: видео и его звук с репликами ведущих поверх, затем возвращение в студию.'), flush=True)
    return output


def main() -> int:
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        print('Не найден ffmpeg или ffprobe; ничего не запущено.')
        return 1
    try:
        run(BASE, SOURCE, ffmpeg, ffprobe)
        return 0
    except FileExistsError:
        print('Проба уже запускалась. Проверь папку и личку, не повторяй вслепую.')
    except (fullscreen.NoSourceAudio, NoPause, bulletin_timing.BulletinTooLong, gemini_tts.TTSFailure,
            bulletin_duo_content_probe.ProbeError) as exc:
        print('Проба остановлена:', str(exc), 'Не повторяй вслепую; сохрани вывод.')
    except Exception:
        print('Проба остановлена. Не повторяй вслепую; сохрани вывод для диагностики.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())

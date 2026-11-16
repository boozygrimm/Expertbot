"""One isolated trial: remote, two presenters, fly hatch and framed hedgehog.

Uses an already saved dialogue. The scene is flattened and all motion is a
small compositing approximation, not a rig or a finished news episode.
"""
from __future__ import annotations

import argparse
from array import array
from functools import lru_cache
import math
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import wave

from PIL import Image, ImageDraw, ImageFilter
from news_prototype import bender_trial as bender
from news_prototype import hedgehog_motion as hedgehog
from news_prototype import podcast_video_trial as podcast
from news_prototype import press_trial as press
from news_prototype import sensei_hatch as hatch
from news_prototype import sensei_hatch_trial as hatch_trial
from news_prototype import transition_trial as scene

# Frame-aligned silence ensures speech starts only after the screen is on.
INTRO_FRAMES = 42
TAIL_FRAMES = 45
ON_PRESS = .85


def timing(dialogue_frames: int) -> tuple[float, float]:
    if dialogue_frames <= 0:
        raise ValueError('Пустой диалог')
    end = (INTRO_FRAMES + dialogue_frames) / bender.FPS
    return end + .5, (INTRO_FRAMES + dialogue_frames + TAIL_FRAMES) / bender.FPS


def visibility(t: float, off_press: float) -> float:
    if t < .98:
        return 0.0
    if t < 1.48:
        return scene.smooth((t - .98) / .5)
    if t < off_press + .1:
        return 1.0
    if t < off_press + .63:
        return 1.0 - scene.smooth((t - off_press - .1) / .53)
    return 0.0


def pulse(t: float, presses: tuple[float, ...]) -> float:
    return max((scene.unit(1 - abs(t - p) / .16) for p in presses), default=0.0)


def finger_amount(t: float, presses: tuple[float, ...]) -> float:
    result = 0.0
    for at in presses:
        if at - .30 <= t < at:
            result = max(result, scene.smooth((t - at + .30) / .30))
        elif at <= t < at + .25:
            result = max(result, 1 - scene.smooth((t - at) / .25))
    return result


def display(frame: Image.Image, mask: Image.Image, off: Image.Image, t: float, off_press: float) -> Image.Image:
    level = visibility(t, off_press)
    inner = Image.blend(off, frame, level)
    frame = Image.composite(inner, frame, mask)
    flash = max(scene.unit(1 - abs(t - at) / span)
                for at, span in ((1.06, .11), (off_press + .54, .12)))
    if flash:
        line = Image.new('RGBA', scene.SIZE, (0, 0, 0, 0))
        d = ImageDraw.Draw(line)
        y = scene.point(0, 385)[1]
        d.rounded_rectangle((scene.point(220, 0)[0], y-2, scene.point(1140, 0)[0], y+2),
                            radius=2, fill=(168, 224, 255, round(155 * flash)))
        line.putalpha(Image.composite(line.getchannel('A'), Image.new('L', scene.SIZE), mask))
        frame = Image.alpha_composite(frame.convert('RGBA'), line).convert('RGB')
    p = pulse(t, (ON_PRESS, off_press))
    if p:
        glow = Image.new('RGBA', scene.SIZE, (0, 0, 0, 0))
        d = ImageDraw.Draw(glow)
        x, y = scene.point(1045, 560)
        radius = 4 + round(4 * p)
        d.ellipse((x-radius, y-radius, x+radius, y+radius), fill=(255, 50, 25, round(125*p)))
        glow = glow.filter(ImageFilter.GaussianBlur(3))
        ImageDraw.Draw(glow).ellipse((x-2, y-2, x+2, y+2), fill=(255, 180, 110, round(230*p)))
        frame = Image.alpha_composite(frame.convert('RGBA'), glow).convert('RGB')
    return frame


def write_sound(dialogue: Path, output: Path, dialogue_frames: int) -> None:
    rate, speech = podcast.read_wav(dialogue)
    if rate % bender.FPS:
        raise ValueError('Частота аудио не совпадает с частотой кадров')
    per_frame = rate // bender.FPS
    speech_room = dialogue_frames * per_frame
    if len(speech) > speech_room:
        raise ValueError('Диалог длиннее таймлайна')
    total = (INTRO_FRAMES + dialogue_frames + TAIL_FRAMES) * per_frame
    result = array('h', [0]) * total
    start = INTRO_FRAMES * per_frame
    result[start:start + len(speech)] = speech
    off_press, _ = timing(dialogue_frames)
    rng = random.Random(817)
    # Clicks are placed in silence before and after speech, not over the dialogue.
    for when in (ON_PRESS, off_press):
        for delay, level in ((0.0, 1.0), (.17, .35)):
            offset = round((when + delay) * rate)
            for i in range(round(.11 * rate)):
                t = i / rate
                value = level * (0.29 * rng.uniform(-1, 1) * math.exp(-t*170)
                    + 0.10 * math.sin(2*math.pi*1150*t) * math.exp(-t*48)
                    + 0.12 * math.sin(2*math.pi*190*t) * math.exp(-t*35))
                at = offset + i
                if 0 <= at < total:
                    result[at] = max(-32767, min(32767, result[at] + round(value*32767)))
    if sys.byteorder != 'little':
        result.byteswap()
    with wave.open(str(output), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate)
        wav.writeframes(result.tobytes())


def render(folder: Path, output: Path, ffmpeg: str) -> None:
    audio, active, states = podcast.dialogue_timeline(folder)
    frames = podcast.prepare_frames()
    hatch_assets, hedgehog_assets = hatch.prepare(), hedgehog.prepare()
    window = hatch_trial.event_window(active)
    off_press, _ = timing(len(active))
    base = Image.frombytes('RGB', bender.SIZE, frames[False, False, False])
    mask = scene.screen_mask()
    finger = press.finger_layer(base)
    off = Image.new('RGB', scene.SIZE, (5, 10, 18))
    ImageDraw.Draw(off).ellipse((130, 48, 800, 340), fill=(8, 15, 25))

    @lru_cache(maxsize=25)
    def hatch_overlay(step: int) -> Image.Image:
        return hatch.overlay_at_fraction(hatch_assets, step / 24)

    @lru_cache(maxsize=32)
    def hedgehog_overlay(nod: int, blink: int) -> Image.Image:
        return hedgehog.overlay(hedgehog_assets, nod / 24, blink / 8)

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='combined-trial-') as tmp:
        sound = Path(tmp) / 'sound.wav'
        write_sound(audio, sound, len(active))
        command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
                   '-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size', f'{bender.SIZE[0]}x{bender.SIZE[1]}',
                   '-framerate', str(bender.FPS), '-i', 'pipe:0', '-i', str(sound),
                   '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
                   '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(output)]
        process = subprocess.Popen(command, stdin=subprocess.PIPE)
        try:
            assert process.stdin is not None
            for index in range(INTRO_FRAMES + len(active) + TAIL_FRAMES):
                t = index / bender.FPS
                i = index - INTRO_FRAMES
                if 0 <= i < len(active):
                    local_t = i / bender.FPS
                    speaker, state = active[i], states[i]
                    data = frames[speaker == 'Бендер' and state, bender.is_blink(local_t),
                                  speaker == 'Киберсенсей' and state]
                    frame = Image.frombytes('RGB', bender.SIZE, data).convert('RGBA')
                    amount = hatch.openness(local_t, *window) if window else 0.0
                    step = round(amount * 24)
                    if step:
                        frame = Image.alpha_composite(frame, hatch_overlay(step))
                    nod, blink = hedgehog.gesture(local_t, len(active) / bender.FPS)
                    ns, bs = round(nod * 24), round(blink * 8)
                    if ns or bs:
                        frame = Image.alpha_composite(frame, hedgehog_overlay(ns, bs))
                    frame = frame.convert('RGB')
                else:
                    frame = base
                frame = display(frame, mask, off, t, off_press)
                frame = press.add_finger(frame, finger, finger_amount(t, (ON_PRESS, off_press)))
                process.stdin.write(frame.tobytes())
            process.stdin.close()
            if process.wait() != 0:
                raise RuntimeError('Не удалось создать видео')
        except BaseException:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            process.wait()
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dialogue-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    render(args.dialogue_dir, args.output, args.ffmpeg)
    print(f'Общая проба готова: {args.output}', flush=True)


if __name__ == '__main__':
    main()

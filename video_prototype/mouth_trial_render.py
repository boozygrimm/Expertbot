"""Isolated experiment: faster loudness response, never changes production renderer."""
import array
import math
from pathlib import Path
import subprocess
import sys
import wave

if __package__:
    from .render import FPS, SIZE, audio_levels, blink_at, make_frames, mouth_states
    from .smoke_layer_render import prepare_frames, frame_with_layers
else:
    from render import FPS, SIZE, audio_levels, blink_at, make_frames, mouth_states
    from smoke_layer_render import prepare_frames, frame_with_layers


def quick_levels(path, fps=FPS):
    """RMS over 25 ms around each video frame, instead of the old 80 ms."""
    with wave.open(str(path), 'rb') as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != 'NONE':
            raise ValueError('Нужен WAV PCM 16 бит, моно')
        rate, count = wav.getframerate(), wav.getnframes()
        if count == 0 or count / rate >= 60:
            raise ValueError('Аудио должно быть непустым и короче 60 секунд')
        samples = array.array('h', wav.readframes(count))
    if sys.byteorder != 'little':
        samples.byteswap()
    radius = max(1, round(rate * .0125))
    result = []
    for i in range(math.ceil(len(samples) * fps / rate)):
        center = int((i + .5) * rate / fps)
        part = samples[max(0, center - radius):min(len(samples), center + radius)]
        result.append(math.sqrt(sum(x*x for x in part) / len(part)) if part else 0)
    return result


def trial_states(levels):
    """Adapt to typical speech level with a small hysteresis; no invented cadence."""
    peak = max(levels, default=0)
    if not peak:
        return [False] * len(levels)
    active = sorted(x for x in levels if x > peak * .06)
    if not active:
        return [False] * len(levels)
    reference = active[round((len(active)-1)*.75)]
    open_at = max(peak * .10, reference * .50)
    close_at = open_at * .75
    states = []
    opened = False
    for level in levels:
        if opened and level < close_at:
            opened = False
        elif not opened and level > open_at:
            opened = True
        states.append(opened)
    # Suppress single-frame flips, except at the edges; avoids rapid visual jitter.
    original = states[:]
    for i in range(1, len(original)-1):
        if original[i-1] == original[i+1] != original[i]:
            states[i] = original[i-1]
    return states


def describe_states(name, states):
    n = len(states)
    runs, run = [], 0
    for state in states:
        if state:
            run += 1
        elif run:
            runs.append(run)
            run = 0
    if run:
        runs.append(run)
    percent = 100 * sum(states) / n if n else 0
    longest = max(runs, default=0) / FPS
    print(f'{name}: рот открыт {percent:.0f}% кадров; самый долгий непрерывный открытый участок {longest:.1f} с', flush=True)


def render(audio, closed, opened, blink, plate, output):
    levels = quick_levels(audio)
    states = trial_states(levels)
    describe_states('Обычный вариант', list(mouth_states(audio_levels(audio))))
    describe_states('Новая проба', states)
    layers = prepare_frames(make_frames(closed, opened, blink), plate)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
           '-s', f'{SIZE}x{SIZE}', '-r', str(FPS), '-i', 'pipe:0', '-i', str(audio),
           '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
           '-c:a', 'aac', '-b:a', '128k', '-shortest', '-movflags', '+faststart', str(output)]
    process = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i, state in enumerate(states):
            process.stdin.write(frame_with_layers(layers[state, blink_at(i)], i / FPS))
    finally:
        process.stdin.close()
        status = process.wait()
    if status:
        raise RuntimeError('FFmpeg не смог создать видео')
    print(f'Пробный кружок с движением рта готов: {output}', flush=True)

"""Produce a silent local animation proof using synthetic timing, not real TTS.

No network, provider keys, bot integration, delivery or publishing. The result
is a design test: mouth movement uses amplitude, not phoneme synchronization.
"""
from __future__ import annotations

import argparse
import array
from io import BytesIO
import math
from pathlib import Path
import subprocess
import tempfile
from PIL import Image, ImageDraw
from news_prototype import (bulletin_animation, bulletin_wide_screen, news_image_codec,
                            podcast_audio_trial, podcast_video_trial, bender_trial)


def _dummy_picture(color: str, accent: str) -> str:
    image = Image.new('RGB', (800, 450), color)
    draw = ImageDraw.Draw(image)
    draw.ellipse((300, 80, 500, 280), fill=accent)
    draw.polygon([(0, 425), (160, 190), (300, 330), (445, 120), (610, 330), (720, 200),
                  (800, 280), (800, 450), (0, 450)], fill=(36, 83, 125))
    out = BytesIO()
    image.save(out, format='PNG')
    return news_image_codec.make_thumbnail(out.getvalue())


def _synthetic_wav(path: Path, seconds: float) -> None:
    """Silent-on-export cadence used to exercise amplitude-driven animation."""
    rate = podcast_audio_trial.RATE
    samples = array.array('h')
    for i in range(round(rate * seconds)):
        t = i / rate
        envelope = .75 if (t % .5) < .28 else 0.0
        samples.append(round(5500 * envelope * math.sin(2 * math.pi * 180 * t)))
    bender_trial.write_wav(path, samples, rate)


is_blink = bulletin_animation.is_blink


def render_preview(output: Path, ffmpeg: str) -> Path:
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('Файл предпросмотра уже существует')
    output.parent.mkdir(parents=True, exist_ok=True)
    faces = bulletin_animation.frames()
    pictures = [_dummy_picture('#1d547a', '#67b7c0'), _dummy_picture('#59496e', '#dcae70')]
    # Compositing and loading assets on every frame would be prohibitively
    # expensive; prepare each screen/face combination once.
    scenes = [{state: bulletin_wide_screen.compose(picture, speaker_layers=layers).tobytes()
               for state, layers in faces.items()} for picture in pictures]
    with tempfile.TemporaryDirectory(prefix='bulletin-motion-') as temp:
        root = Path(temp)
        first, second, joined = root / 'bender.wav', root / 'sensei.wav', root / 'dialogue.wav'
        _synthetic_wav(first, 2.6); _synthetic_wav(second, 2.6)
        podcast_audio_trial.assemble_wav([first, second], joined)
        _, active, states = podcast_video_trial.dialogue_timeline_from_parts(joined,
            [(first, 'Бендер'), (second, 'Киберсенсей')])
        fps = bender_trial.FPS
        command = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                   '-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size', '960x540',
                   '-framerate', str(fps), '-i', 'pipe:0', '-an', '-c:v', 'libx264',
                   '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
                   '-movflags', '+faststart', str(output)]
        process = subprocess.Popen(command, stdin=subprocess.PIPE)
        try:
            assert process.stdin is not None
            first_sensei = active.index('Киберсенсей')
            for index in range(len(active)):
                speaker, state = active[index], states[index]
                phase = int(index >= first_sensei)
                key = (speaker == 'Бендер' and state, is_blink(index / fps),
                       speaker == 'Киберсенсей' and state)
                process.stdin.write(scenes[phase][key])
            process.stdin.close()
            if process.wait() != 0:
                raise RuntimeError('Не удалось создать предпросмотр')
        except BaseException:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            process.wait()
            if output.exists():
                output.unlink()
            raise
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New MP4 path; existing files are not overwritten')
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    print(f'Предпросмотр готов: {render_preview(args.output, args.ffmpeg)}')


if __name__ == '__main__':
    main()

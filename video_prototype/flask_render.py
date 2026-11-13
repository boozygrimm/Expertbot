"""Isolated trial: glowing bubbles inside the purple laboratory flask."""
import argparse
import math
from pathlib import Path
import subprocess
from PIL import Image, ImageChops, ImageDraw, ImageFilter

if __package__:
    from .render import FPS, SIZE, audio_levels, blink_at, make_frames, mouth_states
    from .smoke_layer_render import prepare_frames, frame_with_layers
else:
    from render import FPS, SIZE, audio_levels, blink_at, make_frames, mouth_states
    from smoke_layer_render import prepare_frames, frame_with_layers


def flask_mask():
    # Coordinates in the 480px render: below the surface, within the glass.
    scale = SIZE / 480
    pts = [(169, 113), (228, 113), (228, 122), (224, 132),
           (216, 139), (207, 142), (187, 142), (176, 139), (169, 131), (167, 122)]
    mask = Image.new('L', (SIZE, SIZE))
    ImageDraw.Draw(mask).polygon([(round(x*scale), round(y*scale)) for x,y in pts], fill=255)
    return mask.filter(ImageFilter.GaussianBlur(.5))


FLASK_MASK = flask_mask()


def bubble_layer(seconds):
    """A brief reaction: four small highlights rise, fade and repeat."""
    if not 1.0 <= seconds < 7.0:
        return None
    fade = min(1.0, (seconds - 1.0) / .6, (7.0 - seconds) / .6)
    scale = SIZE / 480
    layer = Image.new('RGBA', (SIZE, SIZE))
    draw = ImageDraw.Draw(layer)
    for x, offset, radius in ((177, 0, 1.7), (187, .47, 2.2),
                               (199, 1.02, 1.8), (212, 1.53, 2.3),
                               (221, .75, 1.5)):
        phase = ((seconds - 1 + offset) / 2.2) % 1
        opacity = round(220 * fade * min(1, phase * 6, (1 - phase) * 7))
        cx = (x + 1.3 * math.sin(phase * math.tau + offset)) * scale
        cy = (139 - 24 * phase) * scale
        r = radius * scale
        draw.ellipse((cx-r, cy-r, cx+r, cy+r), outline=(245, 196, 255, opacity), width=max(1, round(scale)))
        draw.ellipse((cx-r*.45, cy-r*.65, cx, cy-r*.15), fill=(255, 245, 255, opacity))
    layer.putalpha(ImageChops.multiply(layer.getchannel('A'), FLASK_MASK))
    return layer


def frame_with_bubbles(frame, seconds):
    layer = bubble_layer(seconds)
    if layer is None:
        return frame
    image = Image.frombytes('RGB', (SIZE, SIZE), frame).convert('RGBA')
    image.alpha_composite(layer)
    return image.convert('RGB').tobytes()


def render(audio, closed, opened, blink, plate, output, frame_effect=frame_with_bubbles):
    levels = audio_levels(audio)
    frames = prepare_frames(make_frames(closed, opened, blink), plate)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
        '-s', f'{SIZE}x{SIZE}', '-r', str(FPS), '-i', 'pipe:0', '-i', str(audio),
        '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
        '-b:a', '128k', '-shortest', '-movflags', '+faststart', str(output)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        for i, state in enumerate(mouth_states(levels)):
            seconds = i / FPS
            frame = frame_with_layers(frames[state, blink_at(i)], seconds)
            process.stdin.write(frame_effect(frame, seconds))
    finally:
        process.stdin.close()
        status = process.wait()
    if status:
        raise RuntimeError('FFmpeg не смог создать видео')
    print(f'Пробный кружок с колбой готов: {output}', flush=True)


def main():
    parser = argparse.ArgumentParser()
    for name in ('audio', 'closed', 'open', 'blink', 'plate', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    render(args.audio, args.closed, args.open, args.blink, args.plate, args.output)


if __name__ == '__main__':
    main()

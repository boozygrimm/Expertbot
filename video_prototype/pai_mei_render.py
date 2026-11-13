"""One-off Pai Mei cameo, layered over the approved smoke render."""
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

# Hand-traced against the supplied square 640x640 image. No image is stored in Git.
OUTLINE = [(238,35),(251,24),(267,21),(275,16),(303,15),(329,22),(342,35),
 (360,43),(385,53),(398,75),(403,98),(422,105),(420,120),(406,133),
 (415,155),(421,182),(416,210),(405,224),(405,252),(403,278),(410,306),
 (427,319),(448,325),(480,330),(514,340),(548,355),(568,374),(584,398),
 (598,430),(612,470),(615,495),(606,516),(589,540),(561,563),(523,585),
 (480,604),(425,620),(367,632),(300,635),(237,630),(175,618),(128,598),
 (91,575),(72,553),(65,529),(80,506),(99,493),(104,460),(115,427),(127,411),
 (147,399),(172,389),(194,378),(215,371),(227,358),(232,337),(233,306),
 (233,278),(233,251),(221,229),(219,208),(219,181),(223,159),(215,143),
 (210,132),(217,119),(218,94),(223,70),(229,53)]


def prepare_portrait(path):
    with Image.open(path) as source:
        if abs(source.width / source.height - 1) > .02:
            raise ValueError('Нужна квадратная картинка Пай Мея из сообщения 117')
        image = source.convert('RGBA').resize((640, 640), Image.Resampling.LANCZOS)
    mask = Image.new('L', image.size)
    ImageDraw.Draw(mask).polygon(OUTLINE, fill=255)
    image.putalpha(mask.filter(ImageFilter.GaussianBlur(.8)))
    image = image.crop((60, 10, 620, 640))
    image.thumbnail((round(SIZE * .33), round(SIZE * .37)), Image.Resampling.LANCZOS)
    return image


def circle_mask():
    mask = Image.new('L', (SIZE, SIZE))
    inset = SIZE * 39 / 512
    ImageDraw.Draw(mask).ellipse((inset, inset, SIZE-inset, SIZE-inset), fill=255)
    return mask.filter(ImageFilter.GaussianBlur(.8))


CIRCLE = circle_mask()


def cameo_offset(seconds):
    # Smoothly slide in, hold, and slide back out; no head/limb animation.
    if seconds < 2 or seconds >= 6.5:
        return None
    def smooth(x):
        return x * x * (3 - 2 * x)
    amount = min(1., (seconds - 2) / .65, (6.5 - seconds) / .65)
    return smooth(max(0., amount))


def frame_with_cameo(frame, seconds, portrait):
    amount = cameo_offset(seconds)
    if amount is None:
        return frame
    original = Image.frombytes('RGB', (SIZE, SIZE), frame).convert('RGBA')
    layer = Image.new('RGBA', original.size)
    x = round(SIZE * (1.03 - .39 * amount))
    y = round(SIZE * .65)
    layer.alpha_composite(portrait, (x, y))
    layer.putalpha(ImageChops.multiply(layer.getchannel('A'), CIRCLE))
    original.alpha_composite(layer)
    return original.convert('RGB').tobytes()


def render(audio, closed, opened, blink, plate, portrait_path, output):
    levels = audio_levels(audio)
    frames = prepare_frames(make_frames(closed, opened, blink), plate)
    portrait = prepare_portrait(portrait_path)
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
            process.stdin.write(frame_with_cameo(frame, seconds, portrait))
    finally:
        process.stdin.close()
        status = process.wait()
    if status:
        raise RuntimeError('FFmpeg не смог создать видео')
    print(f'Пробный кружок с Пай Меем готов: {output}', flush=True)


def main():
    parser = argparse.ArgumentParser()
    for name in ('audio','closed','open','blink','plate','portrait','output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    render(args.audio, args.closed, args.open, args.blink, args.plate, args.portrait, args.output)


if __name__ == '__main__':
    main()

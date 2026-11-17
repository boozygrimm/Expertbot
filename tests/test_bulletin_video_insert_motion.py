import subprocess

import imageio_ffmpeg
from PIL import Image, ImageChops
from news_prototype import bulletin_video_insert_trial as trial


def test_moving_source_remains_moving_on_screen(tmp_path):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    source = tmp_path / 'source.mp4'
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=320x180:rate=24', '-t', '1', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', str(source)], check=True)
    result = tmp_path / 'preview.mp4'
    assert trial.render(source, result, ffmpeg, seconds=1) == 24
    paths = [tmp_path / 'first.png', tmp_path / 'second.png']
    for timestamp, path in zip(('0.15', '0.8'), paths):
        subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-ss', timestamp, '-i', str(result),
                        '-frames:v', '1', str(path)], check=True)
    with Image.open(paths[0]) as a, Image.open(paths[1]) as b:
        difference = ImageChops.difference(a.convert('RGB'), b.convert('RGB')).crop((335, 130, 550, 280))
        assert difference.getbbox() is not None

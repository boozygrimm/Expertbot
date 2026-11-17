import subprocess
from pathlib import Path

import imageio_ffmpeg
from PIL import Image
import pytest
from news_prototype import bulletin_video_insert_trial as trial, bulletin_wide_screen


def test_synthetic_video_renders_behind_presenters_without_modifying_source(tmp_path):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    source = tmp_path / 'source.mp4'
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=red:s=320x180:r=24', '-f', 'lavfi', '-i', 'sine=frequency=440',
                    '-t', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                    str(source)], check=True)
    original = source.read_bytes()
    result = tmp_path / 'preview.mp4'
    assert trial.render(source, result, ffmpeg, seconds=1) == 24
    assert result.stat().st_size > 0 and source.read_bytes() == original
    # Source sound is deliberately not carried into the preview.
    audio = subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-i', str(result), '-map', '0:a',
                            '-f', 'null', '-'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert audio.returncode != 0
    framefile = tmp_path / 'frame.png'
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-ss', '0.5', '-i', str(result),
                    '-frames:v', '1', str(framefile)], check=True)
    with Image.open(framefile) as frame:
        frame = frame.convert('RGB')
        r, g, b = frame.getpixel((440, 150))
        assert r > g + 100 and r > b + 100
        # The sensei layer is composited in front of the red video.
        r, g, b = frame.getpixel((600, 210))
        assert not (r > g + 100 and r > b + 100)
    with pytest.raises(FileExistsError): trial.render(source, result, ffmpeg, seconds=1)


def test_bad_inputs_are_rejected_before_starting_ffmpeg(tmp_path):
    source = tmp_path / 'video.mp4'; source.write_bytes(b'irrelevant')
    with pytest.raises(ValueError): trial.render(source, tmp_path / 'out.mp4', 'bad', seconds=13)
    with pytest.raises(ValueError): trial.render(source, source, 'bad', seconds=1)
    symlink = tmp_path / 'link'; symlink.symlink_to(source)
    with pytest.raises(ValueError): trial.render(symlink, tmp_path / 'out.mp4', 'bad', seconds=1)

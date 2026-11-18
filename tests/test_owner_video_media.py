import asyncio
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image
from handlers import owner_video_media as media


@pytest.mark.asyncio
@pytest.mark.parametrize('size,box', [('320x180', '70:0:180:180'), ('180x320', '0:70:180:180')])
async def test_conversion_center_crops_without_borders_and_keeps_audio(tmp_path, size, box):
    ffmpeg = pytest.importorskip('imageio_ffmpeg').get_ffmpeg_exe()
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        pytest.skip('ffprobe not installed')
    source, output = tmp_path/'source.mp4', tmp_path/'note.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', f'color=c=red:s={size}:r=24,drawbox={box}:color=green:t=fill',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=24000', '-t', '2', '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)], check=True)
    converted_duration = await media.make_note(source, output, ffmpeg=ffmpeg, ffprobe=ffprobe)
    info = json.loads(subprocess.check_output([ffprobe, '-v', 'error', '-show_entries',
        'stream=codec_type,width,height:format=duration', '-of', 'json', str(output)]))
    video = next(s for s in info['streams'] if s['codec_type'] == 'video')
    assert (video['width'], video['height']) == (480, 480)
    assert any(s['codec_type'] == 'audio' for s in info['streams'])
    assert abs(float(info['format']['duration'])-2) < .3
    assert converted_duration == float(info['format']['duration'])
    frame = tmp_path/'frame.png'
    subprocess.run([ffmpeg, '-v', 'error', '-ss', '1', '-i', str(output), '-frames:v', '1', str(frame)], check=True)
    with Image.open(frame) as im:
        im = im.convert('RGB')
        # Green central square fills even the outermost pixels. The red outer
        # strips are cropped for both landscape and portrait input.
        for x, y in ((10, 10), (469, 10), (10, 469), (469, 469), (240, 240)):
            r, g, b = im.getpixel((x, y))
            assert g > 70 and r < 60 and b < 60



@pytest.mark.asyncio
async def test_too_long_rejected_before_conversion(tmp_path):
    from unittest.mock import AsyncMock, patch
    source = tmp_path/'in.mp4'; source.write_bytes(b'anything')
    with patch.object(media, 'probe', AsyncMock(return_value=60.1)), patch.object(media, '_run', AsyncMock()) as run:
        with pytest.raises(media.VideoError, match='60 секунд'):
            await media.make_note(source, tmp_path/'out.mp4')
        run.assert_not_awaited()
    assert not (tmp_path/'out.mp4').exists()


@pytest.mark.asyncio
async def test_probe_rejects_invalid_size_duration_or_multiple_video_streams(tmp_path):
    from unittest.mock import AsyncMock, patch
    source = tmp_path/'in.mp4'; source.write_bytes(b'x')
    for data in [
        {'format': {'duration': 'nan'}, 'streams': [{'codec_type': 'video', 'width': 10, 'height': 10}]},
        {'format': {'duration': '3'}, 'streams': [{'codec_type': 'video', 'width': 5000, 'height': 10}]},
        {'format': {'duration': '3'}, 'streams': [{'codec_type': 'video', 'width': 10, 'height': 10}]*2},
    ]:
        with patch.object(media, '_run', AsyncMock(return_value=json.dumps(data).encode())):
            with pytest.raises(media.VideoError): await media.probe(source)

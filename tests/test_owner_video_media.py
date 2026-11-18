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
async def test_conversion_keeps_complete_landscape_within_circle_and_audio(tmp_path):
    ffmpeg = pytest.importorskip('imageio_ffmpeg').get_ffmpeg_exe()
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        pytest.skip('ffprobe not installed')
    source, output = tmp_path/'source.mp4', tmp_path/'note.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=320x180:r=24',
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
        # The corners of the red frame remain inside the circular mask, with
        # a small safety margin against codec artefacts and Telegram's crop.
        for x, y in ((39, 128), (440, 128), (39, 351), (440, 351)):
            assert math.hypot(x-240, y-240) < 240
            r, g, b = im.getpixel((x, y))
            assert r > 150 and g < 80 and b < 80
        assert max(im.getpixel((10, 10))) < 40


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

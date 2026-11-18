"""Single-story bulletin studio, using only synthetic audio and local media."""
import json
import subprocess
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

import imageio_ffmpeg
import pytest
from PIL import Image

from news_prototype import (bulletin_layered_video, bulletin_wide_screen, news_image_codec,
                            podcast_audio_trial, story_audio, story_pipeline, story_studio_video)
from tests.test_podcast_video_trial import make_segment
from tests.test_owner_drafts import DRAFT


def create_story(folder, *, image=True):
    folder.mkdir()
    names = ['a.wav', 'b.wav']
    for name in names:
        make_segment(folder / name, seconds=6)
    podcast_audio_trial.assemble_wav([folder / name for name in names], folder / 'dialogue.wav')
    (folder / 'story.json').write_text(json.dumps({'ticker': 'ТЕСТ', 'turns': [
        {'speaker': 'Бендер', 'audio': 'a.wav'}, {'speaker': 'Киберсенсей', 'audio': 'b.wav'}]}), encoding='utf-8')
    if image:
        im = Image.new('RGB', (120, 120), 'red')
        for x in range(60, 120):
            for y in range(120):
                im.putpixel((x, y), (0, 0, 255))
        out = BytesIO(); im.save(out, format='PNG')
        value = news_image_codec.make_thumbnail(out.getvalue())
        (folder / 'news-image.jpg').write_bytes(news_image_codec.decode_thumbnail(value))


def test_story_studio_preserves_reviewed_image_views_and_speaker_timing(tmp_path):
    folder = tmp_path / 'story'; create_story(folder)
    regions = [[0, 0, 450, 1000], [550, 0, 1000, 1000]]
    with patch.object(bulletin_layered_video, 'render') as render:
        story_studio_video.render(folder, tmp_path / 'video.mp4', 'ffmpeg', image_regions=regions)
    kwargs = render.call_args.kwargs
    views = kwargs['picture_views']
    assert len(views) == 3
    assert views[1].getpixel((342, 194))[0] > 180
    assert views[2].getpixel((342, 194))[2] > 180
    assert kwargs['timeline'][1][2] == 'Бендер'
    assert 'Киберсенсей' in kwargs['timeline'][1]
    start, end = kwargs['picture_window']
    assert end - start >= 7
    assert [bulletin_layered_video.picture_view_blend(views, (start, end), start + (end-start)*p)[0]
            for p in (.05, .3, .6, .9)] == [0, 1, 2, 0]
    first, second, mix = bulletin_layered_video.picture_view_blend(views, (start, end), start + (end-start)*.20)
    assert (first, second) == (0, 1) and .49 < mix < .51
    assert kwargs['sections'][0]['ticker'] == 'ТЕСТ'


def test_without_picture_uses_bulletin_fallback_and_no_focus(tmp_path):
    folder = tmp_path / 'story'; create_story(folder, image=False)
    with patch.object(bulletin_layered_video, 'render') as render:
        story_studio_video.render(folder, tmp_path / 'video.mp4', 'ffmpeg')
    assert render.call_args.kwargs['picture_views'] is None
    with pytest.raises(ValueError):
        story_studio_video.render(folder, tmp_path / 'video.mp4', 'ffmpeg', image_regions=[[0, 0, 500, 500]])


def test_new_studio_preflight_before_tts(tmp_path):
    with patch('video_prototype.news_jobs.story_studio_assets_ready', return_value=False), \
         patch.object(story_audio, 'prepare') as prepare:
        with pytest.raises(ValueError, match='Студия'):
            story_pipeline.run({}, tmp_path / 'job', 'ffmpeg', ['unused'], studio='bulletin')
        prepare.assert_not_called()
    assert not (tmp_path / 'job').exists()


def test_synthetic_render_uses_bulletin_studio_and_picture(tmp_path):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    image = BytesIO(); Image.new('RGB', (120, 80), (255, 10, 20)).save(image, format='PNG')
    script = DRAFT['script'] | {'image_b64': news_image_codec.make_thumbnail(image.getvalue())}
    def synthesize(text, target, keys, start_index):
        make_segment(target, seconds=2)
    with patch.object(story_audio.gemini_tts, 'synthesize', side_effect=synthesize):
        result = story_pipeline.run(script, tmp_path / 'job', ffmpeg, ['unused'], studio='bulletin')
    assert json.loads((result.parent / 'status.json').read_text())['stage'] == 'ready_for_review'
    png = tmp_path / 'frame.png'
    subprocess.run([ffmpeg, '-v', 'error', '-ss', '4', '-i', str(result), '-frames:v', '1', str(png)], check=True)
    with Image.open(png) as frame:
        frame = frame.convert('RGB')
        assert frame.size == bulletin_wide_screen.SIZE
        r, g, b = frame.getpixel((445, 175))
        assert r > 170 and g < 90 and b < 90
        expected = bulletin_wide_screen.compose()
        # A studio background point outside the screen and faces distinguishes
        # this scene from the older single-story backdrop, allowing codec noise.
        assert sum(abs(a-b) for a,b in zip(frame.getpixel((25, 100)), expected.getpixel((25, 100)))) < 75


def test_synthetic_video_shows_reviewed_focus_in_new_studio(tmp_path):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    folder = tmp_path / 'story'; create_story(folder)
    output = tmp_path / 'result.mp4'
    story_studio_video.render(folder, output, ffmpeg, image_regions=[[0, 0, 450, 1000], [550, 0, 1000, 1000]])
    for second, dominant in ((6.4, 0), (9, 2)):
        png = tmp_path / f'{second}.png'
        subprocess.run([ffmpeg, '-v', 'error', '-ss', str(second), '-i', str(output), '-frames:v', '1', str(png)], check=True)
        with Image.open(png) as frame:
            rgb = frame.convert('RGB').getpixel((445, 175))
            assert rgb[dominant] > 150 and rgb[dominant] > rgb[2-dominant] * 2

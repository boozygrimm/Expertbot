from array import array
from io import BytesIO
import math
import subprocess
import wave
from unittest.mock import patch

import imageio_ffmpeg
from PIL import Image, ImageChops, ImageOps, ImageStat
import pytest

from news_prototype import (bulletin_video_pipeline as pipeline, bulletin_video_timing_plan as timing,
    bulletin_timing, combined_trial, podcast_audio_trial, podcast_video_trial)


def wav(path, data):
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(24000); out.writeframes(data.tobytes())


def sine(seconds, hz, level=4000):
    return array('h', (round(level * math.sin(2 * math.pi * hz * i / 24000)) for i in range(round(seconds * 24000))))


def inputs(tmp_path):
    p1, p2, dialogue, remarks = (tmp_path / n for n in ('1.wav', '2.wav', 'dialogue.wav', 'remarks.wav'))
    wav(p1, sine(1, 250) + array('h', [0]) * 24000 + sine(1, 250))
    wav(p2, sine(1, 300) + array('h', [0]) * 24000 + sine(1, 300))
    wav(remarks, sine(.5, 700))
    podcast_audio_trial.assemble_wav([p1, p2], dialogue)
    data = {'sections': [
        {'id': 1, 'ticker': 'Первая новость', 'turns': [{'speaker': 'Бендер', 'text': 'Первая новость.'}]},
        {'id': 2, 'ticker': 'Вторая новость', 'turns': [{'speaker': 'Киберсенсей', 'text': 'Вторая новость.'}]},
    ]}
    return data, dialogue, [(1, p1, 'Бендер'), (2, p2, 'Киберсенсей')], remarks


def test_mixing_only_fills_reservation_and_keeps_speech_and_full_remarks():
    audio = sine(1, 250) + array('h', [0]) * 24000 + sine(4, 250)
    sections = [timing.Section(1, 0, 72000), timing.Section(2, 72000, len(audio))]
    entry = timing.Insert(1, 36, 72)
    plan = timing.plan(audio, sections, [entry])
    source = sine(5, 440)
    comments = sine(.5, 700)
    mixed = pipeline._mix(plan, entry, source, 5, comments)
    event = plan.events[0]
    start = (event.clip_start - combined_trial.INTRO_FRAMES) * 1000
    end = start + entry.clip_frames * 1000
    assert mixed[:start] == plan.samples[:start] and mixed[end:] == plan.samples[end:]
    assert any(mixed[start:end])
    # With silent background, spoken samples are exactly preserved.
    voice_only = pipeline._mix(plan, entry, array('h'), 5, comments)
    assert voice_only[start + 24000:start + 24000 + len(comments)] == comments
    with pytest.raises(ValueError, match='не помещаются'):
        pipeline._mix(plan, entry, source, 5, sine(2, 700))


@pytest.mark.parametrize('section_id,cut_frame,video_start,whole_story,clip_frames',
                         [(1, 36, 0, True, 240), (2, 116, 81, False, 72)])
def test_real_synthetic_bulletin_video_and_original_audio_are_rendered(tmp_path, section_id, cut_frame, video_start, whole_story, clip_frames):
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    data, dialogue, parts, remarks = inputs(tmp_path)
    from news_prototype.news_image_codec import make_thumbnail
    picture = BytesIO(); Image.new('RGB', (300, 200), 'red').save(picture, format='PNG')
    data['sections'][1 if section_id == 1 else 0]['image_b64'] = make_thumbnail(picture.getvalue())
    if whole_story:
        # Joint two-speaker recording, as in the approved full bulletin trial.
        data['sections'][0]['turns'].append({'speaker': 'Киберсенсей', 'text': 'Второй голос.'})
        parts = [(number, path) for number, path, _ in parts]
    source = tmp_path / 'source.mp4'
    source_wav = tmp_path / 'source.wav'
    cue = array('h')
    for frequency in (330, 440, 550, 660, 770):
        cue.extend(sine(1, frequency))
    wav(source_wav, cue)
    subprocess.run([ff, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24',
                    '-i', str(source_wav), '-t', '5', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', str(source)], check=True)
    output = tmp_path / 'bulletin.mp4'
    # The bundled FFmpeg has no ffprobe. Decode and mixing use real FFmpeg;
    # metadata is simulated only for the synthetic known 5-second file.
    with patch.object(pipeline.telegram_public_source, 'inspect', return_value=(5., 320, 180, True)):
        assert pipeline.render(data, dialogue, parts, source, output, ff, 'mock-ffprobe',
                               section_id=section_id, cut_frame=cut_frame, clip_frames=clip_frames, remarks=remarks) == output
    assert output.stat().st_size > 1000
    subprocess.run([ff, '-nostdin', '-v', 'error', '-xerror', '-i', str(output), '-f', 'null', '-'], check=True)
    # Full-TV view must show the continuing source, not restart at 1 s.
    observed_file = tmp_path / 'observed.png'
    when = (combined_trial.INTRO_FRAMES + cut_frame + 18 + 24) / 24
    source_time = (cut_frame - video_start + 18 + 24) / 24
    subprocess.run([ff, '-v', 'error', '-ss', str(when), '-i', str(output), '-frames:v', '1', str(observed_file)], check=True)
    refs = []
    for index, t in enumerate((source_time, 1.0)):
        path = tmp_path / f'ref-{index}.png'
        subprocess.run([ff, '-v', 'error', '-ss', str(t), '-i', str(source), '-frames:v', '1', str(path)], check=True)
        with Image.open(path) as image:
            refs.append(image.convert('RGB').resize((704, 396), Image.Resampling.LANCZOS))
    with Image.open(observed_file) as image:
        observed = image.convert('RGB').crop((210, 120, 690, 380))
    differences = [sum(ImageStat.Stat(ImageChops.difference(observed, image.crop((100, 53, 580, 313)))).mean)
                   for image in refs]
    assert differences[0] < differences[1] * .65, differences
    # The same decoder continues before AND after the full-TV transition.
    for label, n in (('before', combined_trial.INTRO_FRAMES + cut_frame + 18 - 12),
                     ('after', combined_trial.INTRO_FRAMES + cut_frame + 18 + clip_frames + 2 + 12)):
        screen = tmp_path / f'studio-{label}.png'
        ref = tmp_path / f'source-{label}.png'
        source_position = ((n - combined_trial.INTRO_FRAMES - video_start) / 24) % 5
        subprocess.run([ff, '-v', 'error', '-ss', str(n / 24), '-i', str(output), '-frames:v', '1', str(screen)], check=True)
        subprocess.run([ff, '-v', 'error', '-ss', str(source_position), '-i', str(source), '-frames:v', '1', str(ref)], check=True)
        with Image.open(ref) as raw:
            video = raw.convert('RGB').resize((704, 396), Image.Resampling.LANCZOS)
            fitted = ImageOps.contain(video, (456, 212), Image.Resampling.LANCZOS)
            panel = Image.new('RGB', (456, 212), (18, 53, 94))
            panel.paste(fitted, ((456 - fitted.width) // 2, (212 - fitted.height) // 2))
        with Image.open(screen) as screenshot:
            observed_panel = screenshot.convert('RGB').crop((330, 130, 470, 190))
        difference = sum(ImageStat.Stat(ImageChops.difference(observed_panel, panel.crop((111, 23, 251, 83)))).mean)
        assert difference < 35, (label, difference)
    # The other story still shows its existing picture.
    added_frames = 18 + clip_frames + 2 + 18
    picture_time = combined_trial.INTRO_FRAMES / 24 + (3.35 + added_frames / 24 + 1 if section_id == 1 else .5)
    image_file = tmp_path / 'other-story.png'
    subprocess.run([ff, '-v', 'error', '-ss', str(picture_time), '-i', str(output), '-frames:v', '1', str(image_file)], check=True)
    with Image.open(image_file) as frame:
        red, green, blue = frame.convert('RGB').getpixel((440, 150))
    assert red > green + 100 and red > blue + 100
    # Time-coded source audio must match the same video position: the first
    # quarter second of the clip is in the 550 Hz segment, not 330 Hz at zero.
    audio_when = (combined_trial.INTRO_FRAMES + cut_frame + 18) / 24 + .2
    sound = subprocess.run([ff, '-v', 'error', '-ss', str(audio_when), '-t', '0.2', '-i', str(output),
                            '-map', '0:a:0', '-f', 's16le', '-ac', '1', '-ar', '24000', 'pipe:1'],
                           stdout=subprocess.PIPE, check=True).stdout
    samples = array('h'); samples.frombytes(sound)
    crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
    assert len(samples) > 100 and 100 <= crossings <= 120, crossings
    if clip_frames >= 5 * 24:
        # After the five-second source wraps, sound must wrap with the video.
        wrapped_when = (combined_trial.INTRO_FRAMES + cut_frame + 18) / 24 + 3.15
        sound = subprocess.run([ff, '-v', 'error', '-ss', str(wrapped_when), '-t', '0.2', '-i', str(output),
                                '-map', '0:a:0', '-f', 's16le', '-ac', '1', '-ar', '24000', 'pipe:1'],
                               stdout=subprocess.PIPE, check=True).stdout
        samples = array('h'); samples.frombytes(sound)
        crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
        assert 60 <= crossings <= 72, crossings
    with pytest.raises(ValueError):
        pipeline.render(data, dialogue, parts, source, output, ff, 'mock-ffprobe',
                        section_id=1, cut_frame=36, clip_frames=72)


def test_fail_closed_before_decode_on_invalid_cut_or_over_budget(tmp_path):
    data, dialogue, parts, _ = inputs(tmp_path)
    source = tmp_path / 'source.mp4'; source.write_bytes(b'fake')
    output = tmp_path / 'out.mp4'
    with patch.object(pipeline.telegram_public_source, 'inspect') as inspect:
        with pytest.raises(ValueError, match='тишины'):
            pipeline.render(data, dialogue, parts, source, output, 'ffmpeg', 'ffprobe',
                            section_id=1, cut_frame=10, clip_frames=72)
        inspect.assert_not_called()
        with pytest.raises(bulletin_timing.BulletinTooLong):
            pipeline.render(data, dialogue, parts, source, output, 'ffmpeg', 'ffprobe',
                            section_id=1, cut_frame=36, clip_frames=300 * 24)
        inspect.assert_not_called()
    assert not output.exists()


def test_cli_uses_saved_full_story_recordings_without_tts_or_send(tmp_path, capsys):
    import json
    data, dialogue, parts, _ = inputs(tmp_path)
    snapshot = tmp_path / 'bulletin.json'; snapshot.write_text(json.dumps(data), encoding='utf-8')
    source = tmp_path / 'source.mp4'; source.write_bytes(b'not used by this test')
    output = tmp_path / 'new.mp4'
    args = ['bulletin_video_pipeline', '--bulletin', str(snapshot), '--audio-dir', str(tmp_path),
            '--source', str(source), '--section', '1', '--cut-frame', '36', '--output', str(output)]
    with patch('sys.argv', args), patch('shutil.which', side_effect=lambda x: x), \
         patch.object(pipeline, 'render', return_value=output) as render:
        assert pipeline.main() == 0
    assert render.call_args.args[1] == dialogue
    assert render.call_args.args[2] == [(1, tmp_path / '1-normalized.wav'), (2, tmp_path / '2-normalized.wav')]
    assert render.call_args.kwargs['clip_frames'] == 240
    assert 'отправка не выполнялась' in capsys.readouterr().out

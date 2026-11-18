from array import array
from dataclasses import replace
import pytest

from news_prototype import bulletin_video_timing_plan as timing, bulletin_timing, combined_trial


def fixture():
    # 2 sections, with one full second of digital silence in each.
    audio = array('h', [1200]) * (timing.RATE * 6)
    for second in (1, 4):
        audio[second * timing.RATE:(second + 1) * timing.RATE] = array('h', [0]) * timing.RATE
    return audio, [timing.Section(1, 0, timing.RATE * 3),
                   timing.Section(2, timing.RATE * 3, timing.RATE * 6)]


def test_preserves_every_pcm_sample_and_uses_same_continuous_video_position():
    audio, sections = fixture()
    requests = [timing.Insert(2, 4 * timing.FPS + 12, 10 * timing.FPS),
                timing.Insert(1, timing.FPS + 12, 10 * timing.FPS)]
    result = timing.plan(audio, sections, requests)
    assert [event.section_id for event in result.events] == [1, 2]
    extra_frames = 18 + 240 + 2 + 18
    cut1, cut2 = (timing.FPS + 12) * 1000, (4 * timing.FPS + 12) * 1000
    extra = extra_frames * 1000
    assert result.samples[:cut1] == audio[:cut1]
    assert not any(result.samples[cut1:cut1 + extra])
    assert result.samples[cut1 + extra:cut2 + extra] == audio[cut1:cut2]
    assert not any(result.samples[cut2 + extra:cut2 + 2 * extra])
    assert result.samples[cut2 + 2 * extra:] == audio[cut2:]
    assert result.events[0].source_elapsed_at_clip == 36 + 18
    assert result.events[1].source_elapsed_at_clip == 36 + 18
    assert result.events[1].clip_start == combined_trial.INTRO_FRAMES + 108 + extra_frames + 18
    assert result.total_frames == combined_trial.INTRO_FRAMES + 6 * timing.FPS + 2 * extra_frames + combined_trial.TAIL_FRAMES
    assert audio[0] == 1200 and len(audio) == 6 * timing.RATE


def test_non_aligned_section_start_uses_next_frame_for_source():
    audio, _ = fixture()
    sections = [timing.Section(1, 1, 3 * timing.RATE),
                timing.Section(2, 3 * timing.RATE, 6 * timing.RATE)]
    result = timing.plan(audio, sections, [timing.Insert(1, 36, 240)])
    assert result.events[0].source_elapsed_at_clip == 36 - 1 + 18


def test_limit_is_for_whole_release_and_never_truncates():
    audio = array('h', [0]) * (280 * timing.RATE)
    sections = [timing.Section(1, 0, 140 * timing.RATE),
                timing.Section(2, 140 * timing.RATE, 280 * timing.RATE)]
    before = audio[:]
    with pytest.raises(bulletin_timing.BulletinTooLong):
        timing.plan(audio, sections, [timing.Insert(1, timing.FPS, 20 * timing.FPS)])
    assert audio == before
    result = timing.plan(audio, sections, [])
    assert result.samples == before
    assert result.total_frames < 300 * timing.FPS


def test_silence_is_required_in_the_selected_section():
    audio, sections = fixture()
    request = timing.Insert(1, 36, 240)
    cut = request.cut_frame * timing.SAMPLES_PER_FRAME
    audio[cut - 1] = 1
    with pytest.raises(ValueError, match='тишины'):
        timing.plan(audio, sections, [request])
    with pytest.raises(ValueError, match='тишины'):
        timing.plan(audio, sections, [replace(request, cut_frame=4 * timing.FPS + 12)])


def test_duplicate_invalid_or_unbounded_input_is_rejected():
    audio, sections = fixture()
    request = timing.Insert(1, 36, 240)
    bad = [[request, request], [replace(request, section_id=3)], [replace(request, clip_frames=0)],
           [replace(request, cut_frame=True)], [replace(request, before_frames=-1)]]
    for value in bad:
        with pytest.raises(ValueError): timing.plan(audio, sections, value)
    with pytest.raises(ValueError): timing.plan(audio, [sections[1], sections[0]], [])
    with pytest.raises(ValueError): timing.plan(audio, [sections[0], sections[0]], [])
    with pytest.raises(ValueError): timing.plan(array('i', [0]), sections, [])


def test_all_20_stories_and_insertions_still_obey_global_limit():
    samples_per_story = timing.RATE * 2
    audio = array('h', [0]) * (20 * samples_per_story)
    sections = [timing.Section(i + 1, i * samples_per_story, (i + 1) * samples_per_story) for i in range(20)]
    inserts = [timing.Insert(i + 1, (i * 2 + 1) * timing.FPS, 10 * timing.FPS) for i in range(20)]
    result = timing.plan(audio, sections, inserts)
    assert len(result.events) == 20 and result.total_frames <= 300 * timing.FPS
    too_long = [replace(entry, clip_frames=12 * timing.FPS) for entry in inserts]
    with pytest.raises(bulletin_timing.BulletinTooLong): timing.plan(audio, sections, too_long)


def test_exact_300_seconds_allowed_but_one_extra_sample_is_not():
    frames = 300 * timing.FPS - combined_trial.INTRO_FRAMES - combined_trial.TAIL_FRAMES
    audio = array('h', [0]) * (frames * timing.SAMPLES_PER_FRAME)
    middle = len(audio) // 2
    sections = [timing.Section(1, 0, middle), timing.Section(2, middle, len(audio))]
    assert timing.plan(audio, sections, []).total_frames == 300 * timing.FPS
    audio.append(0)
    sections[1] = timing.Section(2, middle, len(audio))
    with pytest.raises(bulletin_timing.BulletinTooLong):
        timing.plan(audio, sections, [])

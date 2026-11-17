from PIL import ImageChops
import pytest

from news_prototype import (bender_trial, bulletin_layered_video, bulletin_remote_animation as animation,
                            bulletin_timing, bulletin_wide_screen, combined_trial)


def test_screen_turns_on_and_off_after_power_clicks():
    assert animation.screen_level(.5, .65, 5) == 0
    assert animation.screen_level(1.4, .65, 5) == pytest.approx(1)
    assert animation.screen_level(4.9, .65, 5) == pytest.approx(1)
    assert animation.screen_level(5.3, .65, 5) < 1
    assert animation.screen_level(5.7, .65, 5) == 0


def test_screen_dimming_does_not_dim_presenters():
    bright = bulletin_wide_screen.compose(screen_level=1)
    dark = bulletin_wide_screen.compose(screen_level=0)
    assert bright.getpixel((430, 140)) != dark.getpixel((430, 140))
    assert bright.getpixel((250, 215)) == dark.getpixel((250, 215))
    assert bright.getpixel((600, 210)) == dark.getpixel((600, 210))
    with pytest.raises(ValueError): bulletin_wide_screen.compose(screen_level=1.1)


def test_press_is_timed_to_click_and_stops_between_events():
    at = 5.0
    assert animation.press_amount(4.8, [at]) == 0
    assert 0 < animation.press_amount(4.92, [at]) < 1
    assert animation.press_amount(at, [at]) == pytest.approx(1)
    assert 0 < animation.press_amount(5.07, [at]) < 1
    assert animation.press_amount(5.15, [at]) == 0
    assert animation.press_amount(5, []) == 0


def test_press_changes_only_remote_region_and_does_not_modify_input():
    frame = bulletin_wide_screen.compose()
    original = frame.copy()
    assert animation.apply(frame, 0).tobytes() == frame.tobytes()
    pressed = animation.apply(frame, 1)
    bbox = ImageChops.difference(original, pressed).getbbox()
    assert bbox is not None
    x0, y0, x1, y1 = bbox
    assert 720 <= x0 < x1 <= 760 and 385 <= y0 < y1 <= 415
    assert frame.tobytes() == original.tobytes()


def test_press_peak_matches_audio_click_in_inter_news_gap():
    intro = combined_trial.INTRO_FRAMES / bender_trial.FPS
    windows = [bulletin_timing.SectionWindow(1, intro, intro + 1),
               bulletin_timing.SectionWindow(2, intro + 1.35, intro + 2.35)]
    position, = bulletin_layered_video.click_positions(windows, 24000)
    at = intro + position / 24000 + .002
    assert windows[0].end < at < windows[1].start
    assert animation.press_amount(at, [at]) == pytest.approx(1)
    assert abs(windows[1].start - at) < .1

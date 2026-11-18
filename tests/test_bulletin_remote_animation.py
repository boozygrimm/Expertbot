from PIL import ImageChops
import pytest

from news_prototype import (bender_trial, bulletin_layered_video, bulletin_remote_animation as animation,
                            bulletin_timing, bulletin_wide_screen, combined_trial)


def test_outer_tv_dimming_hides_studio_but_preserves_remote_and_frame():
    bright = bulletin_wide_screen.compose()
    dark = combined_trial.display(bright, animation.television_mask(), animation.unlit_screen(), .1, 8.)
    for point in ((430, 140), (270, 225), (600, 210), (400, 390)):
        assert dark.getpixel(point) == animation.unlit_screen().getpixel(point)
        assert bright.getpixel(point) != dark.getpixel(point)
    for point in ((735, 394), (750, 405), (775, 425), (890, 200), (20, 20)):
        assert bright.getpixel(point) == dark.getpixel(point)
    assert combined_trial.display(bright, animation.television_mask(), animation.unlit_screen(), 3., 8.).tobytes() == bright.tobytes()


def test_crt_flash_and_button_glow_match_power_events_without_touching_frame():
    frame = bulletin_wide_screen.compose()
    mask, glass = animation.television_mask(), animation.unlit_screen()
    original = combined_trial.display(frame, mask, glass, .1, 8.)
    on, off = combined_trial.ON_PRESS, 8.
    on_flash = combined_trial.display(frame, mask, glass, on + .21, off)
    off_flash = combined_trial.display(frame, mask, glass, off + .54, off)
    assert sum(on_flash.getpixel((400, 271))) > sum(original.getpixel((400, 271))) + 100
    assert sum(off_flash.getpixel((400, 271))) > sum(original.getpixel((400, 271))) + 100
    assert on_flash.getpixel((20, 20)) == original.getpixel((20, 20))
    assert on_flash.getpixel((735, 394)) == original.getpixel((735, 394))
    glow = combined_trial.display(frame, mask, glass, on, off)
    assert glow.getpixel((735, 394)) != original.getpixel((735, 394))
    idle = combined_trial.display(frame, mask, glass, 3., off)
    assert idle.tobytes() == frame.tobytes()


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

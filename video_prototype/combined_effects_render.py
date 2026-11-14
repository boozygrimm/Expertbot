"""Temporary all-at-once visual trial, not a random production scheduler."""
from pathlib import Path

if __package__:
    from .flask_render import frame_with_bubbles, render as base_render
    from .pyramid_render import frame_with_pyramid
    from .beholder_render import frame_with_beholder, prepare_sprites as prepare_beholder
    from .hedgehog_render import frame_with_hedgehog, make_run_frames
    from .pai_mei_render import frame_with_cameo, prepare_portrait
else:
    from flask_render import frame_with_bubbles, render as base_render
    from pyramid_render import frame_with_pyramid
    from beholder_render import frame_with_beholder, prepare_sprites as prepare_beholder
    from hedgehog_render import frame_with_hedgehog, make_run_frames
    from pai_mei_render import frame_with_cameo, prepare_portrait

HERE = Path(__file__).resolve().parent


def make_effect(portrait_path):
    hedgehog = make_run_frames(HERE / 'assets/hedgehog.png')
    beholder = prepare_beholder()
    portrait = prepare_portrait(portrait_path)
    def apply(frame, seconds):
        frame = frame_with_bubbles(frame, seconds)
        frame = frame_with_beholder(frame, seconds, beholder)
        frame = frame_with_pyramid(frame, seconds)
        frame = frame_with_hedgehog(frame, seconds, hedgehog)
        return frame_with_cameo(frame, seconds, portrait)
    return apply


def render(audio, closed, opened, blink, plate, portrait_path, output):
    effect = make_effect(portrait_path)
    base_render(audio, closed, opened, blink, plate, output, frame_effect=effect)

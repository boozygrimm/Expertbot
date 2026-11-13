"""Trial of planned glances toward multiple overlapping guests, not production."""
from PIL import Image

if __package__:
    from .render import SIZE, FPS, blink_at
    from .gaze_trial_render import EYE_BOX, prepare_eye_frames
    from .gaze_planner import Guest, plan_looks, gaze_at
    from .hedgehog_render import HERE, make_run_frames, frame_with_hedgehog
    from .beholder_render import prepare_sprites, frame_with_beholder
    from .pai_mei_render import prepare_portrait, frame_with_cameo
    from .mouth_trial_render import quick_levels, render as base_render
else:
    from render import SIZE, FPS, blink_at
    from gaze_trial_render import EYE_BOX, prepare_eye_frames
    from gaze_planner import Guest, plan_looks, gaze_at
    from hedgehog_render import HERE, make_run_frames, frame_with_hedgehog
    from beholder_render import prepare_sprites, frame_with_beholder
    from pai_mei_render import prepare_portrait, frame_with_cameo
    from mouth_trial_render import quick_levels, render as base_render

# Visibility windows are conservative, excluding fade-in/out and the parts of
# the hedgehog's path hidden behind the sign or Bender's head.
GUESTS = [Guest('hedgehog', 1.7, 3.1, 0), Guest('beholder', 2.5, 6.3, 1),
          Guest('pai_mei', 2.5, 6.0, 2)]
TARGETS = {'hedgehog': [(261,174),(306,169)],
           'beholder': [(259,171),(303,166)],
           'pai_mei': [(273,174),(318,169)]}


def make_effect(closed, portrait_path, audio_duration):
    plan = plan_looks(GUESTS, audio_duration)
    print('План взглядов:', ', '.join(f'{x.guest} {x.start:.1f}–{x.end:.1f} с' for x in plan) or 'нет', flush=True)
    frames = {name: prepare_eye_frames(closed, targets=targets) for name, targets in TARGETS.items()}
    neutral = frames['beholder'][0]
    hedgehog = make_run_frames(HERE / 'assets/hedgehog.png')
    creature = prepare_sprites()
    portrait = prepare_portrait(portrait_path)

    def effect(frame, seconds):
        frame = frame_with_beholder(frame, seconds, creature)
        frame = frame_with_hedgehog(frame, seconds, hedgehog)
        frame = frame_with_cameo(frame, seconds, portrait)
        # Never paste open eyes over the original blink.
        if blink_at(round(seconds * FPS)):
            return frame
        target, amount = gaze_at(seconds, plan)
        eye = frames[target][round(amount * (len(frames[target])-1))] if target else neutral
        image = Image.frombytes('RGB', (SIZE, SIZE), frame)
        image.paste(eye, EYE_BOX[:2])
        return image.tobytes()
    return effect


def render(audio, closed, opened, blink, plate, portrait_path, output):
    duration = len(quick_levels(audio)) / FPS
    effect = make_effect(closed, portrait_path, duration)
    base_render(audio, closed, opened, blink, plate, output, frame_effect=effect)

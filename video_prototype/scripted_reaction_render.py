"""Isolated trial: time the guests and glances to two spoken chunks."""
from PIL import Image

if __package__:
    from .render import SIZE, FPS, blink_at
    from .gaze_trial_render import EYE_BOX, prepare_eye_frames
    from .gaze_planner import Look, gaze_at
    from .hedgehog_render import HERE, make_run_frames, frame_with_hedgehog
    from .beholder_render import prepare_sprites, frame_with_beholder
    from .pai_mei_render import prepare_portrait, frame_with_cameo
    from .mouth_trial_render import render as base_render
else:
    from render import SIZE, FPS, blink_at
    from gaze_trial_render import EYE_BOX, prepare_eye_frames
    from gaze_planner import Look, gaze_at
    from hedgehog_render import HERE, make_run_frames, frame_with_hedgehog
    from beholder_render import prepare_sprites, frame_with_beholder
    from pai_mei_render import prepare_portrait, frame_with_cameo
    from mouth_trial_render import render as base_render

TARGETS = {'hedgehog': [(261,174),(306,169)],
           'beholder': [(259,171),(303,166)],
           'pai_mei': [(273,174),(318,169)]}


def scene_schedule(second_start, total_duration):
    """Schedule guests and glances from measured chunk boundaries, not text speed."""
    if second_start <= 1 or total_duration <= second_start:
        raise ValueError('Недостаточная длительность речевых частей')
    cameo_start = max(.2, second_start - .8)
    cameo_end = total_duration - .15
    hedgehog_start = max(0., second_start - .2)
    # Glance is bounded by the presence of the corresponding character.
    beast_end = min(second_start - .25, 3.9)
    pai_start, pai_end = second_start + .1, min(second_start + 1.2, cameo_end-.1)
    hedge_start, hedge_end = second_start + 1.5, min(second_start + 2.7, hedgehog_start + 3.1, total_duration-.1)
    candidates = [Look('beholder', .55, beast_end), Look('pai_mei', pai_start, pai_end),
                  Look('hedgehog', hedge_start, hedge_end)]
    looks = [look for look in candidates if look.end - look.start >= .5]
    return cameo_start, cameo_end, hedgehog_start, looks


def cameo_time(seconds, start, end):
    if seconds < start or seconds >= end:
        return None
    # The original cameo has a fixed 0.65s entry/exit; stretch its hold only.
    if seconds < start + .65:
        return 2 + seconds - start
    if seconds > end - .65:
        return 6.5 - (end - seconds)
    return 3.0


def make_effect(closed, portrait_path, second_start, total_duration):
    start, end, hedge_start, looks = scene_schedule(second_start, total_duration)
    print('План взглядов:', ', '.join(f'{x.guest} {x.start:.1f}–{x.end:.1f} с' for x in looks), flush=True)
    frames = {name: prepare_eye_frames(closed, targets=targets) for name, targets in TARGETS.items()}
    neutral = frames['beholder'][0]
    hedgehog = make_run_frames(HERE / 'assets/hedgehog.png')
    creature = prepare_sprites()
    portrait = prepare_portrait(portrait_path)

    def effect(frame, seconds):
        # Shift the accepted beholder's entry earlier to match the first chunk.
        frame = frame_with_beholder(frame, seconds + 1.5, creature)
        frame = frame_with_hedgehog(frame, seconds - hedge_start + 1., hedgehog)
        cameo = cameo_time(seconds, start, end)
        if cameo is not None:
            frame = frame_with_cameo(frame, cameo, portrait)
        if blink_at(round(seconds * FPS)):
            return frame
        target, amount = gaze_at(seconds, looks)
        eye = frames[target][round(amount * (len(frames[target])-1))] if target else neutral
        image = Image.frombytes('RGB', (SIZE, SIZE), frame)
        image.paste(eye, EYE_BOX[:2])
        return image.tobytes()
    return effect


def render(audio, closed, opened, blink, plate, portrait_path, output, second_start, total_duration):
    effect = make_effect(closed, portrait_path, second_start, total_duration)
    base_render(audio, closed, opened, blink, plate, output, frame_effect=effect)

"""Random-guest prototype for the existing scene; not connected to the worker."""
import json
from pathlib import Path
from PIL import Image

if __package__:
    from .render import FPS, SIZE, blink_at
    from .gaze_planner import gaze_at
    from .guest_planner import make_plan, local_time
    from .gaze_trial_render import EYE_BOX, prepare_eye_frames
    from .hedgehog_render import HERE, make_run_frames, frame_with_hedgehog, run_position
    from .beholder_render import prepare_sprites, frame_with_beholder
    from .pai_mei_render import prepare_portrait, frame_with_cameo
    from .flask_render import frame_with_bubbles
    from .pyramid_render import frame_with_pyramid
    from .coin_drop_render import frame_with_coin
    from .mouth_trial_render import quick_levels, render as base_render
else:
    from render import FPS, SIZE, blink_at
    from gaze_planner import gaze_at
    from guest_planner import make_plan, local_time
    from gaze_trial_render import EYE_BOX, prepare_eye_frames
    from hedgehog_render import HERE, make_run_frames, frame_with_hedgehog, run_position
    from beholder_render import prepare_sprites, frame_with_beholder
    from pai_mei_render import prepare_portrait, frame_with_cameo
    from flask_render import frame_with_bubbles
    from pyramid_render import frame_with_pyramid
    from coin_drop_render import frame_with_coin
    from mouth_trial_render import quick_levels, render as base_render

TARGETS = {'hedgehog': [(261,174),(306,169)],
           'beholder': [(259,171),(303,166)],
           'pai_mei': [(273,174),(318,169)]}


def hedgehog_eye_bin(local_seconds):
    """Approximate tracking as the hedgehog crosses the narrow visible strip."""
    pos = run_position(local_seconds)
    if pos is None:
        return 0
    progress = max(0., min(1., (pos[0] - 169) / (227 - 169)))
    return round(progress * 4)


def make_effect(closed, portrait_path, plan):
    names = {guest.name for guest in plan.guests}
    if 'pai_mei' in names and (portrait_path is None or not Path(portrait_path).is_file()):
        raise FileNotFoundError('Для сенсея нужна сохранённая картинка из пробы')
    eyes = {name: prepare_eye_frames(closed, targets=TARGETS[name]) for name in names if name != 'hedgehog'}
    hedge_eyes = []
    if 'hedgehog' in names:
        for shift in (0, 1, 2, 3, 4):
            targets = [(261 + shift, 174), (306 + shift, 169)]
            hedge_eyes.append(prepare_eye_frames(closed, targets=targets))
    neutral = next(iter(eyes.values()))[0] if eyes else hedge_eyes[0][0]
    hedgehog = make_run_frames(HERE / 'assets/hedgehog.png') if 'hedgehog' in names else None
    creature = prepare_sprites() if 'beholder' in names else None
    portrait = prepare_portrait(portrait_path) if 'pai_mei' in names else None

    def effect(frame, seconds):
        index = round(seconds * plan.fps)
        for decoration in plan.decorations:
            t = local_time(decoration, index, plan.fps)
            if t is None:
                continue
            if decoration.name == 'bubbles':
                frame = frame_with_bubbles(frame, t)
            elif decoration.name == 'pyramid':
                frame = frame_with_pyramid(frame, t)
            elif decoration.name == 'coin':
                frame = frame_with_coin(frame, t)
        for guest in plan.guests:
            t = local_time(guest, index, plan.fps)
            if t is None:
                continue
            if guest.name == 'hedgehog':
                frame = frame_with_hedgehog(frame, t, hedgehog)
            elif guest.name == 'beholder':
                frame = frame_with_beholder(frame, t, creature)
            elif guest.name == 'pai_mei':
                frame = frame_with_cameo(frame, t, portrait)
        if blink_at(round(seconds * FPS)):
            return frame
        name, amount = gaze_at(seconds, plan.looks)
        if name == 'hedgehog':
            guest = next(g for g in plan.guests if g.name == 'hedgehog')
            t = local_time(guest, index, plan.fps)
            series = hedge_eyes[hedgehog_eye_bin(t)] if t is not None else hedge_eyes[0]
            eye = series[round(amount * (len(series)-1))]
        elif name:
            eye = eyes[name][round(amount * (len(eyes[name])-1))]
        else:
            eye = neutral
        image = Image.frombytes('RGB', (SIZE, SIZE), frame)
        image.paste(eye, EYE_BOX[:2])
        return image.tobytes()
    return effect


def render(audio, closed, opened, blink, plate, portrait_path, output, seed, manifest=None):
    frame_count = len(quick_levels(audio))
    plan = make_plan(frame_count, seed, FPS)
    effect = make_effect(closed, portrait_path, plan)
    if manifest is not None:
        Path(manifest).write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Гости:', ', '.join(a.name for a in plan.guests), flush=True)
    print('Эффекты:', ', '.join(a.name for a in plan.decorations) or 'нет', flush=True)
    print('План взглядов:', ', '.join(f'{x.guest} {x.start:.1f}–{x.end:.1f} с' for x in plan.looks) or 'нет', flush=True)
    base_render(audio, closed, opened, blink, plate, output, frame_effect=effect)
    return plan

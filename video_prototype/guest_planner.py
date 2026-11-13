"""Deterministic per-video guest selection and visibility-bound gaze planning.

The Telegram worker is deliberately not wired to this experimental planner.
At least one of the three character guests is selected. Decorative effects and
future mutually exclusive sign variants are separate from character selection.
"""
from dataclasses import asdict, dataclass
import math
import random

if __package__:
    from .gaze_planner import Guest, plan_looks
else:
    from gaze_planner import Guest, plan_looks

FPS = 24
GUEST_NAMES = ('hedgehog', 'beholder', 'pai_mei')
# Natural animation duration and conservative fully visible window, measured
# relative to the start of each original effect, in seconds.
PROFILES = {
    'hedgehog': (3.2, .70, 2.10, 2.35),
    'beholder': (5.5, 1.00, 4.50, 3.00),
    'pai_mei': (4.5, .65, 3.85, 3.00),
}


@dataclass(frozen=True)
class Appearance:
    name: str
    start_frame: int
    end_frame: int
    visible_from_frame: int
    visible_until_frame: int
    mode: str  # normal, short_hold, or short_hedgehog


@dataclass(frozen=True)
class ScenePlan:
    seed: str
    frame_count: int
    fps: int
    guests: tuple
    looks: tuple

    def to_dict(self):
        return {'seed': self.seed, 'frame_count': self.frame_count, 'fps': self.fps,
                'guests': [asdict(g) for g in self.guests],
                'looks': [asdict(look) for look in self.looks]}


def select_guests(rng):
    """Uniformly select one of the seven nonempty subsets, never an empty set."""
    mask = rng.randrange(1, 1 << len(GUEST_NAMES))
    return tuple(name for i, name in enumerate(GUEST_NAMES) if mask & (1 << i))


def make_plan(frame_count, seed, fps=FPS):
    """Draw once per clip; frame-based intervals remain inside its audio length."""
    if isinstance(frame_count, bool) or not isinstance(frame_count, int) or frame_count <= 0:
        raise ValueError('Нужен положительный целый размер записи в кадрах')
    if isinstance(fps, bool) or not isinstance(fps, int) or fps <= 0:
        raise ValueError('Нужна положительная целая частота кадров')
    if not isinstance(seed, (str, int)) or isinstance(seed, bool):
        raise ValueError('Seed должен быть целым числом или строкой')
    seed = str(seed)
    rng = random.Random(seed)
    selected = select_guests(rng)
    appearances = []
    for name in selected:
        natural, visible_start, visible_end, _ = PROFILES[name]
        length = min(frame_count, max(1, round(natural * fps)))
        start = rng.randrange(frame_count - length + 1)
        end = start + length
        natural_frames = round(natural * fps)
        if length < natural_frames:
            # No time for the accepted full movement: keep a recognisable pose.
            # This avoids an artificially sped-up and jerky run.
            mode = 'short_hedgehog' if name == 'hedgehog' else 'short_hold'
            visible_from, visible_until = start, end
        else:
            mode = 'normal'
            visible_from = min(end - 1, start + math.ceil(visible_start * fps))
            visible_until = min(end, start + math.floor(visible_end * fps))
            visible_until = max(visible_from + 1, visible_until)
        appearances.append(Appearance(name, start, end, visible_from, visible_until, mode))

    # Priorities are also seeded so that simultaneous guests do not always
    # attract attention in the same order. One guest at a time, with a pause.
    order = list(selected)
    rng.shuffle(order)
    priority = {name: i for i, name in enumerate(order)}
    gaze_guests = [Guest(a.name, a.visible_from_frame / fps, a.visible_until_frame / fps,
                         priority[a.name]) for a in appearances]
    duration = frame_count / fps
    minimum = min(.75, max(1 / fps, duration * .20))
    glance = max(minimum, min(1.1, duration * .4))
    pause = min(.25, duration * .05)
    looks = tuple(plan_looks(gaze_guests, duration, glance=glance, pause=pause, minimum=minimum))
    return ScenePlan(seed, frame_count, fps, tuple(appearances), looks)


def local_time(appearance, frame, fps=FPS):
    """Map an appearance to its original renderer's local timeline."""
    if not appearance.start_frame <= frame < appearance.end_frame:
        return None
    if appearance.mode != 'normal':
        return PROFILES[appearance.name][3]
    return {'hedgehog': 1., 'beholder': 1.5, 'pai_mei': 2.}[appearance.name] + (frame - appearance.start_frame) / fps

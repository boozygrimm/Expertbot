"""A small, testable planner for looks toward actually visible guests."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Guest:
    name: str
    visible_from: float
    visible_until: float
    priority: int = 0


@dataclass(frozen=True)
class Look:
    guest: str
    start: float
    end: float


def plan_looks(guests, duration, glance=1.1, pause=.25, minimum=.75):
    """At most one look at a time; each is entirely inside its guest's visibility.

    Lower priority number wins simultaneous choices. No random choice is made in
    this isolated trial. Too-short or already departed guests are skipped.
    """
    if duration <= 0 or glance <= 0 or minimum <= 0 or pause < 0:
        return []
    remaining = [g for g in guests if g.visible_from < g.visible_until and
                 min(g.visible_until, duration) - max(g.visible_from, 0) >= minimum]
    now = 0.
    output = []
    while remaining:
        eligible = [g for g in remaining if g.visible_from <= now and
                    min(g.visible_until, duration) - now >= minimum]
        if not eligible:
            future = [g for g in remaining if g.visible_from > now and
                      min(g.visible_until, duration) - g.visible_from >= minimum]
            if not future:
                break
            now = min(g.visible_from for g in future)
            continue
        target = min(eligible, key=lambda g: (g.priority, g.visible_until, g.name))
        end = min(now + glance, target.visible_until, duration)
        output.append(Look(target.name, now, end))
        remaining.remove(target)
        now = end + pause
    return output


def gaze_at(seconds, looks, transition=.25):
    """Return (guest, amount) or (None, 0), smoothly returning to neutral."""
    if transition <= 0:
        raise ValueError('Переход должен быть положительным')
    for look in looks:
        if look.start <= seconds < look.end:
            weight = max(0., min(1., (seconds-look.start)/transition, (look.end-seconds)/transition))
            return look.guest, weight*weight*(3-2*weight)
    return None, 0.

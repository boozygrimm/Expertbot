"""Validated normalized rectangles for optional news-image detail views."""
from __future__ import annotations


def validate_regions(value: object) -> list[list[int]]:
    # Coordinates are x1, y1, x2, y2 in thousandths of the original image.
    if not isinstance(value, list) or not 1 <= len(value) <= 2:
        raise ValueError('Неверные области изображения')
    result = []
    for box in value:
        if not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box):
            raise ValueError('Неверные координаты изображения')
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= 1000 and 0 <= y1 < y2 <= 1000):
            raise ValueError('Область вне изображения')
        # Reject implausibly tiny or almost-full areas; use the geometric fallback.
        if x2 - x1 < 80 or y2 - y1 < 80 or ((x2-x1) * (y2-y1) > 900_000):
            raise ValueError('Неподходящий размер области')
        result.append([x1, y1, x2, y2])
    return result

"""Draft E3 selection rules; the previous experiments keep their original rule."""

from __future__ import annotations

from ego_motion import _box, _touch, same_object


def matches(candidate, kept, *, share=None, representative_radius=None, gap=3, pad=8):
    a, b = candidate["points"], kept["points"]
    if a[0][0] > b[-1][0] + gap or b[0][0] > a[-1][0] + gap:
        return False
    if representative_radius is not None:
        # Ask whether the omitted source reaches the image actually shown.
        representative = _box(kept["representative"], pad)
        frame = kept["representative_frame"]
        if not any(abs(i - frame) <= representative_radius and _touch(_box(point, pad), representative)
                   for i, point in a):
            return False
    if share is None:
        return same_object(candidate, kept, gap, pad)
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    by_frame = {i: _box(point, pad) for i, point in long}
    coverage = sum(any(j in by_frame and _touch(_box(point, pad), by_frame[j])
                       for j in range(i - gap, i + gap + 1)) for i, point in short)
    return coverage / len(short) >= share


def stationary_match(candidate, kept, segments):
    """Compare only stable triplets inside an explicitly marked stopped interval."""
    a, b = candidate["representative_frame"], kept["representative_frame"]
    if not any(first <= min(a, b) <= max(a, b) < end for first, end in segments):
        return False
    box_a, box_b = _box(candidate["representative"]), _box(kept["representative"])
    reference = box_b

    def iou(box):
        x0, y0, x1, y1 = box
        a0, b0, a1, b1 = reference
        intersection = max(0, min(x1, a1) - max(x0, a0) + 1) * max(
            0, min(y1, b1) - max(y0, b0) + 1
        )
        return intersection / ((x1-x0+1)*(y1-y0+1)+(a1-a0+1)*(b1-b0+1)-intersection)

    if iou(box_a) < 0.5:
        return False
    for row in (candidate, kept):
        near = [_box(point) for frame, point in row["points"]
                if abs(frame - row["representative_frame"]) <= 3]
        if len(near) < 3 or sum(iou(box) >= 0.5 for box in near) / len(near) < 2 / 3:
            return False
    return True

"""Experimental CPU percentile filter for temperature grids with few distinct values."""

from __future__ import annotations

import cv2
import numpy as np
from scipy.ndimage import percentile_filter


def local_percentile(grid, *, percentile, size):
    # Bound the number of full-grid box sums on unusually diverse frames.
    if grid.ndim != 2 or grid.dtype != np.float32 or not 0 <= percentile < 100:
        return percentile_filter(grid, percentile=percentile, size=size)
    values = np.unique(grid)
    if values.size > 1024 or not np.isfinite(values).all():
        return percentile_filter(grid, percentile=percentile, size=size)

    rank = int(size * size * percentile / 100)
    # OpenCV's comparison returns 255 per match. The first threshold with
    # rank + 1 matches is exactly the window's value at the requested rank.
    minimum_sum = (rank + 1) * 255
    background = np.empty_like(grid)
    pending = np.ones(grid.shape, dtype=bool)
    for value in values:
        below = cv2.compare(grid, float(value), cv2.CMP_LE)
        counts = cv2.boxFilter(below, cv2.CV_32S, (size, size), normalize=False,
                               borderType=cv2.BORDER_REFLECT)
        resolved = pending & (counts >= minimum_sum)
        background[resolved] = value
        pending[resolved] = False
        if not pending.any():
            break
    return background

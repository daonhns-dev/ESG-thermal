"""Connected-component extraction for thermal review candidates."""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.ndimage import label

from .anomaly import circularity, edge_sharpness


def extract_candidates(
    grid_c: np.ndarray,
    score_map: np.ndarray,
    score_thresh: float = 3.0,
    min_area: int = 20,
    max_area_frac: float = 0.25,
    offset_y: int = 0,
    offset_x: int = 0,
) -> list[dict[str, Any]]:
    """Return ranked-input features, not final defect decisions."""
    mask = score_map > score_thresh
    labeled, label_count = label(mask)
    max_area = grid_c.size * max_area_frac
    rows: list[dict[str, Any]] = []

    for component_id in range(1, label_count + 1):
        component_mask = labeled == component_id
        area = int(component_mask.sum())
        if area < min_area or area > max_area:
            continue

        ys, xs = np.where(component_mask)
        temperatures = grid_c[component_mask]
        scores = score_map[component_mask]
        rows.append(
            {
                "bbox_x0": int(xs.min()) + offset_x,
                "bbox_y0": int(ys.min()) + offset_y,
                "bbox_x1": int(xs.max()) + offset_x,
                "bbox_y1": int(ys.max()) + offset_y,
                "area_px": area,
                "mean_temp_c": float(temperatures.mean()),
                "peak_temp_c": float(temperatures.max()),
                "mean_score": float(scores.mean()),
                "max_score": float(scores.max()),
                "mean_z": float(scores.mean()),
                "max_z": float(scores.max()),
                "edge_sharpness": edge_sharpness(grid_c, component_mask),
                "circularity": circularity(component_mask),
            }
        )

    return rows


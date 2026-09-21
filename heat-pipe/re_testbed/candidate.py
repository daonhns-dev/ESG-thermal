"""Candidate extraction utilities for ROI thermal anomaly review."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from scipy.ndimage import label

PROJECT_ROOT = Path(__file__).resolve().parent.parent
import sys

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.thermal_anomaly import circularity, edge_sharpness  # noqa: E402


def extract_candidates(
    grid_c: np.ndarray,
    z_map: np.ndarray,
    z_thresh: float = 3.0,
    min_area: int = 20,
    max_area_frac: float = 0.25,
    offset_y: int = 0,
    offset_x: int = 0,
) -> list[dict[str, Any]]:
    """Extract connected thermal-anomaly candidates from a z-score map.

    Returns candidate dictionaries intended for ranking and review, not final
    binary decisions.
    """
    mask = z_map > z_thresh
    labeled, n_labels = label(mask)
    max_area = grid_c.size * max_area_frac
    rows: list[dict[str, Any]] = []

    for comp_id in range(1, n_labels + 1):
        comp_mask = labeled == comp_id
        area = int(comp_mask.sum())
        if area < min_area or area > max_area:
            continue

        ys, xs = np.where(comp_mask)
        values = grid_c[comp_mask]
        z_values = z_map[comp_mask]
        rows.append(
            {
                "bbox_x0": int(xs.min()) + offset_x,
                "bbox_y0": int(ys.min()) + offset_y,
                "bbox_x1": int(xs.max()) + offset_x,
                "bbox_y1": int(ys.max()) + offset_y,
                "area_px": area,
                "mean_temp_c": float(values.mean()),
                "peak_temp_c": float(values.max()),
                "mean_score": float(z_values.mean()),
                "max_score": float(z_values.max()),
                "mean_z": float(z_values.mean()),
                "max_z": float(z_values.max()),
                "edge_sharpness": edge_sharpness(grid_c, comp_mask),
                "circularity": circularity(comp_mask),
            }
        )

    return rows

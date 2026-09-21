"""ROI helpers for re_testbed review pipelines."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from re_testbed.background import robust_z_score_map

Roi = tuple[int, int, int, int]


@dataclass(frozen=True)
class ReviewZMap:
    """Z-score result for a user review ROI."""

    z_full: np.ndarray
    z_review: np.ndarray
    analysis_roi: Roi
    review_roi: Roi
    requested_roi: Roi


def default_analysis_roi(shape: tuple[int, int], top_frac: float) -> Roi:
    """Return the lower-frame road-area ROI used for z-score background statistics."""
    height, width = shape
    return 0, int(height * top_frac), width, height


def grid_roi(shape: tuple[int, int], top_frac: float, count: int, index: int) -> Roi:
    """Split the lower frame into equal vertical ROIs and return one 0-based cell."""
    if count <= 0:
        raise ValueError("roi grid count must be positive")
    if index < 0 or index >= count:
        raise ValueError(f"roi grid index must be in [0, {count - 1}]")
    x0, y0, x1, y1 = default_analysis_roi(shape, top_frac)
    width = x1 - x0
    cell_x0 = x0 + int(round(width * index / count))
    cell_x1 = x0 + int(round(width * (index + 1) / count))
    return cell_x0, y0, cell_x1, y1


def clip_roi(shape: tuple[int, int], roi: Roi) -> Roi:
    """Clip an x0,y0,x1,y1 ROI to frame bounds."""
    height, width = shape
    x0, y0, x1, y1 = roi
    x0 = max(0, min(width - 1, x0))
    x1 = max(x0 + 1, min(width, x1))
    y0 = max(0, min(height - 1, y0))
    y1 = max(y0 + 1, min(height, y1))
    return x0, y0, x1, y1


def intersect_roi(a: Roi, b: Roi) -> Roi | None:
    """Return the intersection of two ROIs, or None when they do not overlap."""
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def crop(arr: np.ndarray, roi: Roi) -> np.ndarray:
    """Crop an array by x0,y0,x1,y1 coordinates."""
    x0, y0, x1, y1 = roi
    return arr[y0:y1, x0:x1]


def compute_review_z_map(
    frame_c: np.ndarray,
    requested_roi: Roi | None,
    top_frac: float,
    method: str,
    param: float,
    analysis_mode: str = "roi",
) -> ReviewZMap:
    """Compute z-score for the selected review ROI.

    `roi` mode matches the prototype flow: the user-selected ROI is the
    thermal-pipe review area and z-score is computed inside that ROI.

    `road` mode is kept for diagnostics/comparison: z-score is computed over
    the lower-frame road area, then cropped to the user ROI.
    """
    road_roi = default_analysis_roi(frame_c.shape, top_frac)
    requested = clip_roi(frame_c.shape, requested_roi or road_roi)
    mode = analysis_mode.lower()

    if mode == "roi":
        analysis_roi = requested
        review_roi = requested
    elif mode == "road":
        analysis_roi = road_roi
        review_roi = intersect_roi(requested, analysis_roi)
        if review_roi is None:
            raise ValueError(
                f"ROI {requested} does not overlap the z-score analysis ROI {analysis_roi}. "
                "Choose an ROI in the lower road area or lower --roi-top-frac."
            )
    else:
        raise ValueError(f"unknown analysis mode: {analysis_mode}")

    z_full = np.zeros_like(frame_c, dtype=np.float32)
    ax0, ay0, ax1, ay1 = analysis_roi
    z_full[ay0:ay1, ax0:ax1] = robust_z_score_map(
        frame_c[ay0:ay1, ax0:ax1],
        method,
        param,
    ).astype(np.float32)
    return ReviewZMap(
        z_full=z_full,
        z_review=crop(z_full, review_roi),
        analysis_roi=analysis_roi,
        review_roi=review_roi,
        requested_roi=requested,
    )

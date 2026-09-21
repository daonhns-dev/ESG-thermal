"""Shared thermal anomaly helper functions.

These functions are intentionally small and dependency-light so experiment
scripts and `re_testbed` can reuse the same robust z-score and shape-feature
logic without importing one-off CLI scripts.
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import binary_erosion, gaussian_filter, grey_opening, median_filter


def compute_background(grid: np.ndarray, method: str = "gaussian", param: float = 45) -> np.ndarray:
    """Estimate the expected thermal background for a temperature grid."""
    if method == "gaussian":
        return gaussian_filter(grid, sigma=float(param))
    if method == "median":
        size = int(param)
        if size % 2 == 0:
            size += 1
        return median_filter(grid, size=size)
    if method == "top_hat":
        size = int(param)
        if size % 2 == 0:
            size += 1
        return grey_opening(grid, size=(size, size))
    raise ValueError(f"unknown background method: {method}")


def robust_z_score_map(grid: np.ndarray, method: str = "gaussian", param: float = 45) -> np.ndarray:
    """Compute robust z-score of local residuals against an estimated background."""
    background = compute_background(grid, method, param)
    residual = grid - background
    med = np.median(residual)
    mad = np.median(np.abs(residual - med)) * 1.4826 + 1e-6
    return (residual - med) / mad


def edge_sharpness(grid: np.ndarray, mask: np.ndarray) -> float:
    """Mean thermal gradient magnitude on the boundary of a candidate mask."""
    if mask.sum() == 0:
        return 0.0
    eroded = binary_erosion(mask)
    boundary = mask & ~eroded
    if boundary.sum() == 0:
        return 0.0
    gy, gx = np.gradient(grid)
    grad_mag = np.sqrt(gy**2 + gx**2)
    return float(grad_mag[boundary].mean())


def circularity(mask: np.ndarray) -> float:
    """Return 4*pi*area/perimeter^2 for a binary mask."""
    area = mask.sum()
    if area == 0:
        return 0.0
    eroded = binary_erosion(mask)
    boundary = mask & ~eroded
    perimeter = boundary.sum()
    if perimeter == 0:
        return 0.0
    return float(4 * np.pi * area / (perimeter**2))

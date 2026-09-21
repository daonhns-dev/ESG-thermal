"""Map-based thermal anomaly scoring algorithms for comparison."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from scipy.ndimage import grey_opening, percentile_filter

from utils.thermal_anomaly import robust_z_score_map


def robust_zscore(grid_c: np.ndarray, method: str = "gaussian", param: float = 45) -> np.ndarray:
    """Baseline robust z-score map."""
    return robust_z_score_map(grid_c, method, param).astype(np.float32)


def top_hat_zscore(grid_c: np.ndarray, size: int = 31) -> np.ndarray:
    """Robust z-score of a morphological white top-hat residual."""
    size = _odd(size)
    background = grey_opening(grid_c, size=(size, size))
    return _robust_scale(grid_c - background)


def multiscale_top_hat(grid_c: np.ndarray, sizes: Sequence[int] = (15, 31, 61)) -> np.ndarray:
    """Maximum robust top-hat response across several spatial scales."""
    maps = [top_hat_zscore(grid_c, size) for size in sizes]
    return np.maximum.reduce(maps).astype(np.float32)


def percentile_contrast(grid_c: np.ndarray, size: int = 31, percentile: float = 40) -> np.ndarray:
    """Local temperature contrast against a low-percentile neighborhood baseline."""
    size = _odd(size)
    background = percentile_filter(grid_c, percentile=percentile, size=size)
    return _robust_scale(grid_c - background)


def score_map(
    grid_c: np.ndarray,
    algorithm: str,
    bg_method: str = "gaussian",
    bg_param: float = 45,
    top_hat_sizes: Sequence[int] = (15, 31, 61),
    percentile_size: int = 31,
    percentile: float = 40,
) -> np.ndarray:
    """Dispatch a named algorithm to a score map."""
    if algorithm == "robust_zscore":
        return robust_zscore(grid_c, bg_method, bg_param)
    if algorithm == "top_hat_zscore":
        size = int(top_hat_sizes[1] if len(top_hat_sizes) > 1 else top_hat_sizes[0])
        return top_hat_zscore(grid_c, size)
    if algorithm == "multiscale_top_hat":
        return multiscale_top_hat(grid_c, top_hat_sizes)
    if algorithm == "percentile_contrast":
        return percentile_contrast(grid_c, percentile_size, percentile)
    raise ValueError(f"unknown algorithm: {algorithm}")


def _robust_scale(residual: np.ndarray) -> np.ndarray:
    med = np.median(residual)
    mad = np.median(np.abs(residual - med)) * 1.4826 + 1e-6
    return ((residual - med) / mad).astype(np.float32)


def _odd(value: int) -> int:
    value = max(3, int(value))
    return value + 1 if value % 2 == 0 else value

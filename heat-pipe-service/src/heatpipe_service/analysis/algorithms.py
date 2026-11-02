"""Map-based thermal anomaly scoring algorithms."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from scipy.ndimage import grey_opening, percentile_filter

from .anomaly import robust_z_score_map

SUPPORTED_ALGORITHMS = (
    "robust_zscore",
    "top_hat_zscore",
    "multiscale_top_hat",
    "percentile_contrast",
)
V0_CANDIDATE_ALGORITHMS = (
    "robust_zscore",
    "multiscale_top_hat",
    "percentile_contrast",
)


def robust_zscore(grid_c: np.ndarray, method: str = "gaussian", param: float = 45) -> np.ndarray:
    return robust_z_score_map(grid_c, method, param).astype(np.float32)


def top_hat_zscore(grid_c: np.ndarray, size: int = 31) -> np.ndarray:
    size = _odd(size)
    background = grey_opening(grid_c, size=(size, size))
    return _robust_scale(grid_c - background)


def multiscale_top_hat(grid_c: np.ndarray, sizes: Sequence[int] = (15, 31, 61)) -> np.ndarray:
    maps = [top_hat_zscore(grid_c, size) for size in sizes]
    return np.maximum.reduce(maps).astype(np.float32)


def percentile_contrast(grid_c: np.ndarray, size: int = 31, percentile: float = 40) -> np.ndarray:
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


def score_maps(grid_c: np.ndarray, algorithms: Sequence[str], device: str = "cpu") -> list[np.ndarray]:
    """Score one frame on the selected device and return CPU arrays for extraction."""
    if device == "cpu":
        return [score_map(grid_c, algorithm) for algorithm in algorithms]
    if device == "cuda":
        from .gpu import cuda_score_maps

        return cuda_score_maps(grid_c, algorithms)
    raise ValueError(f"unknown analysis device: {device}")


def _robust_scale(residual: np.ndarray) -> np.ndarray:
    median = np.median(residual)
    mad = np.median(np.abs(residual - median)) * 1.4826 + 1e-6
    return ((residual - median) / mad).astype(np.float32)


def _odd(value: int) -> int:
    value = max(3, int(value))
    return value + 1 if value % 2 == 0 else value


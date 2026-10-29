"""Thermal background, robust score, and candidate-shape helpers."""

from __future__ import annotations

import numpy as np
from scipy.ndimage import binary_erosion, gaussian_filter, grey_opening, median_filter


def compute_background(grid: np.ndarray, method: str = "gaussian", param: float = 45) -> np.ndarray:
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
    background = compute_background(grid, method, param)
    residual = grid - background
    median = np.median(residual)
    mad = np.median(np.abs(residual - median)) * 1.4826 + 1e-6
    return (residual - median) / mad


def edge_sharpness(grid: np.ndarray, mask: np.ndarray) -> float:
    if mask.sum() == 0:
        return 0.0
    boundary = mask & ~binary_erosion(mask)
    if boundary.sum() == 0:
        return 0.0
    grad_y, grad_x = np.gradient(grid)
    magnitude = np.sqrt(grad_y**2 + grad_x**2)
    return float(magnitude[boundary].mean())


def circularity(mask: np.ndarray) -> float:
    area = mask.sum()
    if area == 0:
        return 0.0
    boundary = mask & ~binary_erosion(mask)
    perimeter = boundary.sum()
    if perimeter == 0:
        return 0.0
    return float(4 * np.pi * area / (perimeter**2))


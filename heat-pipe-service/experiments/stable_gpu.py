"""Experimental CUDA score maps with a nonzero-MAD fallback."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def score_maps(grid_c: np.ndarray, algorithms: Sequence[str]) -> tuple[list[np.ndarray], int]:
    import cupy as cp
    from cupyx.scipy import ndimage as cndi

    grid = cp.asarray(grid_c, dtype=cp.float32)
    fallback_count = 0

    def scale(residual):
        nonlocal fallback_count
        median = cp.median(residual)
        deviations = cp.abs(residual - median)
        mad = float((cp.median(deviations) * 1.4826).get())
        if mad <= 1e-5:
            nonzero = deviations[deviations > 0.005]
            mad = (
                max(0.05, float(cp.median(nonzero).get()) * 1.4826)
                if nonzero.size else 0.05
            )
            fallback_count += 1
        return ((residual - median) / (mad + 1e-6)).astype(cp.float32)

    def top_hat(size: int):
        return scale(grid - cndi.grey_opening(grid, size=(size, size)))

    output = []
    for algorithm in algorithms:
        if algorithm == "robust_zscore":
            scores = scale(grid - cndi.gaussian_filter(grid, sigma=45.0))
        elif algorithm == "multiscale_top_hat":
            maps = [top_hat(size) for size in (15, 31, 61)]
            scores = cp.maximum(cp.maximum(maps[0], maps[1]), maps[2])
        elif algorithm == "percentile_contrast":
            scores = scale(grid - cndi.percentile_filter(grid, percentile=40, size=31))
        else:
            raise ValueError(f"unsupported experimental algorithm: {algorithm}")
        output.append(cp.asnumpy(scores).astype(np.float32, copy=False))
    return output, fallback_count

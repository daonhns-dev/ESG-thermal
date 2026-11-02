"""Optional CUDA score maps; candidate extraction and tracking remain on CPU."""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache

import numpy as np


@lru_cache(maxsize=1)
def require_cuda() -> None:
    try:
        import cupy as cp
    except ImportError as exc:
        raise RuntimeError("CUDA analysis requires the optional gpu dependencies") from exc
    try:
        if cp.cuda.runtime.getDeviceCount() < 1:
            raise RuntimeError("CUDA analysis requested, but no CUDA GPU is available")
    except cp.cuda.runtime.CUDARuntimeError as exc:
        raise RuntimeError("CUDA analysis requested, but the CUDA driver is unavailable") from exc


def cuda_score_maps(grid_c: np.ndarray, algorithms: Sequence[str]) -> list[np.ndarray]:
    require_cuda()
    import cupy as cp
    from cupyx.scipy import ndimage as cndi

    grid = cp.asarray(grid_c, dtype=cp.float32)

    def scale(residual):
        median = cp.median(residual)
        mad = cp.median(cp.abs(residual - median)) * 1.4826 + 1e-6
        return ((residual - median) / mad).astype(cp.float32)

    def top_hat(size: int):
        background = cndi.grey_opening(grid, size=(size, size))
        return scale(grid - background)

    output = []
    for algorithm in algorithms:
        if algorithm == "robust_zscore":
            scores = scale(grid - cndi.gaussian_filter(grid, sigma=45.0))
        elif algorithm == "top_hat_zscore":
            scores = top_hat(31)
        elif algorithm == "multiscale_top_hat":
            maps = [top_hat(size) for size in (15, 31, 61)]
            scores = cp.maximum(cp.maximum(maps[0], maps[1]), maps[2])
        elif algorithm == "percentile_contrast":
            scores = scale(grid - cndi.percentile_filter(grid, percentile=40, size=31))
        else:
            raise ValueError(f"unknown algorithm: {algorithm}")
        output.append(cp.asnumpy(scores).astype(np.float32, copy=False))
    return output

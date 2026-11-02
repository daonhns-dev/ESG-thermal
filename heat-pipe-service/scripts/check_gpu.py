"""Compare CPU and CUDA score maps on a representative thermal ROI."""

from __future__ import annotations

import json
import time

import numpy as np

from heatpipe_service.analysis.algorithms import SUPPORTED_ALGORITHMS, score_maps


def main() -> None:
    rng = np.random.default_rng(20261102)
    grid = (30 + rng.normal(0, 0.08, (144, 128))).astype(np.float32)
    yy, xx = np.ogrid[:144, :128]
    grid[(xx - 64) ** 2 + (yy - 75) ** 2 <= 6**2] += 5

    cpu = score_maps(grid, SUPPORTED_ALGORITHMS, "cpu")
    started = time.perf_counter()
    cuda = score_maps(grid, SUPPORTED_ALGORITHMS, "cuda")
    first_cuda_seconds = round(time.perf_counter() - started, 3)
    comparisons = {}
    for name, a, b in zip(SUPPORTED_ALGORITHMS, cpu, cuda, strict=True):
        comparisons[name] = {
            "maximum_absolute_difference": round(float(np.max(np.abs(a - b))), 6),
            "threshold_mask_equal": bool(np.array_equal(a > 3, b > 3)),
            "finite": bool(np.isfinite(b).all()),
        }

    score_maps(grid, ("robust_zscore",), "cuda")  # Warm-up.
    started = time.perf_counter()
    for _ in range(20):
        score_maps(grid, ("robust_zscore",), "cpu")
    cpu_seconds = time.perf_counter() - started
    started = time.perf_counter()
    for _ in range(20):
        score_maps(grid, ("robust_zscore",), "cuda")
    cuda_seconds = time.perf_counter() - started
    print(json.dumps({
        "first_cuda_seconds": first_cuda_seconds,
        "algorithms": comparisons,
        "robust_zscore_20_frames_cpu_seconds": round(cpu_seconds, 3),
        "robust_zscore_20_frames_cuda_seconds": round(cuda_seconds, 3),
    }, ensure_ascii=False))
    assert all(
        row["finite"] and row["threshold_mask_equal"]
        and row["maximum_absolute_difference"] <= 0.01
        for row in comparisons.values()
    )


if __name__ == "__main__":
    main()

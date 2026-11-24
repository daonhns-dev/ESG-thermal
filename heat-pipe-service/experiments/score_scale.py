"""Calibrate detector scores after extraction and before candidate merging."""

from __future__ import annotations

from collections import deque

import numpy as np


HISTORY_FRAMES = 20


def fit_gains(components, algorithms, threshold, quantile=0.90, min_samples=20):
    """Fit a common upper-tail scale on accepted components from a reference window."""
    samples = {algorithm: [] for algorithm in algorithms}
    for rows in components.values():
        for row in rows:
            samples[row["source_algorithm"]].append(row["max_score"] - threshold)
    counts = {algorithm: len(values) for algorithm, values in samples.items()}
    excess = {algorithm: float(np.quantile(values, quantile))
              for algorithm, values in samples.items() if len(values) >= min_samples}
    supported = {algorithm: value for algorithm, value in excess.items()
                 if np.isfinite(value) and value > 1e-6}
    reference = float(np.median(list(supported.values()))) if len(supported) >= 2 else None
    gains = {algorithm: 1.0 for algorithm in algorithms}
    if reference is not None:
        for algorithm, value in supported.items():
            gains[algorithm] = float(np.clip(reference / value, 0.25, 4.0))
    return gains, {"sample_count": counts, "peak_excess_quantile": excess,
                   "reference_excess": reference, "quantile": quantile,
                   "min_samples": min_samples}


def rescale_candidate(row, gains, threshold):
    """Preserve the component and rescale both aggregate score aliases."""
    calibrated = dict(row)
    gain = gains[row["source_algorithm"]]
    for score, alias in (("max_score", "max_z"), ("mean_score", "mean_z")):
        value = threshold + gain * (row[score] - threshold)
        calibrated[score] = calibrated[alias] = value
    return calibrated


class RecentScoreCalibration:
    """Calibrate each frame with raw components from preceding frames only."""

    def __init__(self, algorithms, threshold, frame_start):
        self.algorithms = algorithms
        self.threshold = threshold
        self.frame_start = frame_start
        self.history = deque()

    def apply(self, frame, rows):
        while self.history and self.history[0][0] < frame - HISTORY_FRAMES:
            self.history.popleft()
        gains = dict.fromkeys(self.algorithms, 1.0)
        if frame - self.frame_start >= HISTORY_FRAMES:
            fitted, _ = fit_gains(dict(self.history), self.algorithms, self.threshold)
            gains = {name: float(np.sqrt(gain)) for name, gain in fitted.items()}
        self.history.append((frame, [
            {"source_algorithm": row["source_algorithm"], "max_score": row["max_score"]}
            for row in rows
        ]))
        return [rescale_candidate(row, gains, self.threshold) for row in rows], gains

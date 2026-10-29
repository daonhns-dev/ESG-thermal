from __future__ import annotations

import numpy as np

from heatpipe_service.analysis.algorithms import V0_CANDIDATE_ALGORITHMS, score_map
from heatpipe_service.analysis.candidates import extract_candidates
from heatpipe_service.analysis.ranking import rank_candidates
from heatpipe_service.analysis.roi import grid_roi
from heatpipe_service.analysis.tracking import build_tracks, summarize_track


def synthetic_frame() -> np.ndarray:
    rng = np.random.default_rng(42)
    frame = 30 + rng.normal(0, 0.08, (128, 160))
    yy, xx = np.ogrid[:128, :160]
    frame[(xx - 80) ** 2 + (yy - 96) ** 2 <= 8**2] += 5
    return frame.astype(np.float32)


def test_v0_algorithms_extract_and_rank_candidates() -> None:
    frame = synthetic_frame()
    for algorithm in V0_CANDIDATE_ALGORITHMS:
        scores = score_map(frame, algorithm)
        candidates = extract_candidates(frame, scores, score_thresh=3, min_area=20)
        assert candidates
        ranked = rank_candidates(candidates)
        assert ranked[0]["review_score"] >= ranked[-1]["review_score"]


def test_tracking_and_roi_helpers() -> None:
    candidate = {
        "bbox_x0": 10,
        "bbox_y0": 90,
        "bbox_x1": 20,
        "bbox_y1": 100,
        "area_px": 100,
        "circularity": 0.8,
        "peak_temp_c": 35.0,
        "max_score": 5.0,
        "max_z": 5.0,
    }
    by_frame = {
        0: [candidate],
        1: [{**candidate, "bbox_y0": 94, "bbox_y1": 104, "area_px": 120}],
        2: [{**candidate, "bbox_y0": 102, "bbox_y1": 127, "area_px": 180}],
    }
    tracks = build_tracks(by_frame, max_dist=25, max_gap=1)
    assert len(tracks) == 1
    summary = summarize_track(tracks[0], 0, 128, 1, 1.3, 0.35)
    assert summary["n_frames"] == 3
    assert summary["likely_ground_fixed"] is True
    assert grid_roi((288, 384), 0.55, 4, 1) == (96, 158, 192, 288)


from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from heatpipe_service.analysis import algorithms as service_algorithms
from heatpipe_service.analysis.candidates import extract_candidates as service_extract_candidates
from heatpipe_service.analysis.ranking import rank_candidates as service_rank_candidates
from heatpipe_service.analysis.roi import compute_review_z_map as service_compute_review_z_map

POC_ROOT = Path(__file__).resolve().parents[3] / "heat-pipe"
sys.path.insert(0, str(POC_ROOT))

from re_testbed.algorithms import score_map as poc_score_map  # noqa: E402
from re_testbed.candidate import extract_candidates as poc_extract_candidates  # noqa: E402
from re_testbed.roi import compute_review_z_map as poc_compute_review_z_map  # noqa: E402
from re_testbed.score_and_rank import rank_candidates as poc_rank_candidates  # noqa: E402


def synthetic_grid() -> np.ndarray:
    rng = np.random.default_rng(20261029)
    grid = 30 + rng.normal(0, 0.12, (128, 160))
    yy, xx = np.ogrid[:128, :160]
    grid[(xx - 65) ** 2 + (yy - 91) ** 2 <= 7**2] += 4.5
    grid[(xx - 112) ** 2 + (yy - 102) ** 2 <= 5**2] += 2.8
    return grid.astype(np.float32)


def test_score_maps_match_poc() -> None:
    grid = synthetic_grid()
    for algorithm in service_algorithms.SUPPORTED_ALGORITHMS:
        expected = poc_score_map(grid, algorithm)
        actual = service_algorithms.score_map(grid, algorithm)
        np.testing.assert_allclose(actual, expected, rtol=0, atol=0)


def test_candidate_and_ranking_outputs_match_poc() -> None:
    grid = synthetic_grid()
    scores = service_algorithms.score_map(grid, "multiscale_top_hat")
    expected = poc_extract_candidates(grid, scores, z_thresh=3, min_area=20)
    actual = service_extract_candidates(grid, scores, score_thresh=3, min_area=20)
    assert actual == expected
    assert service_rank_candidates(actual) == poc_rank_candidates(expected)


def test_roi_outputs_match_poc() -> None:
    grid = synthetic_grid()
    roi = (40, 60, 130, 128)
    expected = poc_compute_review_z_map(grid, roi, 0.55, "gaussian", 45, "roi")
    actual = service_compute_review_z_map(grid, roi, 0.55, "gaussian", 45, "roi")
    assert actual.analysis_roi == expected.analysis_roi
    assert actual.review_roi == expected.review_roi
    np.testing.assert_allclose(actual.z_full, expected.z_full, rtol=0, atol=0)


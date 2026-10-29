from __future__ import annotations

import json
import csv

import numpy as np

from re_testbed.algorithms import score_map
from re_testbed.label_candidate_export import read_excluded_case_ids
from re_testbed.single_session_review import build_parser, run_review


def _synthetic_frame(frame_index: int) -> np.ndarray:
    """Return a stable road-temperature frame with one moving hot patch."""
    rng = np.random.default_rng(42 + frame_index)
    frame = 3000 + rng.normal(0, 8, (288, 384))
    yy, xx = np.ogrid[:288, :384]
    hot_patch = (xx - (140 + frame_index * 2)) ** 2 + (yy - 220) ** 2 <= 8**2
    frame[hot_patch] += 500
    return np.clip(frame, 0, 65535).astype(np.uint16)


def test_all_candidate_maps_detect_synthetic_hot_patch() -> None:
    frame_c = _synthetic_frame(0).astype(np.float32) / 100
    road_roi = frame_c[160:288, 80:320]

    for algorithm in (
        "robust_zscore",
        "top_hat_zscore",
        "multiscale_top_hat",
        "percentile_contrast",
    ):
        scores = score_map(road_roi, algorithm)
        assert scores.shape == road_roi.shape
        assert np.isfinite(scores).all()
        assert float(scores.max()) > 3.0


def test_single_session_review_writes_ranked_track_outputs(tmp_path) -> None:
    dataset_root = tmp_path / "dataset"
    thermal_dir = dataset_root / "smoke_session" / "thermal"
    thermal_dir.mkdir(parents=True)
    for frame_index in range(10):
        np.save(thermal_dir / f"{frame_index:06d}.npy", _synthetic_frame(frame_index))

    output_root = tmp_path / "output"
    args = build_parser().parse_args(
        [
            "--dataset-root",
            str(dataset_root),
            "--session",
            "smoke_session",
            "--roi",
            "80,160,320,288",
            "--analysis-mode",
            "roi",
            "--z-thresh",
            "2.0",
            "--min-area",
            "20",
            "--output-dir",
            str(output_root),
        ]
    )

    result = run_review(args)

    assert result["candidate_count"] > 0
    assert result["track_count"] == 1
    assert (output_root / "smoke_session" / "frame_candidates.csv").is_file()
    assert (output_root / "smoke_session" / "tracks.csv").is_file()
    summary_path = output_root / "smoke_session" / "summary.json"
    assert summary_path.is_file()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["frame_paths_considered"] == 10
    assert summary["skipped_zero_frames"] == 0


def test_read_excluded_case_ids_merges_nonempty_ids(tmp_path) -> None:
    paths = []
    for index, rows in enumerate(
        (
            [{"case_id": "C0001"}, {"case_id": "C0002"}],
            [{"case_id": "C0002"}, {"case_id": ""}, {"case_id": "C0003"}],
        )
    ):
        path = tmp_path / f"labels_{index}.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["case_id"])
            writer.writeheader()
            writer.writerows(rows)
        paths.append(path)

    assert read_excluded_case_ids(paths) == {"C0001", "C0002", "C0003"}


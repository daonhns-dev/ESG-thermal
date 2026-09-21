"""Single-session ROI thermal candidate review with frame-to-frame tracking."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np  # noqa: E402

from datasets.att_atg_io import TEMP_SCALE  # noqa: E402
from re_testbed.candidate import extract_candidates  # noqa: E402
from re_testbed.roi import compute_review_z_map, crop, grid_roi  # noqa: E402
from re_testbed.score_and_rank import rank_candidates  # noqa: E402
from re_testbed.single_frame_review import parse_roi  # noqa: E402
from utils.thermal_tracks import build_tracks, summarize_track  # noqa: E402

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "results" / "re_testbed" / "single_session_review"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        if not rows:
            f.write("")
            return
        fieldnames: list[str] = []
        for row in rows:
            for key in row.keys():
                if key not in fieldnames:
                    fieldnames.append(key)
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def iter_frame_paths(thermal_dir: Path, start: int | None, end: int | None, sample_every: int) -> list[Path]:
    paths = sorted(thermal_dir.glob("*.npy"), key=lambda path: int(path.stem))
    selected = []
    for path in paths:
        frame_idx = int(path.stem)
        if start is not None and frame_idx < start:
            continue
        if end is not None and frame_idx > end:
            continue
        if frame_idx % sample_every != 0:
            continue
        selected.append(path)
    return selected


def run_review(args: argparse.Namespace) -> dict[str, Any]:
    dataset_root = Path(args.dataset_root)
    session_dir = dataset_root / args.session
    thermal_dir = session_dir / "thermal"
    if not thermal_dir.exists():
        raise FileNotFoundError(thermal_dir)

    output_dir = Path(args.output_dir) / args.session
    output_dir.mkdir(parents=True, exist_ok=True)

    by_frame: dict[int, list[dict[str, Any]]] = defaultdict(list)
    candidate_rows: list[dict[str, Any]] = []
    frame_height = None
    skipped_zero = 0

    frame_paths = iter_frame_paths(thermal_dir, args.start_frame, args.end_frame, args.sample_every)
    for path in frame_paths:
        frame_idx = int(path.stem)
        raw = np.load(path)
        if not raw.any():
            skipped_zero += 1
            continue

        grid_c = raw.astype(np.float32) / TEMP_SCALE
        frame_height = grid_c.shape[0]
        requested_roi = args.roi
        if args.roi_grid_count is not None:
            requested_roi = grid_roi(grid_c.shape, args.roi_top_frac, args.roi_grid_count, args.roi_grid_index)
        z_result = compute_review_z_map(
            grid_c,
            requested_roi,
            args.roi_top_frac,
            args.bg_method,
            args.bg_param,
            args.analysis_mode,
        )
        x0, y0, x1, y1 = z_result.review_roi
        roi_grid = crop(grid_c, z_result.review_roi)
        z_map = z_result.z_review
        candidates = extract_candidates(
            roi_grid,
            z_map,
            z_thresh=args.z_thresh,
            min_area=args.min_area,
            max_area_frac=args.max_area_frac,
            offset_y=y0,
            offset_x=x0,
        )
        ranked = rank_candidates(candidates)
        for candidate in ranked:
            candidate["session"] = args.session
            candidate["frame_idx"] = frame_idx
            candidate["analysis_roi_xyxy"] = ",".join(str(v) for v in z_result.analysis_roi)
            candidate["review_roi_xyxy"] = ",".join(str(v) for v in z_result.review_roi)
            candidate_rows.append(candidate)
            by_frame[frame_idx].append(candidate)

    tracks = build_tracks(by_frame, args.max_dist, args.max_gap) if by_frame else []
    if frame_height is None:
        frame_height = 0
    track_rows = [
        summarize_track(track, i, frame_height, args.bottom_margin, args.growth_thresh, args.min_circularity)
        for i, track in enumerate(tracks)
    ]
    for row in track_rows:
        row["session"] = args.session
        row["review_score"] = (
            float(row.get("mean_max_z", 0.0))
            + float(row.get("n_frames", 0)) * 0.25
            + (2.0 if row.get("likely_ground_fixed") else 0.0)
        )
    track_rows.sort(key=lambda row: float(row.get("review_score", 0.0)), reverse=True)

    candidate_csv = output_dir / "frame_candidates.csv"
    track_csv = output_dir / "tracks.csv"
    json_path = output_dir / "summary.json"
    write_csv(candidate_csv, candidate_rows)
    write_csv(track_csv, track_rows)

    payload = {
        "mode": "single_session_review",
        "scope_note": (
            "This is a single-session ROI candidate review. It can rank thermal "
            "review targets, but cannot prove whether a fixed heat source is new "
            "or pipe-related without external GIS, labels, or repeated observations."
        ),
        "dataset_root": str(dataset_root),
        "session": args.session,
        "frame_paths_considered": len(frame_paths),
        "skipped_zero_frames": skipped_zero,
        "candidate_count": len(candidate_rows),
        "track_count": len(track_rows),
        "candidate_csv": str(candidate_csv),
        "track_csv": str(track_csv),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["json_path"] = str(json_path)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--roi", type=parse_roi, help="x0,y0,x1,y1 in thermal frame pixels")
    parser.add_argument("--roi-top-frac", type=float, default=0.55, help="default lower-frame ROI top fraction")
    parser.add_argument("--roi-grid-count", type=int, choices=[3, 4], help="split lower frame into 3 or 4 equal ROIs")
    parser.add_argument("--roi-grid-index", type=int, default=0, help="0-based ROI index for --roi-grid-count")
    parser.add_argument("--analysis-mode", default="roi", choices=["roi", "road"])
    parser.add_argument("--start-frame", type=int)
    parser.add_argument("--end-frame", type=int)
    parser.add_argument("--sample-every", type=int, default=1)
    parser.add_argument("--bg-method", default="gaussian", choices=["gaussian", "median", "top_hat"])
    parser.add_argument("--bg-param", type=float, default=45)
    parser.add_argument("--z-thresh", type=float, default=3.0)
    parser.add_argument("--min-area", type=int, default=20)
    parser.add_argument("--max-area-frac", type=float, default=0.25)
    parser.add_argument("--max-dist", type=float, default=25.0)
    parser.add_argument("--max-gap", type=int, default=1)
    parser.add_argument("--bottom-margin", type=int, default=5)
    parser.add_argument("--growth-thresh", type=float, default=1.3)
    parser.add_argument("--min-circularity", type=float, default=0.35)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run_review(args)
    print("Single-session review saved")
    print(f"  candidates: {result['candidate_count']}")
    print(f"  tracks:     {result['track_count']}")
    print(f"  json:       {result['json_path']}")
    print(f"  candidates: {result['candidate_csv']}")
    print(f"  tracks:     {result['track_csv']}")


if __name__ == "__main__":
    main()

"""Compare map-based algorithms at session/track level."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from datasets.att_atg_io import TEMP_SCALE  # noqa: E402
from re_testbed.algorithm_compare import DEFAULT_ALGORITHMS, candidate_rows, resolve_requested_roi, score_for_mode  # noqa: E402
from re_testbed.roi import crop  # noqa: E402
from re_testbed.single_frame_review import parse_roi, z_to_bgr  # noqa: E402
from utils.thermal_tracks import build_tracks, summarize_track  # noqa: E402
from utils.thermal_viz import colorize, imwrite_unicode  # noqa: E402

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "results" / "re_testbed" / "algorithm_track_compare"


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


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        if not rows:
            f.write("")
            return
        fieldnames: list[str] = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def bbox_iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    x0 = max(int(a["bbox_x0"]), int(b["bbox_x0"]))
    y0 = max(int(a["bbox_y0"]), int(b["bbox_y0"]))
    x1 = min(int(a["bbox_x1"]), int(b["bbox_x1"]))
    y1 = min(int(a["bbox_y1"]), int(b["bbox_y1"]))
    inter = max(0, x1 - x0 + 1) * max(0, y1 - y0 + 1)
    if inter == 0:
        return 0.0
    area_a = (int(a["bbox_x1"]) - int(a["bbox_x0"]) + 1) * (int(a["bbox_y1"]) - int(a["bbox_y0"]) + 1)
    area_b = (int(b["bbox_x1"]) - int(b["bbox_x0"]) + 1) * (int(b["bbox_y1"]) - int(b["bbox_y0"]) + 1)
    return inter / max(area_a + area_b - inter, 1)


def pairwise_overlap(rows: list[dict[str, Any]], iou_thresh: float) -> list[dict[str, Any]]:
    by_algorithm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_algorithm[row["algorithm"]].append(row)

    result = []
    for a_name, b_name in combinations(sorted(by_algorithm.keys()), 2):
        a_rows = by_algorithm[a_name]
        b_rows = by_algorithm[b_name]
        matches = 0
        for a in a_rows:
            frame_matches = [b for b in b_rows if b["frame_idx"] == a["frame_idx"]]
            if any(bbox_iou(a, b) >= iou_thresh for b in frame_matches):
                matches += 1
        denom = max(len(a_rows), 1)
        result.append(
            {
                "algorithm_a": a_name,
                "algorithm_b": b_name,
                "a_candidates": len(a_rows),
                "b_candidates": len(b_rows),
                "a_matched_candidates": matches,
                "a_match_rate": round(matches / denom, 3),
                "iou_thresh": iou_thresh,
            }
        )
    return result


def representative_point(track: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Return the frame/candidate pair with the strongest score inside a track."""
    return max(
        track["points"],
        key=lambda point: float(point[1].get("max_score", point[1].get("max_z", 0.0))),
    )


def add_track_scores(row: dict[str, Any], track: dict[str, Any]) -> None:
    """Add temporal persistence and representative-frame fields to a track row."""
    rep_frame, rep_candidate = representative_point(track)
    span = max(1, int(row["end_frame"]) - int(row["start_frame"]) + 1)
    temporal_density = int(row["n_frames"]) / span
    mean_max_score = float(row.get("mean_max_score", row.get("mean_max_z", 0.0)))
    persistence_score = mean_max_score * math.log1p(int(row["n_frames"])) * temporal_density
    candidates = [point[1] for point in track["points"]]
    areas = np.array([float(candidate["area_px"]) for candidate in candidates], dtype=np.float32)
    edge_values = np.array([float(candidate.get("edge_sharpness", 0.0)) for candidate in candidates], dtype=np.float32)
    circularity_values = np.array([float(candidate.get("circularity", 0.0)) for candidate in candidates], dtype=np.float32)
    row["temporal_density"] = round(temporal_density, 3)
    row["persistence_score"] = round(persistence_score, 3)
    row["area_cv"] = round(float(areas.std() / (areas.mean() + 1e-6)), 3)
    row["mean_edge_sharpness"] = round(float(edge_values.mean()), 3)
    row["mean_candidate_circularity"] = round(float(circularity_values.mean()), 3)
    row["representative_frame"] = rep_frame
    row["representative_max_score"] = float(rep_candidate.get("max_score", rep_candidate.get("max_z", 0.0)))
    for key in ("bbox_x0", "bbox_y0", "bbox_x1", "bbox_y1"):
        row[f"representative_{key}"] = rep_candidate[key]


def add_penalty_scores(
    row: dict[str, Any],
    review_roi: tuple[int, int, int, int],
    args: argparse.Namespace,
) -> None:
    """Add soft penalties and an integrated track quality score."""
    rx0, ry0, rx1, ry1 = review_roi
    rw = max(1, rx1 - rx0)
    rh = max(1, ry1 - ry0)
    bx0 = int(row["representative_bbox_x0"])
    by0 = int(row["representative_bbox_y0"])
    bx1 = int(row["representative_bbox_x1"])
    by1 = int(row["representative_bbox_y1"])
    margin = args.boundary_margin

    boundary_touch = bx0 <= rx0 + margin or by0 <= ry0 + margin or bx1 >= rx1 - 1 - margin or by1 >= ry1 - 1 - margin
    top_band = by0 <= ry0 + rh * args.top_band_frac
    area_frac = float(row["area_end"]) / max(1.0, rw * rh)
    area_cv = float(row.get("area_cv", 0.0))
    edge_sharpness = float(row.get("mean_edge_sharpness", 0.0))
    circularity = float(row.get("mean_candidate_circularity", row.get("mean_circularity", 0.0)))

    boundary_penalty = args.boundary_penalty if boundary_touch else 0.0
    top_band_penalty = args.top_band_penalty if top_band else 0.0
    area_penalty = min(args.area_penalty_cap, area_frac * args.area_penalty_scale)
    smoothness_penalty = min(args.smoothness_penalty_cap, area_cv * args.smoothness_penalty_scale)
    edge_penalty = min(args.edge_penalty_cap, edge_sharpness * args.edge_penalty_scale)
    circularity_penalty = max(0.0, args.circularity_target - circularity) * args.circularity_penalty_scale
    total_penalty = boundary_penalty + top_band_penalty + area_penalty + smoothness_penalty + edge_penalty + circularity_penalty

    row["boundary_touch"] = boundary_touch
    row["top_band_touch"] = top_band
    row["representative_area_frac"] = round(area_frac, 4)
    row["boundary_penalty"] = round(boundary_penalty, 3)
    row["top_band_penalty"] = round(top_band_penalty, 3)
    row["area_penalty"] = round(area_penalty, 3)
    row["smoothness_penalty"] = round(smoothness_penalty, 3)
    row["edge_penalty"] = round(edge_penalty, 3)
    row["circularity_penalty"] = round(circularity_penalty, 3)
    row["total_penalty"] = round(total_penalty, 3)
    row["track_quality_score"] = round(float(row["persistence_score"]) - total_penalty, 3)


def load_grid_c(dataset_root: Path, session: str, frame_idx: int) -> np.ndarray:
    raw = np.load(dataset_root / session / "thermal" / f"{frame_idx:06d}.npy")
    return raw.astype(np.float32) / TEMP_SCALE


def draw_bbox(image: np.ndarray, row: dict[str, Any], roi: tuple[int, int, int, int], scale: int) -> None:
    x0, y0, _, _ = roi
    p0 = (
        int((int(row["representative_bbox_x0"]) - x0) * scale),
        int((int(row["representative_bbox_y0"]) - y0) * scale),
    )
    p1 = (
        int((int(row["representative_bbox_x1"]) - x0 + 1) * scale - 1),
        int((int(row["representative_bbox_y1"]) - y0 + 1) * scale - 1),
    )
    cv2.rectangle(image, p0, p1, (0, 0, 255), max(1, scale // 2))


def draw_top_tracks_panel(
    dataset_root: Path,
    args: argparse.Namespace,
    review_roi: tuple[int, int, int, int],
    rows: list[dict[str, Any]],
    output_path: Path,
) -> None:
    """Save a grid of representative frames for top quality tracks."""
    selected: list[dict[str, Any]] = []
    for algorithm in args.algorithms:
        for context_mode in args.context_modes:
            group = [r for r in rows if r["algorithm"] == algorithm and r["context_mode"] == context_mode]
            group.sort(key=lambda r: float(r.get("track_quality_score", r.get("persistence_score", 0.0))), reverse=True)
            selected.extend(group[: args.top_tracks_per_algorithm])

    if not selected:
        return

    scale = args.panel_roi_scale
    label_w = 190
    header_h = 28
    x0, y0, x1, y1 = review_roi
    tile_w = (x1 - x0) * scale
    tile_h = (y1 - y0) * scale
    cell_w = tile_w * 2
    cell_h = header_h + tile_h
    row_h = cell_h
    panel_h = len(args.algorithms) * row_h
    panel_w = label_w + args.top_tracks_per_algorithm * cell_w
    panel = np.zeros((panel_h, panel_w, 3), dtype=np.uint8)
    frame_cache: dict[int, np.ndarray] = {}

    for row_idx, algorithm in enumerate(args.algorithms):
        y = row_idx * row_h
        cv2.putText(panel, algorithm, (8, y + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1, cv2.LINE_AA)
        group = [r for r in selected if r["algorithm"] == algorithm]
        for col_idx, row in enumerate(group[: args.top_tracks_per_algorithm]):
            cell_x = label_w + col_idx * cell_w
            frame_idx = int(row["representative_frame"])
            if frame_idx not in frame_cache:
                frame_cache[frame_idx] = load_grid_c(dataset_root, args.session, frame_idx)
            grid_c = frame_cache[frame_idx]
            score, _ = score_for_mode(grid_c, review_roi, row["context_mode"], row["algorithm"], args)

            roi_grid = crop(grid_c, review_roi)
            lo, hi = np.percentile(roi_grid, [1, 99])
            thermal = colorize(roi_grid, lo, hi)
            score_img = z_to_bgr(score)
            thermal = cv2.resize(thermal, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
            score_img = cv2.resize(score_img, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
            draw_bbox(thermal, row, review_roi, scale)
            draw_bbox(score_img, row, review_roi, scale)

            label = (
                f"track {row['track_id']} f{row['start_frame']}-{row['end_frame']} rep {frame_idx} "
                f"n={row['n_frames']} q={row['track_quality_score']} p={row['persistence_score']}"
            )
            cv2.putText(panel, label, (cell_x + 5, y + 19), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 1, cv2.LINE_AA)
            panel[y + header_h : y + header_h + tile_h, cell_x : cell_x + tile_w] = thermal
            panel[y + header_h : y + header_h + tile_h, cell_x + tile_w : cell_x + tile_w * 2] = score_img
            cv2.line(panel, (cell_x, y), (cell_x, y + row_h - 1), (80, 80, 80), 1)

        cv2.line(panel, (0, y + row_h - 1), (panel.shape[1] - 1, y + row_h - 1), (80, 80, 80), 1)

    imwrite_unicode(output_path, panel, ".png")


def run_compare(args: argparse.Namespace) -> dict[str, Any]:
    dataset_root = Path(args.dataset_root)
    thermal_dir = dataset_root / args.session / "thermal"
    if not thermal_dir.exists():
        raise FileNotFoundError(thermal_dir)

    output_dir = Path(args.output_dir) / args.session
    output_dir.mkdir(parents=True, exist_ok=True)
    frame_paths = iter_frame_paths(thermal_dir, args.start_frame, args.end_frame, args.sample_every)

    by_key_frame: dict[tuple[str, str], dict[int, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    candidate_rows_all: list[dict[str, Any]] = []
    frame_height = 0
    review_roi = None
    skipped_zero = 0

    for path in frame_paths:
        frame_idx = int(path.stem)
        raw = np.load(path)
        if not raw.any():
            skipped_zero += 1
            continue
        grid_c = raw.astype(np.float32) / TEMP_SCALE
        frame_height = grid_c.shape[0]
        if review_roi is None:
            review_roi = resolve_requested_roi(grid_c, args)

        for algorithm in args.algorithms:
            for context_mode in args.context_modes:
                score, analysis_roi = score_for_mode(grid_c, review_roi, context_mode, algorithm, args)
                rows = candidate_rows(grid_c, score, review_roi, algorithm, context_mode, args)
                key = (algorithm, context_mode)
                for row in rows:
                    row["session"] = args.session
                    row["frame_idx"] = frame_idx
                    row["review_roi_xyxy"] = ",".join(str(v) for v in review_roi)
                    row["analysis_roi_xyxy"] = ",".join(str(v) for v in analysis_roi)
                    candidate_rows_all.append(row)
                    by_key_frame[key][frame_idx].append(row)

    track_rows_all: list[dict[str, Any]] = []
    for (algorithm, context_mode), by_frame in by_key_frame.items():
        tracks = build_tracks(by_frame, args.max_dist, args.max_gap)
        for i, track in enumerate(tracks):
            row = summarize_track(track, i, frame_height, args.bottom_margin, args.growth_thresh, args.min_circularity)
            row["algorithm"] = algorithm
            row["context_mode"] = context_mode
            row["session"] = args.session
            add_track_scores(row, track)
            if review_roi is not None:
                add_penalty_scores(row, review_roi, args)
            row["review_score"] = (
                float(row.get("mean_max_score", row.get("mean_max_z", 0.0)))
                + float(row.get("n_frames", 0)) * 0.25
                + (2.0 if row.get("likely_ground_fixed") else 0.0)
            )
            track_rows_all.append(row)

    track_rows_all.sort(
        key=lambda row: (
            row["algorithm"],
            row["context_mode"],
            -float(row.get("track_quality_score", row.get("persistence_score", row["review_score"]))),
        )
    )
    overlap_rows = pairwise_overlap(candidate_rows_all, args.iou_thresh)

    candidate_csv = output_dir / "algorithm_frame_candidates.csv"
    track_csv = output_dir / "algorithm_tracks.csv"
    overlap_csv = output_dir / "algorithm_pairwise_overlap.csv"
    top_tracks_panel = output_dir / "top_tracks_panel.png"
    summary_json = output_dir / "summary.json"
    write_csv(candidate_csv, candidate_rows_all)
    write_csv(track_csv, track_rows_all)
    write_csv(overlap_csv, overlap_rows)
    if review_roi is not None:
        draw_top_tracks_panel(dataset_root, args, review_roi, track_rows_all, top_tracks_panel)

    summary_by_key = []
    for algorithm in args.algorithms:
        for context_mode in args.context_modes:
            candidates = [r for r in candidate_rows_all if r["algorithm"] == algorithm and r["context_mode"] == context_mode]
            tracks = [r for r in track_rows_all if r["algorithm"] == algorithm and r["context_mode"] == context_mode]
            summary_by_key.append(
                {
                    "algorithm": algorithm,
                    "context_mode": context_mode,
                    "candidate_count": len(candidates),
                    "track_count": len(tracks),
                    "top_track_review_score": max([float(t["review_score"]) for t in tracks], default=0.0),
                    "top_track_persistence_score": max([float(t.get("persistence_score", 0.0)) for t in tracks], default=0.0),
                    "top_track_quality_score": max([float(t.get("track_quality_score", 0.0)) for t in tracks], default=0.0),
                    "longest_track_frames": max([int(t["n_frames"]) for t in tracks], default=0),
                    "likely_ground_fixed_tracks": sum(1 for t in tracks if str(t.get("likely_ground_fixed")).lower() == "true"),
                }
            )

    payload = {
        "mode": "algorithm_track_compare",
        "scope_note": (
            "Track-level qualitative comparison. Use this to choose labeling candidates, "
            "not to claim final algorithm superiority without labels."
        ),
        "dataset_root": str(dataset_root),
        "session": args.session,
        "frames_considered": len(frame_paths),
        "skipped_zero_frames": skipped_zero,
        "review_roi": list(review_roi) if review_roi else None,
        "score_thresh": args.score_thresh,
        "algorithms": args.algorithms,
        "context_modes": args.context_modes,
        "summary_by_algorithm": summary_by_key,
        "candidate_csv": str(candidate_csv),
        "track_csv": str(track_csv),
        "overlap_csv": str(overlap_csv),
        "top_tracks_panel": str(top_tracks_panel) if top_tracks_panel.exists() else None,
    }
    summary_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["summary_json"] = str(summary_json)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--roi", type=parse_roi)
    parser.add_argument("--roi-top-frac", type=float, default=0.55)
    parser.add_argument("--roi-grid-count", type=int, choices=[3, 4])
    parser.add_argument("--roi-grid-index", type=int, default=0)
    parser.add_argument("--start-frame", type=int)
    parser.add_argument("--end-frame", type=int)
    parser.add_argument("--sample-every", type=int, default=1)
    parser.add_argument("--algorithms", nargs="+", default=list(DEFAULT_ALGORITHMS), choices=list(DEFAULT_ALGORITHMS))
    parser.add_argument("--context-modes", nargs="+", default=["roi"], choices=["roi", "road"])
    parser.add_argument("--bg-method", default="gaussian", choices=["gaussian", "median", "top_hat"])
    parser.add_argument("--bg-param", type=float, default=45)
    parser.add_argument("--top-hat-sizes", type=int, nargs="+", default=[15, 31, 61])
    parser.add_argument("--percentile-size", type=int, default=31)
    parser.add_argument("--percentile", type=float, default=40)
    parser.add_argument("--score-thresh", type=float, default=3.0)
    parser.add_argument("--min-area", type=int, default=20)
    parser.add_argument("--max-area-frac", type=float, default=0.25)
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--max-dist", type=float, default=25.0)
    parser.add_argument("--max-gap", type=int, default=1)
    parser.add_argument("--bottom-margin", type=int, default=5)
    parser.add_argument("--growth-thresh", type=float, default=1.3)
    parser.add_argument("--min-circularity", type=float, default=0.35)
    parser.add_argument("--iou-thresh", type=float, default=0.3)
    parser.add_argument("--top-tracks-per-algorithm", type=int, default=3)
    parser.add_argument("--panel-roi-scale", type=int, default=2)
    parser.add_argument("--boundary-margin", type=int, default=2)
    parser.add_argument("--top-band-frac", type=float, default=0.12)
    parser.add_argument("--boundary-penalty", type=float, default=5.0)
    parser.add_argument("--top-band-penalty", type=float, default=3.0)
    parser.add_argument("--area-penalty-scale", type=float, default=20.0)
    parser.add_argument("--area-penalty-cap", type=float, default=10.0)
    parser.add_argument("--smoothness-penalty-scale", type=float, default=4.0)
    parser.add_argument("--smoothness-penalty-cap", type=float, default=8.0)
    parser.add_argument("--edge-penalty-scale", type=float, default=1.5)
    parser.add_argument("--edge-penalty-cap", type=float, default=6.0)
    parser.add_argument("--circularity-target", type=float, default=0.35)
    parser.add_argument("--circularity-penalty-scale", type=float, default=6.0)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run_compare(args)
    print("Algorithm track comparison saved")
    print(f"  summary: {result['summary_json']}")
    print(f"  candidates: {result['candidate_csv']}")
    print(f"  tracks: {result['track_csv']}")
    print(f"  overlap: {result['overlap_csv']}")
    if result.get("top_tracks_panel"):
        print(f"  top panel: {result['top_tracks_panel']}")
    for row in result["summary_by_algorithm"]:
        print(
            f"  {row['algorithm']} / {row['context_mode']}: "
            f"candidates={row['candidate_count']}, tracks={row['track_count']}, "
            f"longest={row['longest_track_frames']}, top_score={row['top_track_review_score']:.3f}, "
            f"top_persistence={row['top_track_persistence_score']:.3f}, "
            f"top_quality={row['top_track_quality_score']:.3f}"
        )


if __name__ == "__main__":
    main()

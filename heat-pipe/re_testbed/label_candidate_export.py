"""Export diverse track-level labeling candidates from algorithm comparisons.

This tool merges overlapping algorithm-specific tracks into case-level review
items, then samples a diverse label queue. The output is intended for small
manual labels, not final performance claims.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

TEMP_SCALE = 100.0

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "results" / "re_testbed" / "label_candidates"
LABEL_CHOICES = [
    "vehicle",
    "road_paint",
    "structure_edge",
    "road_surface_hotspot",
    "boundary_artifact",
    "unknown",
]


def read_csv(path: Path) -> list[dict[str, Any]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


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


def as_float(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = row.get(key, "")
    if value in ("", None):
        return default
    try:
        return float(value)
    except ValueError:
        return default


def as_int(row: dict[str, Any], key: str, default: int = 0) -> int:
    value = row.get(key, "")
    if value in ("", None):
        return default
    try:
        return int(float(value))
    except ValueError:
        return default


def as_bool(row: dict[str, Any], key: str) -> bool:
    return str(row.get(key, "")).lower() == "true"


def bbox(row: dict[str, Any]) -> tuple[int, int, int, int]:
    return (
        as_int(row, "representative_bbox_x0"),
        as_int(row, "representative_bbox_y0"),
        as_int(row, "representative_bbox_x1"),
        as_int(row, "representative_bbox_y1"),
    )


def bbox_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    x0 = max(ax0, bx0)
    y0 = max(ay0, by0)
    x1 = min(ax1, bx1)
    y1 = min(ay1, by1)
    inter = max(0, x1 - x0 + 1) * max(0, y1 - y0 + 1)
    if inter == 0:
        return 0.0
    area_a = max(0, ax1 - ax0 + 1) * max(0, ay1 - ay0 + 1)
    area_b = max(0, bx1 - bx0 + 1) * max(0, by1 - by0 + 1)
    return inter / max(area_a + area_b - inter, 1)


def temporal_overlap(a: dict[str, Any], b: dict[str, Any]) -> int:
    return max(0, min(as_int(a, "end_frame"), as_int(b, "end_frame")) - max(as_int(a, "start_frame"), as_int(b, "start_frame")) + 1)


def read_tracks_from_run(run_dir: Path) -> list[dict[str, Any]]:
    tracks_path = run_dir / "algorithm_tracks.csv"
    if not tracks_path.exists():
        raise FileNotFoundError(tracks_path)
    rows = read_csv(tracks_path)
    for row in rows:
        row["source_run_dir"] = str(run_dir)
        row["source_track_key"] = f"{row.get('session')}::{row.get('algorithm')}::{row.get('context_mode')}::{row.get('track_id')}"
    return rows


def same_case(a: dict[str, Any], b: dict[str, Any], iou_thresh: float, max_rep_frame_gap: int) -> bool:
    if a.get("session") != b.get("session"):
        return False
    if temporal_overlap(a, b) <= 0:
        return False
    if abs(as_int(a, "representative_frame") - as_int(b, "representative_frame")) > max_rep_frame_gap:
        return False
    return bbox_iou(bbox(a), bbox(b)) >= iou_thresh


def merge_cases(rows: list[dict[str, Any]], iou_thresh: float, max_rep_frame_gap: int) -> list[list[dict[str, Any]]]:
    rows = sorted(rows, key=lambda row: as_float(row, "track_quality_score"), reverse=True)
    cases: list[list[dict[str, Any]]] = []
    for row in rows:
        matched = False
        for case in cases:
            if any(same_case(row, existing, iou_thresh, max_rep_frame_gap) for existing in case):
                case.append(row)
                matched = True
                break
        if not matched:
            cases.append([row])
    return cases


def best_row(case: list[dict[str, Any]]) -> dict[str, Any]:
    return max(case, key=lambda row: as_float(row, "track_quality_score"))


def summarize_case(case: list[dict[str, Any]], case_id: str) -> dict[str, Any]:
    best = best_row(case)
    algorithms = sorted({row.get("algorithm", "") for row in case})
    contexts = sorted({row.get("context_mode", "") for row in case})
    qualities = {row.get("algorithm", ""): max(as_float(row, "track_quality_score"), as_float(row, "persistence_score")) for row in case}
    start_frame = min(as_int(row, "start_frame") for row in case)
    end_frame = max(as_int(row, "end_frame") for row in case)
    rep_frame = as_int(best, "representative_frame")
    bx0, by0, bx1, by1 = bbox(best)
    return {
        "case_id": case_id,
        "session": best.get("session", ""),
        "start_frame": start_frame,
        "end_frame": end_frame,
        "representative_frame": rep_frame,
        "bbox_x0": bx0,
        "bbox_y0": by0,
        "bbox_x1": bx1,
        "bbox_y1": by1,
        "best_algorithm": best.get("algorithm", ""),
        "best_context_mode": best.get("context_mode", ""),
        "best_track_id": best.get("track_id", ""),
        "best_quality_score": round(as_float(best, "track_quality_score"), 3),
        "best_persistence_score": round(as_float(best, "persistence_score"), 3),
        "best_n_frames": as_int(best, "n_frames"),
        "algorithms_present": "|".join(algorithms),
        "contexts_present": "|".join(contexts),
        "algorithm_count": len(algorithms),
        "robust_quality": round(qualities.get("robust_zscore", math.nan), 3) if "robust_zscore" in qualities else "",
        "top_hat_quality": round(qualities.get("top_hat_zscore", math.nan), 3) if "top_hat_zscore" in qualities else "",
        "multiscale_quality": round(qualities.get("multiscale_top_hat", math.nan), 3) if "multiscale_top_hat" in qualities else "",
        "percentile_quality": round(qualities.get("percentile_contrast", math.nan), 3) if "percentile_contrast" in qualities else "",
        "any_likely_ground_fixed": any(as_bool(row, "likely_ground_fixed") for row in case),
        "any_boundary_touch": any(as_bool(row, "boundary_touch") for row in case),
        "any_top_band_touch": any(as_bool(row, "top_band_touch") for row in case),
        "max_area_frac": round(max(as_float(row, "representative_area_frac") for row in case), 4),
        "source_track_keys": "|".join(sorted(row.get("source_track_key", "") for row in case)),
        "sampling_reason": "",
        "reviewer_label": "",
        "reviewer_note": "",
    }


def add_unique(selected: list[dict[str, Any]], row: dict[str, Any], reason: str, limit: int) -> None:
    if len(selected) >= limit:
        return
    if any(existing["case_id"] == row["case_id"] for existing in selected):
        return
    row = dict(row)
    row["sampling_reason"] = reason
    selected.append(row)


def sample_cases(cases: list[dict[str, Any]], target_count: int, per_bucket: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    ranked = sorted(cases, key=lambda row: float(row["best_quality_score"]), reverse=True)

    buckets = [
        (
            "consensus_top",
            [row for row in ranked if int(row["algorithm_count"]) >= 2],
        ),
        (
            "robust_baseline_top",
            [row for row in ranked if "robust_zscore" in row["algorithms_present"].split("|")],
        ),
        (
            "top_hat_extra",
            [
                row
                for row in ranked
                if "top_hat_zscore" in row["algorithms_present"].split("|")
                and "robust_zscore" not in row["algorithms_present"].split("|")
            ],
        ),
        (
            "sensitive_method_extra",
            [
                row
                for row in ranked
                if "robust_zscore" not in row["algorithms_present"].split("|")
                and "top_hat_zscore" not in row["algorithms_present"].split("|")
            ],
        ),
        (
            "non_boundary_mid_quality",
            [
                row
                for row in ranked
                if not row["any_boundary_touch"]
                and not row["any_top_band_touch"]
                and 0.002 <= float(row["max_area_frac"]) <= 0.08
            ],
        ),
    ]

    for reason, rows in buckets:
        for row in rows[:per_bucket]:
            add_unique(selected, row, reason, target_count)

    if len(selected) < target_count:
        mid_start = max(0, len(ranked) // 3)
        mid_end = max(mid_start + 1, len(ranked) * 2 // 3)
        for row in ranked[mid_start:mid_end]:
            add_unique(selected, row, "middle_score_fill", target_count)

    if len(selected) < target_count:
        for row in ranked:
            add_unique(selected, row, "quality_fill", target_count)

    return selected


def draw_case_panel(dataset_root: Path, rows: list[dict[str, Any]], output_path: Path, pad: int, scale: int) -> None:
    if not rows:
        return
    import cv2
    import numpy as np

    from utils.thermal_viz import colorize, imwrite_unicode

    tiles = []
    for row in rows:
        raw = np.load(dataset_root / row["session"] / "thermal" / f"{int(row['representative_frame']):06d}.npy")
        frame = raw.astype(np.float32) / TEMP_SCALE
        x0 = max(0, int(row["bbox_x0"]) - pad)
        y0 = max(0, int(row["bbox_y0"]) - pad)
        x1 = min(frame.shape[1], int(row["bbox_x1"]) + pad + 1)
        y1 = min(frame.shape[0], int(row["bbox_y1"]) + pad + 1)
        crop = frame[y0:y1, x0:x1]
        lo, hi = np.percentile(crop, [1, 99])
        img = colorize(crop, lo, hi)
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        cv2.rectangle(
            img,
            ((int(row["bbox_x0"]) - x0) * scale, (int(row["bbox_y0"]) - y0) * scale),
            ((int(row["bbox_x1"]) - x0 + 1) * scale - 1, (int(row["bbox_y1"]) - y0 + 1) * scale - 1),
            (0, 0, 255),
            max(1, scale // 2),
        )
        label = f"{row['case_id']} {row['best_algorithm']} q={row['best_quality_score']} {row['sampling_reason']}"
        header = np.zeros((24, img.shape[1], 3), dtype=np.uint8)
        cv2.putText(header, label[:70], (4, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 1, cv2.LINE_AA)
        tiles.append(np.vstack([header, img]))

    cols = 4
    cell_w = max(tile.shape[1] for tile in tiles)
    cell_h = max(tile.shape[0] for tile in tiles)
    rows_n = math.ceil(len(tiles) / cols)
    panel = np.zeros((rows_n * cell_h, cols * cell_w, 3), dtype=np.uint8)
    for idx, tile in enumerate(tiles):
        r = idx // cols
        c = idx % cols
        y = r * cell_h
        x = c * cell_w
        panel[y : y + tile.shape[0], x : x + tile.shape[1]] = tile
    imwrite_unicode(output_path, panel, ".png")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument("--run-dirs", nargs="+", required=True, help="Directories containing algorithm_tracks.csv.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--target-count", type=int, default=50)
    parser.add_argument("--per-bucket", type=int, default=8)
    parser.add_argument("--case-iou-thresh", type=float, default=0.25)
    parser.add_argument("--max-rep-frame-gap", type=int, default=8)
    parser.add_argument("--panel-count", type=int, default=None, help="Number of selected cases to draw. Defaults to target-count.")
    parser.add_argument("--panel-pad", type=int, default=24)
    parser.add_argument("--panel-scale", type=int, default=3)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    panel_count = args.target_count if args.panel_count is None else args.panel_count
    dataset_root = Path(args.dataset_root)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    all_rows: list[dict[str, Any]] = []
    for run_dir in args.run_dirs:
        all_rows.extend(read_tracks_from_run(Path(run_dir)))

    cases = merge_cases(all_rows, args.case_iou_thresh, args.max_rep_frame_gap)
    case_rows = [summarize_case(case, f"C{idx + 1:04d}") for idx, case in enumerate(cases)]
    selected = sample_cases(case_rows, args.target_count, args.per_bucket)

    cases_csv = output_dir / "merged_cases.csv"
    label_csv = output_dir / "label_candidates.csv"
    labels_json = output_dir / "label_schema.json"
    panel_png = output_dir / "label_candidates_panel.png"
    write_csv(cases_csv, case_rows)
    write_csv(label_csv, selected)
    labels_json.write_text(json.dumps({"label_choices": LABEL_CHOICES}, ensure_ascii=False, indent=2), encoding="utf-8")
    if panel_count > 0:
        draw_case_panel(dataset_root, selected[:panel_count], panel_png, args.panel_pad, args.panel_scale)

    likely_count = sum(1 for row in all_rows if as_bool(row, "likely_ground_fixed"))
    print("Label candidate export saved")
    print(f"  input tracks: {len(all_rows)}")
    print(f"  merged cases: {len(case_rows)}")
    print(f"  selected label candidates: {len(selected)}")
    print(f"  likely_ground_fixed tracks: {likely_count}")
    print(f"  merged cases: {cases_csv}")
    print(f"  label candidates: {label_csv}")
    print(f"  label schema: {labels_json}")
    if panel_count > 0:
        print(f"  panel: {panel_png}")


if __name__ == "__main__":
    main()

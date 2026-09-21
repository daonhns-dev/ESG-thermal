"""Compare map-based ROI thermal anomaly algorithms on one frame."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from datasets.att_atg_io import TEMP_SCALE  # noqa: E402
from re_testbed.algorithms import score_map  # noqa: E402
from re_testbed.candidate import extract_candidates  # noqa: E402
from re_testbed.roi import clip_roi, crop, default_analysis_roi, grid_roi, intersect_roi  # noqa: E402
from re_testbed.score_and_rank import rank_candidates  # noqa: E402
from re_testbed.single_frame_review import parse_roi, z_to_bgr  # noqa: E402
from utils.thermal_viz import colorize, imwrite_unicode  # noqa: E402

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "results" / "re_testbed" / "algorithm_compare"
DEFAULT_ALGORITHMS = ("robust_zscore", "top_hat_zscore", "multiscale_top_hat", "percentile_contrast")


def load_frame(dataset_root: Path, session: str, frame_idx: int) -> np.ndarray:
    path = dataset_root / session / "thermal" / f"{frame_idx:06d}.npy"
    if not path.exists():
        raise FileNotFoundError(path)
    raw = np.load(path)
    if not raw.any():
        raise ValueError(f"thermal frame is empty/zero padded: {path}")
    return raw.astype(np.float32) / TEMP_SCALE


def resolve_requested_roi(grid_c: np.ndarray, args: argparse.Namespace) -> tuple[int, int, int, int]:
    if args.roi_grid_count is not None:
        return grid_roi(grid_c.shape, args.roi_top_frac, args.roi_grid_count, args.roi_grid_index)
    if args.roi is not None:
        return clip_roi(grid_c.shape, args.roi)
    return default_analysis_roi(grid_c.shape, args.roi_top_frac)


def score_for_mode(
    grid_c: np.ndarray,
    review_roi: tuple[int, int, int, int],
    mode: str,
    algorithm: str,
    args: argparse.Namespace,
) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    if mode == "roi":
        analysis_roi = review_roi
        score = score_map(
            crop(grid_c, analysis_roi),
            algorithm,
            bg_method=args.bg_method,
            bg_param=args.bg_param,
            top_hat_sizes=args.top_hat_sizes,
            percentile_size=args.percentile_size,
            percentile=args.percentile,
        )
        return score, analysis_roi

    if mode == "road":
        analysis_roi = default_analysis_roi(grid_c.shape, args.roi_top_frac)
        if intersect_roi(review_roi, analysis_roi) is None:
            raise ValueError(f"ROI {review_roi} does not overlap road analysis ROI {analysis_roi}")
        full_score = np.zeros_like(grid_c, dtype=np.float32)
        ax0, ay0, ax1, ay1 = analysis_roi
        full_score[ay0:ay1, ax0:ax1] = score_map(
            crop(grid_c, analysis_roi),
            algorithm,
            bg_method=args.bg_method,
            bg_param=args.bg_param,
            top_hat_sizes=args.top_hat_sizes,
            percentile_size=args.percentile_size,
            percentile=args.percentile,
        )
        return crop(full_score, review_roi), analysis_roi

    raise ValueError(f"unknown context mode: {mode}")


def candidate_rows(
    grid_c: np.ndarray,
    score: np.ndarray,
    review_roi: tuple[int, int, int, int],
    algorithm: str,
    context_mode: str,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    rows = extract_candidates(
        crop(grid_c, review_roi),
        score,
        z_thresh=args.score_thresh,
        min_area=args.min_area,
        max_area_frac=args.max_area_frac,
        offset_x=review_roi[0],
        offset_y=review_roi[1],
    )
    ranked = rank_candidates(rows)[: args.top_k]
    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank
        row["algorithm"] = algorithm
        row["context_mode"] = context_mode
        row["score_thresh"] = args.score_thresh
    return ranked


def draw_panel(
    grid_c: np.ndarray,
    review_roi: tuple[int, int, int, int],
    comparisons: list[dict[str, Any]],
    args: argparse.Namespace,
) -> np.ndarray:
    x0, y0, x1, y1 = review_roi
    roi_grid = crop(grid_c, review_roi)
    roi_lo, roi_hi = np.percentile(roi_grid, [1, 99])
    base = colorize(roi_grid, roi_lo, roi_hi)
    scale = args.panel_roi_scale
    tile_h = base.shape[0] * scale
    tile_w = base.shape[1] * scale
    label_w = 170
    header_h = 30

    by_key = {(item["algorithm"], item["context_mode"]): item for item in comparisons}
    algorithms = list(dict.fromkeys(item["algorithm"] for item in comparisons))
    context_modes = list(dict.fromkeys(item["context_mode"] for item in comparisons))
    columns = [(mode, view) for mode in context_modes for view in ("score", "overlay")]

    def render_tile(comparison: dict[str, Any] | None, view: str) -> np.ndarray:
        if comparison is None:
            return np.zeros((tile_h, tile_w, 3), dtype=np.uint8)
        score = comparison["score"]
        candidates = comparison["candidates"]
        if view == "score":
            image = z_to_bgr(score)
        else:
            image = base.copy()
            image[score > args.score_thresh] = (0, 0, 255)
        resized = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        for rank, candidate in enumerate(candidates, start=1):
            p0 = (int((candidate["bbox_x0"] - x0) * scale), int((candidate["bbox_y0"] - y0) * scale))
            p1 = (
                int((candidate["bbox_x1"] - x0 + 1) * scale - 1),
                int((candidate["bbox_y1"] - y0 + 1) * scale - 1),
            )
            cv2.rectangle(resized, p0, p1, (0, 0, 255), max(1, scale // 2))
            cv2.putText(
                resized,
                str(rank),
                (p0[0], max(12, p0[1] - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
        return resized

    if not algorithms or not columns:
        return np.zeros((200, 400, 3), dtype=np.uint8)

    panel_h = header_h + len(algorithms) * tile_h
    panel_w = label_w + len(columns) * tile_w
    panel = np.zeros((panel_h, panel_w, 3), dtype=np.uint8)

    for col_idx, (mode, view) in enumerate(columns):
        x = label_w + col_idx * tile_w
        title = f"{mode} {view}"
        cv2.putText(panel, title, (x + 6, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

    for row_idx, algorithm in enumerate(algorithms):
        y = header_h + row_idx * tile_h
        cv2.putText(panel, algorithm, (8, y + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        for col_idx, (mode, view) in enumerate(columns):
            x = label_w + col_idx * tile_w
            comparison = by_key.get((algorithm, mode))
            tile = render_tile(comparison, view)
            if comparison is not None and view == "score":
                n = len(comparison["candidates"])
                max_score = comparison["score_stats"]["max"]
                cv2.putText(
                    tile,
                    f"n={n} max={max_score:.2f}",
                    (5, tile_h - 8),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.42,
                    (255, 255, 255),
                    1,
                    cv2.LINE_AA,
                )
            panel[y : y + tile_h, x : x + tile_w] = tile

    for col in range(len(columns) + 1):
        x = label_w + col * tile_w
        cv2.line(panel, (x, 0), (x, panel_h - 1), (80, 80, 80), 1)
    for row in range(len(algorithms) + 1):
        y = header_h + row * tile_h
        cv2.line(panel, (0, y), (panel_w - 1, y), (80, 80, 80), 1)
    return panel


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


def run_compare(args: argparse.Namespace) -> dict[str, Any]:
    grid_c = load_frame(Path(args.dataset_root), args.session, args.frame)
    review_roi = resolve_requested_roi(grid_c, args)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    comparisons: list[dict[str, Any]] = []
    all_candidates: list[dict[str, Any]] = []
    for algorithm in args.algorithms:
        for context_mode in args.context_modes:
            score, analysis_roi = score_for_mode(grid_c, review_roi, context_mode, algorithm, args)
            rows = candidate_rows(grid_c, score, review_roi, algorithm, context_mode, args)
            for row in rows:
                row["session"] = args.session
                row["frame_idx"] = args.frame
                row["review_roi_xyxy"] = ",".join(str(v) for v in review_roi)
                row["analysis_roi_xyxy"] = ",".join(str(v) for v in analysis_roi)
            all_candidates.extend(rows)
            comparisons.append(
                {
                    "algorithm": algorithm,
                    "context_mode": context_mode,
                    "analysis_roi": analysis_roi,
                    "score": score,
                    "score_stats": {
                        "min": float(score.min()),
                        "mean": float(score.mean()),
                        "max": float(score.max()),
                        "pixels_over_thresh": int((score > args.score_thresh).sum()),
                    },
                    "candidates": rows,
                }
            )

    stem = f"{args.session}_{args.frame:06d}"
    panel_path = output_dir / f"{stem}_compare_panel.png"
    json_path = output_dir / f"{stem}_compare.json"
    csv_path = output_dir / f"{stem}_compare_candidates.csv"
    panel = draw_panel(grid_c, review_roi, comparisons, args)
    imwrite_unicode(panel_path, panel, ".png")
    write_csv(csv_path, all_candidates)

    payload = {
        "mode": "algorithm_compare",
        "scope_note": (
            "Exploratory qualitative comparison. These map-based algorithms produce "
            "candidate maps; IF/LOF-style methods should be compared later as candidate rerankers."
        ),
        "dataset_root": args.dataset_root,
        "session": args.session,
        "frame_idx": args.frame,
        "review_roi": {"x0": review_roi[0], "y0": review_roi[1], "x1": review_roi[2], "y1": review_roi[3]},
        "score_thresh": args.score_thresh,
        "algorithms": args.algorithms,
        "context_modes": args.context_modes,
        "comparisons": [
            {
                "algorithm": item["algorithm"],
                "context_mode": item["context_mode"],
                "analysis_roi": {
                    "x0": item["analysis_roi"][0],
                    "y0": item["analysis_roi"][1],
                    "x1": item["analysis_roi"][2],
                    "y1": item["analysis_roi"][3],
                },
                "score_stats": item["score_stats"],
                "candidate_count": len(item["candidates"]),
                "candidates": item["candidates"],
            }
            for item in comparisons
        ],
        "panel_path": str(panel_path),
        "csv_path": str(csv_path),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["json_path"] = str(json_path)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--frame", type=int, required=True)
    parser.add_argument("--roi", type=parse_roi)
    parser.add_argument("--roi-top-frac", type=float, default=0.55)
    parser.add_argument("--roi-grid-count", type=int, choices=[3, 4])
    parser.add_argument("--roi-grid-index", type=int, default=0)
    parser.add_argument("--algorithms", nargs="+", default=list(DEFAULT_ALGORITHMS), choices=list(DEFAULT_ALGORITHMS))
    parser.add_argument("--context-modes", nargs="+", default=["roi", "road"], choices=["roi", "road"])
    parser.add_argument("--bg-method", default="gaussian", choices=["gaussian", "median", "top_hat"])
    parser.add_argument("--bg-param", type=float, default=45)
    parser.add_argument("--top-hat-sizes", type=int, nargs="+", default=[15, 31, 61])
    parser.add_argument("--percentile-size", type=int, default=31)
    parser.add_argument("--percentile", type=float, default=40)
    parser.add_argument("--score-thresh", type=float, default=3.0)
    parser.add_argument("--min-area", type=int, default=20)
    parser.add_argument("--max-area-frac", type=float, default=0.25)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--panel-roi-scale", type=int, default=3)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run_compare(args)
    print("Algorithm comparison saved")
    print(f"  panel: {result['panel_path']}")
    print(f"  json:  {result['json_path']}")
    print(f"  csv:   {result['csv_path']}")
    for item in result["comparisons"]:
        print(f"  {item['algorithm']} / {item['context_mode']}: candidates={item['candidate_count']}, max={item['score_stats']['max']:.3f}")


if __name__ == "__main__":
    main()

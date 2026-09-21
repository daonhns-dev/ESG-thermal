"""Single-frame ROI thermal candidate review.

This is the prototype-aligned entry point: one session, one frame, one reviewer
ROI, and ranked thermal anomaly candidates inside that ROI.
"""

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
from re_testbed.candidate import extract_candidates  # noqa: E402
from re_testbed.roi import compute_review_z_map, crop, grid_roi  # noqa: E402
from re_testbed.score_and_rank import rank_candidates  # noqa: E402
from utils.thermal_viz import colorize, imwrite_unicode  # noqa: E402

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "results" / "re_testbed" / "single_frame_review"


def parse_roi(text: str) -> tuple[int, int, int, int]:
    parts = [int(v.strip()) for v in text.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("ROI must be x0,y0,x1,y1")
    x0, y0, x1, y1 = parts
    if x1 <= x0 or y1 <= y0:
        raise argparse.ArgumentTypeError("ROI must satisfy x1>x0 and y1>y0")
    return x0, y0, x1, y1


def load_frame(dataset_root: Path, session: str, frame_idx: int) -> np.ndarray:
    path = dataset_root / session / "thermal" / f"{frame_idx:06d}.npy"
    if not path.exists():
        raise FileNotFoundError(path)
    raw = np.load(path)
    if not raw.any():
        raise ValueError(f"thermal frame is empty/zero padded: {path}")
    return raw.astype(np.float32) / TEMP_SCALE


def default_roi(grid: np.ndarray, top_frac: float) -> tuple[int, int, int, int]:
    height, width = grid.shape[:2]
    y0 = int(height * top_frac)
    return 0, y0, width, height


def z_to_bgr(z_map: np.ndarray) -> np.ndarray:
    clipped = np.clip(z_map, -1.0, 6.0)
    norm = ((clipped + 1.0) / 7.0 * 255.0).astype(np.uint8)
    return cv2.applyColorMap(norm, cv2.COLORMAP_JET)


def resize_nearest(image: np.ndarray, scale: int) -> np.ndarray:
    if scale <= 1:
        return image
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)


def pad_to_height(image: np.ndarray, height: int) -> np.ndarray:
    if image.shape[0] == height:
        return image
    pad_bottom = max(0, height - image.shape[0])
    return cv2.copyMakeBorder(image, 0, pad_bottom, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))


def draw_candidate_boxes(
    image: np.ndarray,
    candidates: list[dict[str, Any]],
    origin_x: int = 0,
    origin_y: int = 0,
    scale: int = 1,
) -> None:
    for rank, candidate in enumerate(candidates, start=1):
        p0 = (
            int((candidate["bbox_x0"] - origin_x) * scale),
            int((candidate["bbox_y0"] - origin_y) * scale),
        )
        p1 = (
            int((candidate["bbox_x1"] - origin_x + 1) * scale - 1),
            int((candidate["bbox_y1"] - origin_y + 1) * scale - 1),
        )
        cv2.rectangle(image, p0, p1, (0, 0, 255), max(1, scale // 2))
        cv2.putText(
            image,
            str(rank),
            (p0[0], max(12, p0[1] - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )


def draw_panel(
    grid_c: np.ndarray,
    roi: tuple[int, int, int, int],
    z_map: np.ndarray,
    analysis_roi: tuple[int, int, int, int],
    candidates: list[dict[str, Any]],
    z_thresh: float,
    roi_scale: int,
) -> np.ndarray:
    lo, hi = np.percentile(grid_c, [1, 99])
    thermal = colorize(grid_c, lo, hi)
    x0, y0, x1, y1 = roi

    ax0, ay0, ax1, ay1 = analysis_roi
    cv2.rectangle(thermal, (ax0, ay0), (ax1 - 1, ay1 - 1), (0, 255, 255), 1)
    cv2.rectangle(thermal, (x0, y0), (x1 - 1, y1 - 1), (255, 255, 255), 1)
    draw_candidate_boxes(thermal, candidates)

    roi_grid = crop(grid_c, roi)
    roi_lo, roi_hi = np.percentile(roi_grid, [1, 99])
    roi_thermal = colorize(roi_grid, roi_lo, roi_hi)
    roi_z = z_to_bgr(z_map)
    hot_mask = z_map > z_thresh
    roi_mask_overlay = roi_thermal.copy()
    roi_mask_overlay[hot_mask] = (0, 0, 255)
    roi_z[hot_mask] = (0, 0, 255)

    roi_thermal = resize_nearest(roi_thermal, roi_scale)
    roi_z = resize_nearest(roi_z, roi_scale)
    roi_mask_overlay = resize_nearest(roi_mask_overlay, roi_scale)
    draw_candidate_boxes(roi_thermal, candidates, x0, y0, roi_scale)
    draw_candidate_boxes(roi_z, candidates, x0, y0, roi_scale)
    draw_candidate_boxes(roi_mask_overlay, candidates, x0, y0, roi_scale)

    panel_height = max(thermal.shape[0], roi_thermal.shape[0])
    parts = [
        pad_to_height(thermal, panel_height),
        pad_to_height(roi_thermal, panel_height),
        pad_to_height(roi_z, panel_height),
        pad_to_height(roi_mask_overlay, panel_height),
    ]
    return np.hstack(parts)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        if not rows:
            f.write("")
            return
        fieldnames = list(rows[0].keys())
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run_review(args: argparse.Namespace) -> dict[str, Any]:
    dataset_root = Path(args.dataset_root)
    grid_c = load_frame(dataset_root, args.session, args.frame)
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
    roi = z_result.review_roi
    x0, y0, x1, y1 = roi

    roi_grid = crop(grid_c, roi)
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
    candidates = rank_candidates(candidates)[: args.top_k]
    for rank, candidate in enumerate(candidates, start=1):
        candidate["rank"] = rank
        candidate["frame_idx"] = args.frame
        candidate["session"] = args.session

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{args.session}_{args.frame:06d}"
    panel_path = output_dir / f"{stem}_panel.png"
    json_path = output_dir / f"{stem}.json"
    csv_path = output_dir / f"{stem}_candidates.csv"

    panel = draw_panel(
        grid_c,
        roi,
        z_map,
        z_result.analysis_roi,
        candidates,
        args.z_thresh,
        args.panel_roi_scale,
    )
    imwrite_unicode(panel_path, panel, ".png")
    write_csv(csv_path, candidates)

    hot_mask = z_map > args.z_thresh
    payload = {
        "mode": "single_frame_review",
        "scope_note": (
            "Candidates are thermal review targets inside one ROI. This does not "
            "prove pipe defects or distinguish pre-existing fixed heat sources."
        ),
        "dataset_root": str(dataset_root),
        "session": args.session,
        "frame_idx": args.frame,
        "requested_roi": {
            "x0": z_result.requested_roi[0],
            "y0": z_result.requested_roi[1],
            "x1": z_result.requested_roi[2],
            "y1": z_result.requested_roi[3],
        },
        "analysis_roi": {
            "x0": z_result.analysis_roi[0],
            "y0": z_result.analysis_roi[1],
            "x1": z_result.analysis_roi[2],
            "y1": z_result.analysis_roi[3],
        },
        "review_roi": {"x0": x0, "y0": y0, "x1": x1, "y1": y1},
        "roi": {"x0": x0, "y0": y0, "x1": x1, "y1": y1},
        "bg_method": args.bg_method,
        "bg_param": args.bg_param,
        "analysis_mode": args.analysis_mode,
        "z_thresh": args.z_thresh,
        "z_stats": {
            "min": float(z_map.min()),
            "mean": float(z_map.mean()),
            "max": float(z_map.max()),
            "pixels_over_thresh": int(hot_mask.sum()),
            "roi_pixels": int(z_map.size),
        },
        "candidate_count": len(candidates),
        "candidates": candidates,
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
    parser.add_argument("--roi", type=parse_roi, help="x0,y0,x1,y1 in thermal frame pixels")
    parser.add_argument("--roi-top-frac", type=float, default=0.55, help="default lower-frame ROI top fraction")
    parser.add_argument("--roi-grid-count", type=int, choices=[3, 4], help="split lower frame into 3 or 4 equal ROIs")
    parser.add_argument("--roi-grid-index", type=int, default=0, help="0-based ROI index for --roi-grid-count")
    parser.add_argument("--analysis-mode", default="roi", choices=["roi", "road"])
    parser.add_argument("--bg-method", default="gaussian", choices=["gaussian", "median", "top_hat"])
    parser.add_argument("--bg-param", type=float, default=45)
    parser.add_argument("--z-thresh", type=float, default=3.0)
    parser.add_argument("--min-area", type=int, default=20)
    parser.add_argument("--max-area-frac", type=float, default=0.25)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--panel-roi-scale", type=int, default=3)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = run_review(args)
    print("Single-frame review saved")
    print(f"  candidates: {result['candidate_count']}")
    print(f"  panel: {result['panel_path']}")
    print(f"  json:  {result['json_path']}")
    print(f"  csv:   {result['csv_path']}")


if __name__ == "__main__":
    main()

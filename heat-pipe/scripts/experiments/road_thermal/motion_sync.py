"""Suggest RGB/thermal frame offsets from motion, without asserting synchronization."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.experiments.road_thermal._att_io import (
    att_frame_count,
    iter_att_frames_raw,
    read_att_header,
)


def rendered_to_att_index(rendered_index: int, zero_indices: list[int], total: int) -> int:
    """Map a zero-skipping renderer's zero-based MP4 index to the source ATT index."""
    nonzero_count = total - len(zero_indices)
    if not 0 <= rendered_index < nonzero_count:
        raise ValueError(f"rendered index must be in 0..{nonzero_count - 1}")
    lo, hi = rendered_index, total - 1
    while lo < hi:
        mid = (lo + hi) // 2
        skipped = int(np.searchsorted(zero_indices, mid, side="right"))
        if mid + 1 - skipped >= rendered_index + 1:
            hi = mid
        else:
            lo = mid + 1
    return lo


def _small_frame(frame: np.ndarray, rgb: bool) -> np.ndarray:
    if rgb:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(frame, (96, 72), interpolation=cv2.INTER_AREA).astype(np.float32)
    return small - np.median(small)


def rgb_motion(avi: Path) -> tuple[np.ndarray, int]:
    capture = cv2.VideoCapture(str(avi))
    if not capture.isOpened():
        raise ValueError(f"cannot open AVI: {avi}")
    metadata_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    values: list[float] = []
    previous = None
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            small = _small_frame(frame, rgb=True)
            values.append(float(np.mean(np.abs(small - previous))) if previous is not None else float("nan"))
            previous = small
    finally:
        capture.release()
    if not values:
        raise ValueError(f"no decoded AVI frames: {avi}")
    return np.asarray(values, dtype=np.float32), metadata_count


def thermal_motion(att: Path) -> tuple[np.ndarray, list[int]]:
    header = read_att_header(att)
    count = att_frame_count(att, header)
    values: list[float] = []
    zeros: list[int] = []
    previous = None
    for index, frame in enumerate(iter_att_frames_raw(att, header)):
        if not frame.any():
            zeros.append(index)
            values.append(float("nan"))
            previous = None
            continue
        small = _small_frame(frame, rgb=False)
        values.append(float(np.mean(np.abs(small - previous))) if previous is not None else float("nan"))
        previous = small
    if len(values) != count:
        raise ValueError(f"ATT frame read stopped at {len(values)} of {count}: {att}")
    return np.asarray(values, dtype=np.float32), zeros


def rgb_activity_suggestions(
    rgb: np.ndarray, start: int, stop: int, count: int = 5, min_separation: int = 100
) -> list[dict]:
    """Shortlist active RGB moments without using cross-modal correlation."""
    candidates = [index for index in range(start, stop) if np.isfinite(rgb[index])]
    candidates.sort(key=lambda index: float(rgb[index]), reverse=True)
    selected = []
    for index in candidates:
        if all(abs(index - item["rgb_idx"]) >= min_separation for item in selected):
            selected.append({"rgb_idx": index, "motion_energy": round(float(rgb[index]), 5)})
            if len(selected) == count:
                break
    return selected


def offset_candidates(
    rgb: np.ndarray, thermal: np.ndarray, start: int, stop: int, max_offset: int
) -> dict:
    """Correlate RGB motion[i] with thermal motion[i + offset]."""
    scores = []
    min_pairs = max(20, int(0.6 * (stop - start)))
    for offset in range(-max_offset, max_offset + 1):
        indices = np.arange(start, stop)
        shifted = indices + offset
        in_range = (shifted >= 0) & (shifted < len(thermal))
        left = rgb[indices[in_range]]
        right = thermal[shifted[in_range]]
        valid = np.isfinite(left) & np.isfinite(right)
        if int(valid.sum()) < min_pairs:
            continue
        left, right = left[valid], right[valid]
        left = left - left.mean()
        right = right - right.mean()
        denom = float(np.linalg.norm(left) * np.linalg.norm(right))
        if denom <= 1e-8:
            continue
        scores.append({
            "thermal_minus_rgb_frames": offset,
            "correlation": round(float(np.dot(left, right) / denom), 5),
            "valid_pairs": int(valid.sum()),
        })
    scores.sort(key=lambda item: item["correlation"], reverse=True)
    distinct = []
    for item in scores:
        if all(abs(item["thermal_minus_rgb_frames"] - kept["thermal_minus_rgb_frames"]) > 2 for kept in distinct):
            distinct.append(item)
        if len(distinct) == 5:
            break
    best = distinct[0]["correlation"] if distinct else None
    second = distinct[1]["correlation"] if len(distinct) > 1 else None
    best_offset = distinct[0]["thermal_minus_rgb_frames"] if distinct else None
    nearby = sorted(
        (item for item in scores if best_offset is not None
         and abs(item["thermal_minus_rgb_frames"] - best_offset) <= 2),
        key=lambda item: item["thermal_minus_rgb_frames"],
    )
    return {
        "rgb_frame_range": [start, stop],
        "top_distinct_candidates": distinct,
        "nearby_offset_scores": nearby,
        "all_offset_scores": sorted(scores, key=lambda item: item["thermal_minus_rgb_frames"]),
        "best_minus_immediate_runner_up_correlation": (
            round(best - scores[1]["correlation"], 5) if len(scores) > 1 else None
        ),
        "distinct_candidate_exclusion_radius_frames": 2,
        "best_minus_second_correlation": round(best - second, 5) if second is not None else None,
        "best_minus_median_correlation": (
            round(best - float(np.median([item["correlation"] for item in scores])), 5)
            if best is not None else None
        ),
        "status": "candidate_only" if distinct else "no_usable_motion_signal",
    }


def analyze(att: Path, avi: Path, max_offset: int) -> dict:
    thermal, zero_indices = thermal_motion(att)
    rgb, metadata_count = rgb_motion(avi)
    if len(rgb) != len(thermal):
        raise ValueError(f"decoded AVI/ATT count mismatch: {len(rgb)} vs {len(thermal)}")
    n = len(rgb)
    boundaries = [0, n // 3, 2 * n // 3, n]
    windows = {"full": [0, n]}
    windows.update({
        name: [boundaries[i], boundaries[i + 1]]
        for i, name in enumerate(("early", "middle", "late"))
    })
    return {
        "session": att.stem,
        "att_path": str(att.resolve()),
        "avi_path": str(avi.resolve()),
        "att_frames": len(thermal),
        "avi_decoded_frames": len(rgb),
        "avi_metadata_frames": metadata_count,
        "zero_att_indices": zero_indices,
        "expected_rendered_mp4_frames": len(thermal) - len(zero_indices),
        "max_offset_frames": max_offset,
        "offset_definition": "thermal_index - rgb_index",
        "windows": {
            name: offset_candidates(rgb, thermal, bounds[0], bounds[1], max_offset)
            for name, bounds in windows.items()
        },
        "rgb_activity_suggestions": {
            name: rgb_activity_suggestions(rgb, bounds[0], bounds[1])
            for name, bounds in windows.items() if name != "full"
        },
        "interpretation": "Candidates only. Independently verify common events in early/middle/late frames.",
        "rendered_mapping_note": "Mapping assumes render_thermal_video.py skipped only all-zero ATT frames; verify the actual MP4 decoded frame count separately.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("analyze", help="scan one AVI/ATT pair and write candidate offsets")
    run.add_argument("--att", type=Path, required=True)
    run.add_argument("--avi", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--max-offset", type=int, default=60)
    mapping = sub.add_parser("map", help="convert a rendered MP4 frame index to ATT")
    mapping.add_argument("--report", type=Path, required=True)
    mapping.add_argument("--rendered-index", type=int, required=True)
    args = parser.parse_args()
    if args.command == "map":
        report = json.loads(args.report.read_text(encoding="utf-8"))
        index = rendered_to_att_index(
            args.rendered_index, report["zero_att_indices"], report["att_frames"]
        )
        print(f"rendered MP4 frame {args.rendered_index} -> ATT frame {index}")
        return
    if args.max_offset < 0:
        parser.error("--max-offset must be nonnegative")
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    if args.att.stem != args.avi.stem:
        parser.error("ATT and AVI session stems must match")
    report = analyze(args.att, args.avi, args.max_offset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {args.output} (zero ATT frames: {len(report['zero_att_indices'])})")
    for name, window in report["windows"].items():
        print(f"  {name}: {window['top_distinct_candidates'][:2]}")


if __name__ == "__main__":
    main()

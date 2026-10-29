"""Probe same-camera registration for one frame pair.

This produces diagnostic candidates, not a registration pass/fail decision.
RANSAC residuals use fitting inliers and are not independent validation errors.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.experiments.road_thermal._att_io import read_atg, read_att_frame_raw, read_att_header


def load_pair(mode: str, path: Path, indices: list[int],) -> list[np.ndarray]:
    if mode == "thermal":
        header = read_att_header(path)
        raw = [read_att_frame_raw(path, index, header) for index in indices]

        samples = np.concatenate([frame[::4, ::4].ravel() for frame in raw if frame.any()])
        if samples.size == 0:
            raise ValueError("Selected thermal frames are all zero")

        lo, hi = np.percentile(samples, [1, 99])
        return [np.clip((frame.astype(np.float32) - lo) * 255.0 / max(float(hi - lo), 1.0), 0, 255,).astype(np.uint8) for frame in raw]

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"Cannot open AVI: {path}")

    frames: list[np.ndarray] = []
    try:
        for index in indices:
            if not capture.set(cv2.CAP_PROP_POS_FRAMES, index):
                raise ValueError(f"Cannot seek to RGB frame {index}")

            ok, frame = capture.read()
            position = capture.get(cv2.CAP_PROP_POS_FRAMES)

            if not ok or abs(position - (index + 1)) > 0.5:
                raise ValueError(f"Cannot decode RGB frame {index} at its expected position")

            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    finally:
        capture.release()

    return frames


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float,) -> float:
    radius_m = 6_371_008.8

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    value = (math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)

    return 2 * radius_m * math.asin(min(1.0, math.sqrt(value)))


def valid_gps(lat: float, lon: float) -> bool:
    return (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180)


def read_pair_metadata(atg_path: Path | None, first_index: int, second_index: int,) -> dict:
    base = {
        "atg_path": str(atg_path) if atg_path is not None else None,
        "atg_record_count": None,
        "first_atg": None,
        "second_atg": None,
        "atg_timestamp_delta_s": None,
        "gps_endpoint_distance_m": None,
        "atg_time_status": "not_available",
        "gps_status": "not_available",
        "crossfile_index_status": "unverified_index_alignment",
    }

    if atg_path is None or not atg_path.exists():
        return base

    records = read_atg(atg_path)
    base["atg_record_count"] = len(records)

    if max(first_index, second_index) >= len(records):
        base["atg_time_status"] = "invalid_index"
        base["gps_status"] = "invalid_index"
        return base

    first = records[first_index]
    second = records[second_index]

    base["first_atg"] = {"timestamp": first.timestamp, "lat": first.lat, "lon": first.lon,}
    base["second_atg"] = {"timestamp": second.timestamp, "lat": second.lat, "lon": second.lon,}

    try:
        first_time = datetime.fromisoformat(first.timestamp)
        second_time = datetime.fromisoformat(second.timestamp)
        delta_s = (second_time - first_time).total_seconds()

        base["atg_timestamp_delta_s"] = delta_s

        if delta_s == 0:
            base["atg_time_status"] = "same_second_bucket"
        elif delta_s > 0:
            base["atg_time_status"] = "coarse_delta_available"
        else:
            base["atg_time_status"] = "timestamp_backstep"
    except ValueError:
        base["atg_time_status"] = "invalid_timestamp"

    if valid_gps(first.lat, first.lon) and valid_gps(second.lat, second.lon,):
        distance = haversine_m(first.lat, first.lon, second.lat, second.lon,)
        base["gps_endpoint_distance_m"] = distance

        if first.lat == second.lat and first.lon == second.lon:
            base["gps_status"] = "unchanged_observation"
        else:
            base["gps_status"] = "changed_observation"
    else:
        base["gps_status"] = "invalid_coordinate"

    return base


def hull_coverage(points: np.ndarray, width: int, height: int, roi_y: int,) -> float:
    if len(points) < 3:
        return 0.0

    hull = cv2.convexHull(points.astype(np.float32))
    area = float(cv2.contourArea(hull))
    roi_area = float(width * (height - roi_y))

    return min(area / max(roi_area, 1.0), 1.0)


def grid_occupancy(points: np.ndarray, width: int, height: int, roi_y: int, rows: int = 4, cols: int = 4,) -> dict:
    grid = np.zeros((rows, cols), dtype=np.uint8)
    roi_height = height - roi_y

    for x, y in points:
        if x < 0 or x >= width or y < roi_y or y >= height:
            continue

        col = min(int(x / width * cols), cols - 1)
        row = min(int((y - roi_y) / max(roi_height, 1) * rows), rows - 1,)
        grid[row, col] = 1

    occupied = int(grid.sum())

    return {
        "rows": rows,
        "cols": cols,
        "occupied_cells": occupied,
        "total_cells": rows * cols,
        "fraction": occupied / (rows * cols),
        "matrix": grid.tolist(),
    }


def add_label(image: np.ndarray, text: str,) -> np.ndarray:
    shown = image.copy()
    width = shown.shape[1]

    cv2.rectangle(shown, (0, 0), (width, 38), (20, 20, 20), -1,)
    cv2.putText(shown, text, (10, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 1, cv2.LINE_AA,)
    return shown


def save_warp_panel(path: Path, first: np.ndarray, second: np.ndarray, homography: np.ndarray, 
                    roi: np.ndarray, roi_y: int, first_index: int, second_index: int,) -> dict:
    height, width = first.shape

    warped = cv2.warpPerspective(first, homography, (width, height),)
    warped_roi = cv2.warpPerspective(roi, homography, (width, height), flags=cv2.INTER_NEAREST,)

    target_roi = roi > 0
    overlap = target_roi & (warped_roi > 0)

    target_bgr = cv2.cvtColor(second, cv2.COLOR_GRAY2BGR)
    warped_bgr = cv2.cvtColor(warped, cv2.COLOR_GRAY2BGR)

    overlay = np.zeros_like(target_bgr)
    overlay[:, :, 0] = warped
    overlay[:, :, 1] = second
    overlay[:, :, 2] = warped
    overlay[~overlap] = 0

    difference = cv2.absdiff(warped, second)
    difference[~overlap] = 0
    difference_bgr = cv2.applyColorMap(difference, cv2.COLORMAP_INFERNO,)
    difference_bgr[~overlap] = 0

    for image in (target_bgr, warped_bgr, overlay, difference_bgr,):
        cv2.line(image, (0, roi_y), (width - 1, roi_y), (0, 255, 255), 2,)

    panel = np.vstack([
        np.hstack([
            add_label(target_bgr, f"Target RGB/thermal #{second_index}",),
            add_label(warped_bgr, f"Warped source #{first_index}",),
        ]),
        np.hstack([
            add_label(overlay, "Overlay: target green / source magenta",),
            add_label(difference_bgr, "Absolute photometric difference",),
        ]),
    ])

    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), panel):
        raise OSError(f"Cannot write warp image: {path}")

    target_roi_pixels = int(target_roi.sum())
    overlap_pixels = int(overlap.sum())

    return {
        "valid_overlap_pixels": overlap_pixels,
        "target_roi_pixels": target_roi_pixels,
        "valid_warp_roi_fraction": (
            overlap_pixels / max(target_roi_pixels, 1)
        ),
        "difference_note": (
            "Photometric difference mixes geometric error, "
            "moving objects, exposure, and appearance changes."
        ),
    }


def write_json(result: dict, output: Path | None,) -> None:
    text = json.dumps(result, indent=2, ensure_ascii=False,)

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")

    print(text)


def check_new_output(parser: argparse.ArgumentParser, path: Path | None,) -> None:
    if path is not None and path.exists():
        parser.error(f"Output already exists: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--mode", choices=["rgb", "thermal"], required=True,)
    parser.add_argument("--source", type=Path, required=True,)
    parser.add_argument("--first", type=int, required=True,)
    parser.add_argument("--second", type=int, required=True,)
    parser.add_argument("--roi-top", type=float, default=0.55,)
    parser.add_argument("--atg", type=Path, help="Optional matching ATG file",)
    parser.add_argument("--experiment-role", choices=["smoke_test", "benchmark_positive", "benchmark_negative",], default="smoke_test",)
    parser.add_argument("--out", type=Path, help="Optional inlier-match PNG",)
    parser.add_argument("--warp-out", type=Path, help="Optional warp diagnostic PNG",)
    parser.add_argument("--json-out", type=Path, help="Optional machine-readable result JSON",)

    args = parser.parse_args()

    if min(args.first, args.second) < 0:
        parser.error("Frame indices must be nonnegative")
    if args.first == args.second:
        parser.error("First and second frame must differ")
    if not 0 <= args.roi_top < 1:
        parser.error("--roi-top must be in [0, 1)")

    check_new_output(parser, args.out)
    check_new_output(parser, args.warp_out)
    check_new_output(parser, args.json_out)

    atg_path = args.atg
    if atg_path is None:
        candidate = args.source.with_suffix(".atg")
        if candidate.exists():
            atg_path = candidate

    first, second = load_pair(args.mode, args.source, [args.first, args.second],)

    if first.shape != second.shape:
        raise ValueError("Frame dimensions differ")

    height, width = first.shape
    roi_y = int(height * args.roi_top)

    roi = np.zeros((height, width), dtype=np.uint8)
    roi[roi_y:] = 255

    result = {
        "schema_version": 2,
        "status": "no_fit",
        "experiment_role": args.experiment_role,
        "mode": args.mode,
        "source": str(args.source.resolve()),
        "frames": [args.first, args.second],
        "frame_delta": args.second - args.first,
        "roi_top_fraction": args.roi_top,
        "roi_y": roi_y,
        "metadata": read_pair_metadata(
            atg_path,
            args.first,
            args.second,
        ),
        "interpretation": ("Diagnostic registration candidate only. " "No pass/fail conclusion is produced."),
    }

    orb = cv2.ORB_create(nfeatures=2000, fastThreshold=10,)

    keypoints_first, descriptors_first = (orb.detectAndCompute(first, roi))
    keypoints_second, descriptors_second = (orb.detectAndCompute(second, roi))

    result["keypoints"] = [len(keypoints_first), len(keypoints_second),]

    if descriptors_first is None or descriptors_second is None:
        result["failure_reason"] = "missing_descriptors"
        write_json(result, args.json_out)
        return

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    pairs = matcher.knnMatch(descriptors_first, descriptors_second, k=2)

    good_matches = [first_match for pair in pairs if len(pair) == 2 for first_match, second_match in [pair] if first_match.distance < 0.75 * second_match.distance]

    result["ratio_matches"] = len(good_matches)

    if len(good_matches) < 4:
        result["failure_reason"] = "fewer_than_four_ratio_matches"
        write_json(result, args.json_out)
        return

    source_points = np.float32([keypoints_first[match.queryIdx].pt for match in good_matches]).reshape(-1, 1, 2)
    target_points = np.float32([keypoints_second[match.trainIdx].pt for match in good_matches]).reshape(-1, 1, 2)

    homography, mask = cv2.findHomography(source_points, target_points, cv2.RANSAC, 3.0,)

    if homography is None or mask is None:
        result["failure_reason"] = "homography_not_found"
        write_json(result, args.json_out)
        return

    inliers = mask.ravel().astype(bool)
    inlier_count = int(inliers.sum())

    if inlier_count < 4:
        result["failure_reason"] = "fewer_than_four_inliers"
        write_json(result, args.json_out)
        return

    predicted = cv2.perspectiveTransform(source_points, homography,).reshape(-1, 2)

    target_flat = target_points.reshape(-1, 2)
    errors = np.linalg.norm(predicted - target_flat, axis=1,)

    source_inliers = source_points.reshape(-1, 2)[inliers]
    target_inliers = target_flat[inliers]
    inlier_errors = errors[inliers]

    result.update({
        "status": "fit_candidate",
        "inliers": inlier_count,
        "inlier_fraction_of_ratio_matches": (inlier_count / len(good_matches)),
        "median_fit_residual_px": float(np.median(inlier_errors)),
        "p95_fit_residual_px": float(np.percentile(inlier_errors, 95)),
        "fit_residual_basis": (
            "RANSAC fitting inliers; "
            "not independent validation points"
        ),
        "source_inlier_hull_roi_fraction": hull_coverage(source_inliers, width, height, roi_y,),
        "target_inlier_hull_roi_fraction": hull_coverage(target_inliers, width, height, roi_y,),
        "source_inlier_grid": grid_occupancy(source_inliers, width, height, roi_y,),
        "target_inlier_grid": grid_occupancy(target_inliers, width, height, roi_y,),
        "homography": homography.tolist(),
        "homography_condition_number": float(np.linalg.cond(homography)),
    })

    if args.out is not None:
        inlier_matches = [match for match, keep in zip(good_matches, inliers) if keep]

        shown = cv2.drawMatches(first, keypoints_first, second, keypoints_second, inlier_matches, None, flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS,)

        args.out.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(args.out), shown):
            raise OSError(f"Cannot write match image: {args.out}")

        result["match_image"] = str(args.out.resolve())

    if args.warp_out is not None:
        result["warp_diagnostics"] = save_warp_panel(
            args.warp_out,
            first,
            second,
            homography,
            roi,
            roi_y,
            args.first,
            args.second,
        )
        result["warp_image"] = str(args.warp_out.resolve())

    write_json(result, args.json_out)


if __name__ == "__main__":
    main()
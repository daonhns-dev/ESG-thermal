"""Shared frame-to-frame thermal candidate tracking helpers."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def centroid(candidate: dict[str, Any]) -> tuple[float, float]:
    """Return the center point of a candidate bbox."""
    return (
        (candidate["bbox_x0"] + candidate["bbox_x1"]) / 2,
        (candidate["bbox_y0"] + candidate["bbox_y1"]) / 2,
    )


def build_tracks(by_frame: dict[int, list[dict[str, Any]]], max_dist: float, max_gap: int) -> list[dict[str, Any]]:
    """Greedy nearest-neighbor matching across frames."""
    active: list[dict[str, Any]] = []
    finished: list[dict[str, Any]] = []

    for frame_idx in sorted(by_frame.keys()):
        candidates = list(by_frame[frame_idx])
        used: set[int] = set()

        for track in active:
            last_frame, last_candidate = track["points"][-1]
            if frame_idx - last_frame > max_gap:
                continue

            last_x, last_y = centroid(last_candidate)
            best_idx = None
            best_dist = max_dist
            for i, candidate in enumerate(candidates):
                if i in used:
                    continue
                current_x, current_y = centroid(candidate)
                dist = math.hypot(current_x - last_x, current_y - last_y)
                if dist < best_dist:
                    best_idx = i
                    best_dist = dist

            if best_idx is not None:
                track["points"].append((frame_idx, candidates[best_idx]))
                track["last_frame"] = frame_idx
                used.add(best_idx)

        still_active = []
        for track in active:
            if frame_idx - track["last_frame"] > max_gap:
                finished.append(track)
            else:
                still_active.append(track)
        active = still_active

        for i, candidate in enumerate(candidates):
            if i not in used:
                active.append({"points": [(frame_idx, candidate)], "last_frame": frame_idx})

    finished.extend(active)
    return finished


def summarize_track(
    track: dict[str, Any],
    track_id: int,
    frame_height: int,
    bottom_margin: int,
    growth_thresh: float,
    min_circularity: float,
) -> dict[str, Any]:
    """Summarize one candidate track for review ranking."""
    points = track["points"]
    frames = [point[0] for point in points]
    first_candidate = points[0][1]
    last_candidate = points[-1][1]
    area_start = first_candidate["area_px"]
    area_end = last_candidate["area_px"]
    y_start = centroid(first_candidate)[1]
    y_end = centroid(last_candidate)[1]
    exits_bottom = last_candidate["bbox_y1"] >= frame_height - 1 - bottom_margin
    growth = area_end / area_start if area_start > 0 else float("nan")
    mean_circularity = float(np.mean([point[1]["circularity"] for point in points]))

    likely_ground_fixed = (
        len(points) >= 3
        and growth >= growth_thresh
        and exits_bottom
        and y_end > y_start
        and mean_circularity >= min_circularity
    )

    return {
        "track_id": track_id,
        "n_frames": len(points),
        "start_frame": frames[0],
        "end_frame": frames[-1],
        "area_start": area_start,
        "area_end": area_end,
        "mean_circularity": round(mean_circularity, 3),
        "area_growth_ratio": round(growth, 3) if not math.isnan(growth) else "",
        "y_center_start": round(y_start, 1),
        "y_center_end": round(y_end, 1),
        "moved_down": y_end > y_start,
        "exits_bottom": exits_bottom,
        "mean_peak_temp_c": round(float(np.mean([point[1]["peak_temp_c"] for point in points])), 2),
        "mean_max_score": round(float(np.mean([point[1].get("max_score", point[1].get("max_z", 0.0)) for point in points])), 3),
        "mean_max_z": round(float(np.mean([point[1].get("max_z", 0.0) for point in points])), 3),
        "likely_ground_fixed": likely_ground_fixed,
    }

"""Compare tracking costs on candidates extracted once per ATT window."""

from __future__ import annotations

import argparse
import json
import sys
from itertools import pairwise
from pathlib import Path
from statistics import median
from time import perf_counter
from unittest.mock import patch

import numpy as np
from scipy.ndimage import binary_erosion
from scipy.optimize import linear_sum_assignment

from aggregate_14_pairs import PROJECT
from stable_gpu import score_maps

sys.path.insert(0, str(PROJECT / "src"))

from heatpipe_service.analysis import candidates as candidate_features
from heatpipe_service.analysis.io import ATT_HEADER_SIZE, TEMP_SCALE, read_att_frame_celsius, read_att_header
from heatpipe_service.analysis.tracking import build_tracks, centroid
from heatpipe_service.jobs import batch


def assign_tracks(by_frame, max_distance, gap, predict=False, geometry=False):
    active, finished = [], []
    for frame, points in sorted(by_frame.items()):
        kept = []
        for track in active:
            (kept if frame - track["last_frame"] <= gap else finished).append(track)
        active = kept
        matched = set()
        if active:
            columns = len(points)
            unmatched = 3 * len(active) + 1
            cost = np.full((len(active), columns + len(active)), unmatched * 2.0)
            cost[:, columns:] = unmatched
            for i, track in enumerate(active):
                history = track["points"]
                last_frame, last = history[-1]
                center = np.array(centroid(last))
                expected = center
                if predict and len(history) >= 2:
                    previous_frame, previous = history[-2]
                    velocity = (center - centroid(previous)) / (last_frame - previous_frame)
                    expected = center + velocity * (frame - last_frame)
                for j, candidate in enumerate(points):
                    observed = centroid(candidate)
                    if np.linalg.norm(observed - center) >= max_distance:
                        continue
                    distance = np.linalg.norm(observed - expected)
                    if distance >= max_distance:
                        continue
                    score = distance / max_distance
                    if geometry:
                        size_change = abs(np.log(candidate["area_px"] / last["area_px"]))
                        overlap = batch._bbox_iou(last, candidate)
                        score += 0.25 * min(1.0, size_change) + 0.25 * (1.0 - overlap)
                    cost[i, j] = score
            rows, choices = linear_sum_assignment(cost)
            for i, j in zip(rows, choices):
                if j < columns:
                    active[i]["points"].append((frame, points[j]))
                    active[i]["last_frame"] = frame
                    matched.add(j)
        active.extend({"points": [(frame, candidate)], "last_frame": frame}
                      for j, candidate in enumerate(points) if j not in matched)
    return finished + active


def compare(att, start, length, track_trace=None):
    header = read_att_header(att)
    config = batch.BatchConfig()
    roi = batch.grid_cell_roi((header.height, header.width), 2, 3, 1, 1)
    candidates = {}
    elapsed = dict(read=0.0, score_maps=0.0, components_merge=0.0)

    def timed_score(roi_c, algorithms, _device):
        before = perf_counter()
        maps, _ = score_maps(roi_c, algorithms)
        elapsed["score_maps"] += perf_counter() - before
        return maps

    with patch.object(batch, "score_maps", timed_score):
        for i in range(start, start + length):
            before = perf_counter()
            frame = read_att_frame_celsius(att, i, header)
            elapsed["read"] += perf_counter() - before
            if frame.max() <= 0:
                continue
            before = perf_counter()
            points = batch._frame_candidates(frame, roi, config)
            elapsed["components_merge"] += perf_counter() - before
            if points:
                candidates[i] = points
    elapsed["components_merge"] -= elapsed["score_maps"]

    algos = {
        "greedy": lambda: build_tracks(candidates, config.max_track_distance, 1),
        "greedy_gap": lambda: build_tracks(candidates, config.max_track_distance, 2),
        "assignment_gap": lambda: assign_tracks(candidates, config.max_track_distance, 2),
        "assignment_motion": lambda: assign_tracks(candidates, config.max_track_distance, 2, predict=True),
        "assignment_motion_geometry": lambda: assign_tracks(candidates, config.max_track_distance, 2,
                                                            predict=True, geometry=True),
    }
    results = {}
    expected_points = sum(map(len, candidates.values()))
    for name, build in algos.items():
        before = perf_counter()
        tracks = build()
        elapsed_track = perf_counter() - before
        if track_trace is not None:
            track_trace[name] = tracks
        assigned = [(frame, id(point)) for track in tracks for frame, point in track["points"]]
        if len(assigned) != expected_points or len(set(assigned)) != expected_points:
            raise ValueError(f"Candidate assignment invalid: {name}")
        eligible = [track for track in tracks if len(track["points"]) >= config.min_track_frames]
        gaps = [right[0] - left[0] for track in eligible for left, right in pairwise(track["points"])]
        results[name] = {
            "tracks": len(tracks),
            "eligible": len(eligible),
            "median_eligible_length": median(len(track["points"]) for track in eligible) if eligible else None,
            "gap_links_in_eligible": gaps.count(2),
            "tracking_seconds": elapsed_track,
        }
    return {"input": att.stem, "start": start, "frames": length, "candidates": expected_points,
            "seconds": elapsed, "tracking": results}


def check_optimizations(att, start, length):
    header = read_att_header(att)
    roi = batch.grid_cell_roi((header.height, header.width), 2, 3, 1, 1)
    x0, y0, x1, y1 = roi
    config = batch.BatchConfig()
    before = perf_counter()
    frames = [read_att_frame_celsius(att, i, header) for i in range(start, start + length)]
    frame_reads = perf_counter() - before
    before = perf_counter()
    with att.open("rb") as stream:
        stream.seek(ATT_HEADER_SIZE + start * header.frame_bytes)
        raw = np.fromfile(stream, dtype="<u2", count=length * header.frame_pixels)
    sequential = raw.reshape(length, header.height, header.width).astype(np.float32) / TEMP_SCALE
    sequential_read = perf_counter() - before
    if any(not np.array_equal(old, new) for old, new in zip(frames, sequential, strict=True)):
        raise ValueError("Sequential and per-frame reads differ")

    def cached_edges(grid, mask):
        boundary = mask & ~binary_erosion(mask)
        return float(magnitude[boundary].mean()) if boundary.any() else 0.0

    original_seconds, reuse_seconds = 0.0, 0.0
    for frame in sequential:
        grid = frame[y0:y1, x0:x1]
        maps, _ = score_maps(grid, config.algorithms)
        # Supply identical score maps to isolate feature extraction costs.
        with patch.object(batch, "score_maps", return_value=maps):
            before = perf_counter()
            original = batch._frame_candidates(frame, roi, config)
            original_seconds += perf_counter() - before
            before = perf_counter()
            grad_y, grad_x = np.gradient(grid)
            magnitude = np.sqrt(grad_y**2 + grad_x**2)
            with patch.object(candidate_features, "edge_sharpness", cached_edges):
                optimized = batch._frame_candidates(frame, roi, config)
            reuse_seconds += perf_counter() - before
        if original != optimized:
            raise ValueError("Gradient reuse altered the candidate features or order")
    return {"frame_reads_seconds": frame_reads, "sequential_read_seconds": sequential_read,
            "features_original_seconds": original_seconds, "features_reuse_seconds": reuse_seconds,
            "exact_frame_and_candidate_match": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    sample_windows = (("20260813_132634", 1600), ("20260816_120017", 9950))
    results = []
    for stem, frame in sample_windows:
        att = next((PROJECT / "data/experiments/raw").glob(f"*/{stem}.att"))
        result = compare(att, frame, 180)
        result["optimization_pilot"] = check_optimizations(att, frame, 180)
        results.append(result)
        print(f"Finished {stem} at frame {frame}", flush=True)
    args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

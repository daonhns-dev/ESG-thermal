"""Compare E1 with per-detector score calibration on two short windows."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

from aggregate_14_pairs import PROJECT
from score_scale import HISTORY_FRAMES, RecentScoreCalibration, fit_gains, rescale_candidate
from stable_gpu import score_maps

sys.path.insert(0, str(PROJECT / "src"))

from heatpipe_service.analysis.candidates import extract_candidates
from heatpipe_service.analysis.io import att_frame_count, read_att_frame_celsius, read_att_header
from heatpipe_service.jobs import batch


WINDOWS = (("20260813_132634", 1600), ("20260816_230532", 2500))
WINDOW_FRAMES = 100


def read_components(att, start, length, config):
    header = read_att_header(att)
    if start < 0 or start + length > att_frame_count(att, header):
        raise ValueError(f"Window outside ATT: {att}")
    x0, y0, x1, y1 = batch.grid_cell_roi(
        (header.height, header.width), config.display_grid_rows, config.display_grid_columns,
        config.display_grid_rows - 1, (config.display_grid_columns - 1) // 2,
    )
    components = {}
    for i in range(start, start + length):
        raw = read_att_frame_celsius(att, i, header)
        if not np.isfinite(raw).all() or raw.max() <= 0:
            continue
        roi = raw[y0:y1, x0:x1]
        maps, _ = score_maps(roi, config.algorithms)
        rows = []
        for algorithm, scores in zip(config.algorithms, maps, strict=True):
            detected = extract_candidates(
                roi, scores, score_thresh=config.score_threshold, min_area=config.min_area,
                max_area_frac=config.max_area_fraction, offset_y=y0, offset_x=x0,
            )
            for row in detected:
                row.update(source_algorithm=algorithm, algorithms=[algorithm])
            rows.extend(detected)
        components[i] = rows
    return components, header.height


def summarize_peaks(components, algorithms, threshold):
    peaks = {algorithm: [] for algorithm in algorithms}
    for rows in components.values():
        for row in rows:
            peaks[row["source_algorithm"]].append(row["max_score"] - threshold)
    return {
        name: {"samples": len(scores), "p90_excess": float(np.quantile(scores, 0.90)) if scores else None}
        for name, scores in peaks.items()
    }


def evaluate(components, height, start, length, config):
    by_frame = {
        frame: batch._merge_candidates(rows, config.merge_iou_threshold)
        for frame, rows in components.items() if rows
    }
    tracks = batch.build_tracks(by_frame, config.max_track_distance, config.max_track_gap)
    eligible = [item for item in batch._rank_tracks(tracks, height, config)
                if item["summary"]["n_frames"] >= config.min_track_frames]
    selected = batch._select_tracks_across_frames(eligible, start, start + length, config.max_results)
    return {
        "per_detector_premerge": summarize_peaks(components, config.algorithms, config.score_threshold),
        "merged_frame_candidates": sum(map(len, by_frame.values())),
        "merged_candidate_sources": dict(Counter(
            p["source_algorithm"] for rows in by_frame.values() for p in rows
        )),
        "eligible_tracks": len(eligible),
        "top50": [
            {"frame": item["representative_frame"],
             "box": [item["representative"][key] for key in ("bbox_x0", "bbox_y0", "bbox_x1", "bbox_y1")],
             "source": item["representative"]["source_algorithm"],
             "score": item["persistence_score"]}
            for item in selected
        ],
    }


def summarize_comparison(stem, reference, original, calibrated, height, start, length, config):
    raw = evaluate(original, height, start, length, config)
    comparison = evaluate(calibrated, height, start, length, config)
    location_sets = [{(row["frame"], tuple(row["box"])) for row in run["top50"]}
                     for run in (raw, comparison)]
    return {
        "input": stem, "reference": reference, "start_frame": start,
        "frames": length, "analyzed_frames": len(original),
        "same_top50_frame_box": len(location_sets[0] & location_sets[1]),
        "e1": raw, "calibrated": comparison,
    }


def compare_window(stem, reference, fit_components, original, height, start, length, config):
    gains, fit = fit_gains(fit_components, config.algorithms, config.score_threshold)
    calibrated = {frame: [rescale_candidate(row, gains, config.score_threshold) for row in rows]
                  for frame, rows in original.items()}
    return {
        **summarize_comparison(stem, reference, original, calibrated, height, start, length, config),
        "gains": gains, "fit": fit,
    }


def compare_recent(stem, components, height, start, length, config):
    """Use prior frames only; damp gain changes toward the original E1 scale."""
    original, calibrated, history_gains = {}, {}, []
    boundary = start + HISTORY_FRAMES
    calibrator = RecentScoreCalibration(config.algorithms, config.score_threshold, start)
    for frame, rows in components.items():
        adjusted, gains = calibrator.apply(frame, rows)
        if frame < boundary:
            continue
        history_gains.append(gains)
        original[frame] = rows
        calibrated[frame] = adjusted
    return {
        **summarize_comparison(stem, "previous_20_frames_damped", original, calibrated, height,
                               boundary, length - HISTORY_FRAMES, config),
        "median_gains": {name: float(np.median([g[name] for g in history_gains]))
                         for name in config.algorithms} if history_gains else {},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    config = batch.BatchConfig()
    root = PROJECT / "data/experiments/raw"
    windows = {}
    for stem, start in WINDOWS:
        path = root / stem[:8] / f"{stem}.att"
        windows[stem] = read_components(path, start, WINDOW_FRAMES, config)
        print(f"{stem}: read {WINDOW_FRAMES} frames", flush=True)

    results = []
    for i, (stem, start) in enumerate(WINDOWS):
        reference_stem = WINDOWS[1 - i][0]
        ref_components, _ = windows[reference_stem]
        original, height = windows[stem]
        results.append(compare_window(stem, reference_stem, ref_components, original, height,
                                      start, WINDOW_FRAMES, config))
        half = WINDOW_FRAMES // 2
        boundary = start + half
        reference_half = {frame: rows for frame, rows in original.items() if frame < boundary}
        evaluation_half = {frame: rows for frame, rows in original.items() if frame >= boundary}
        results.append(compare_window(stem, f"{stem}:{start}-{boundary - 1}", reference_half,
                                      evaluation_half, height, boundary, half, config))
        results.append(compare_recent(stem, original, height, start, WINDOW_FRAMES, config))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()

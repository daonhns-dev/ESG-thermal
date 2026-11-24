"""Summarize one E0 baseline result without treating candidates as ground truth."""

from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter
from pathlib import Path

import numpy as np


def interval(frame: int, start: int, end: int) -> int:
    return min(9, max(0, (frame - start) * 10 // (end - start)))


def quantiles(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {
        str(percentile): round(float(np.percentile(values, percentile)), 3)
        for percentile in (0, 25, 50, 75, 90, 99, 100)
    }


def summarize(output: Path) -> dict:
    result = json.loads((output / "analysis_result.json").read_text(encoding="utf-8"))
    if result["status"] != "SUCCEEDED":
        raise ValueError(f"Baseline did not succeed: {output}")
    frame_range = result["processing"]["frame_range"]
    start, end = frame_range["start"], frame_range["end_exclusive"]
    all_intervals = [0] * 10
    scores: list[float] = []
    lengths: list[float] = []
    peak_temperatures: list[float] = []
    algorithms: Counter[str] = Counter()
    exported = 0
    with gzip.open(output / "eligible_tracks.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            item = json.loads(line)
            exported += 1
            all_intervals[interval(item["representative_frame"], start, end)] += 1
            scores.append(float(item["persistence_score"]))
            lengths.append(float(item["summary"]["n_frames"]))
            peak_temperatures.append(float(item["representative"]["peak_temp_c"]))
            algorithms.update(item["representative"]["algorithms"])
    selected = result["candidates"]
    selected_intervals = [0] * 10
    for candidate in selected:
        selected_intervals[interval(candidate["representative_frame"], start, end)] += 1
    if exported != result["detected_candidate_count"]:
        raise ValueError("Exported track count differs from detected_candidate_count")
    if len(selected) != result["candidate_count"]:
        raise ValueError("Returned candidate count differs from candidate_count")
    summary = {
        "input": result["processing"]["input"],
        "frame_range": frame_range,
        "analysis_roi": result["processing"]["analysis_roi"],
        "elapsed_seconds": result["processing"]["elapsed_seconds"],
        "eligible_tracks": exported,
        "returned_candidates": len(selected),
        "eligible_by_tenth": all_intervals,
        "returned_by_tenth": selected_intervals,
        "persistence_score_quantiles": quantiles(scores),
        "track_frame_count_quantiles": quantiles(lengths),
        "representative_peak_temperature_quantiles_c": quantiles(peak_temperatures),
        "eligible_representative_peak_at_least_130_c": sum(value >= 130 for value in peak_temperatures),
        "returned_candidate_peak_at_least_130_c": sum(
            item["temperature_c"]["maximum"] >= 130 for item in selected
        ),
        "returned_roi_max_at_least_130_c": sum(
            item["roi_temperature_c"]["maximum"] >= 130 for item in selected
        ),
        "representative_algorithm_counts": dict(algorithms),
        "warnings": result["warnings"],
    }
    (output / "e0_diagnostics.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="One successful E0 output directory")
    args = parser.parse_args()
    print(json.dumps(summarize(args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

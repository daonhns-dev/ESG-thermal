"""Compare global top-N with ten-interval selection on exported tracks."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path


def interval(frame: int, start: int, end: int) -> int:
    return min(9, max(0, (frame - start) * 10 // (end - start)))


def describe(items: list[dict], start: int, end: int) -> dict:
    counts = [0] * 10
    for item in items:
        counts[interval(item["representative_frame"], start, end)] += 1
    return {
        "count": len(items),
        "by_tenth": counts,
        "representative_peak_at_least_130_c": sum(
            item["representative"]["peak_temp_c"] >= 130 for item in items
        ),
        "unique_representative_frames": len(
            {item["representative_frame"] for item in items}
        ),
    }


def compare(output: Path, limit: int) -> dict:
    result = json.loads((output / "analysis_result.json").read_text(encoding="utf-8"))
    frame_range = result["processing"]["frame_range"]
    start, end = frame_range["start"], frame_range["end_exclusive"]
    with gzip.open(output / "eligible_tracks.jsonl.gz", "rt", encoding="utf-8") as stream:
        ranked = [json.loads(line) for line in stream]
    global_top = ranked[:limit]
    intervals = [[] for _ in range(10)]
    for item in ranked:
        intervals[interval(item["representative_frame"], start, end)].append(item)
    balanced = []
    while len(balanced) < limit:
        added = False
        for group in intervals:
            if group and len(balanced) < limit:
                balanced.append(group.pop(0))
                added = True
        if not added:
            break
    global_ids = {item["summary"]["track_id"] for item in global_top}
    balanced_ids = {item["summary"]["track_id"] for item in balanced}
    summary = {
        "input": result["processing"]["input"],
        "limit": limit,
        "eligible_tracks": len(ranked),
        "global_top": describe(global_top, start, end),
        "ten_interval_round_robin": describe(balanced, start, end),
        "shared_track_count": len(global_ids & balanced_ids),
    }
    (output / "e3_selection_comparison.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Successful experiment output directory")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    print(json.dumps(compare(args.output, args.limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

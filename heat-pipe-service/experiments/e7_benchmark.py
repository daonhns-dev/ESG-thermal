"""Time one E0 CPU run with optional, output-preserving changes to frame access."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from heatpipe_service.analysis import algorithms, candidates
from heatpipe_service.jobs import batch
from exact_percentile import local_percentile
from optimized_vehicle import FrameGradient, SharedFrameAccess


def verify_equivalence(baseline, output, metrics, result):
    def read_json(path):
        return json.loads(path.read_text(encoding="utf-8"))

    reference = read_json(baseline / "benchmark.json")
    original = read_json(baseline / "analysis_result.json")
    same = {
        "frame_range": reference["frames"] == metrics["frames"],
        "baseline_parameters": all(result["processing"][key] == original["processing"][key]
                                   for key in ("input", "parameters", "analysis_roi", "algorithms")),
        "eligible_tracks": reference["eligible_sha256"] == metrics["eligible_sha256"],
        "selected_track_order": reference["selected_track_ids"] == metrics["selected_track_ids"],
        "candidates_and_png_hashes": original["candidates"] == result["candidates"],
        "csv": (baseline / "analysis_candidates.csv").read_bytes()
        == (output / "analysis_candidates.csv").read_bytes(),
    }
    return same


def benchmark(att, output, variant, start, end, against=None):
    wall_start = perf_counter()
    att = att.resolve()
    output.mkdir(parents=True, exist_ok=False)
    request = batch.BatchRequest(
        job_id="EXPERIMENT-E7", monitoring_data_id=att.stem, att_path=att,
        atg_path=att.with_suffix(".atg"), output_dir=output,
        frame_start=start, frame_end=end,
    )
    seconds, calls = Counter(), Counter()
    captured = {}

    def timed(operation, name):
        def wrapped(*args, **kwargs):
            started = perf_counter()
            try:
                return operation(*args, **kwargs)
            finally:
                seconds[name] += perf_counter() - started
                calls[name] += 1
        return wrapped

    original_select = batch._select_tracks_across_frames

    def capture_selection(tracks, *args):
        captured["eligible"] = tracks
        selected = original_select(tracks, *args)
        captured["selected"] = [row["summary"]["track_id"] for row in selected]
        return selected

    access = SharedFrameAccess(att) if variant in {"read", "both", "all"} else None
    reader = access.read_temperature if access else batch.read_att_frame_celsius
    with ExitStack() as stack:
        if variant in {"gradient", "both", "all"}:
            stack.enter_context(patch.object(candidates, "edge_sharpness", FrameGradient().edge_sharpness))
        if variant in {"percentile", "all"}:
            stack.enter_context(patch.object(algorithms, "percentile_filter", local_percentile))
        stack.enter_context(patch.object(algorithms, "percentile_contrast",
                                         timed(algorithms.percentile_contrast, "percentile_contrast")))
        for attribute, name, operation in (
            ("read_att_frame_celsius", "att_read", reader),
            ("score_maps", "score_maps", batch.score_maps),
            ("extract_candidates", "components_and_features", batch.extract_candidates),
            ("_merge_candidates", "merge", batch._merge_candidates),
            ("build_tracks", "tracking", batch.build_tracks),
            ("_rank_tracks", "track_ranking", batch._rank_tracks),
            ("_select_tracks_across_frames", "selection", capture_selection),
            ("draw_result_image", "render", batch.draw_result_image),
            ("imwrite_unicode", "png_write", batch.imwrite_unicode),
        ):
            stack.enter_context(patch.object(batch, attribute, timed(operation, name)))
        artifacts = batch.run_batch(request, batch.BatchConfig(analysis_device="cpu"))
    wall_seconds = perf_counter() - wall_start
    memory = psutil.Process().memory_info()
    peak_bytes = getattr(memory, "peak_wset", None)
    if peak_bytes is None and sys.platform != "win32":
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_bytes = peak if sys.platform == "darwin" else peak * 1024

    digest = hashlib.sha256()
    for row in captured["eligible"]:
        digest.update(json.dumps(row, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n")
    result = json.loads(artifacts.result_json.read_text(encoding="utf-8"))
    metrics = {
        "variant": variant, "device": "cpu", "platform": platform.platform(),
        "frames": result["processing"]["frame_range"], "wall_seconds": wall_seconds,
        "peak_rss_mib": peak_bytes / 2**20 if peak_bytes is not None else None,
        "phase_seconds": dict(seconds), "phase_calls": dict(calls),
        "eligible_tracks": result["detected_candidate_count"], "eligible_sha256": digest.hexdigest(),
        "selected_track_ids": captured["selected"], "returned": result["candidate_count"],
    }
    if against is not None:
        metrics["equivalence"] = verify_equivalence(against, output, metrics, result)
    (output / "benchmark.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    if against is not None and not all(metrics["equivalence"].values()):
        raise RuntimeError(f"Output changed: {metrics['equivalence']}")
    print(f"{att.stem} {variant}: {wall_seconds:.2f}s, {metrics['eligible_tracks']} eligible tracks", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("att", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--variant", choices=("e0", "read", "gradient", "both", "percentile", "all"), default="e0")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int)
    parser.add_argument("--against", type=Path, help="Check against an existing E0 benchmark directory")
    args = parser.parse_args()
    benchmark(args.att, args.output, args.variant, args.start, args.end, args.against)


if __name__ == "__main__":
    main()

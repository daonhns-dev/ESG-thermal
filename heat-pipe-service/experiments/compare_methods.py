"""Run the current service pipeline as a reproducible experiment baseline.

Additional methods belong in this directory and should use the same input,
frame range, ROI, and evaluation set before their results are compared.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from heatpipe_service.analysis import algorithms, anomaly
from heatpipe_service.analysis.io import TEMP_SCALE, read_att_frame_raw
from heatpipe_service.jobs import batch
from heatpipe_service.jobs.batch import BatchConfig, BatchRequest, run_batch_safely
from stable_gpu import score_maps as stable_cuda_score_maps


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--att", required=True, type=Path, help="ATT input file")
    parser.add_argument("--atg", required=True, type=Path, help="Matching ATG input file")
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="New result directory, normally under output/experiments/",
    )
    parser.add_argument("--job-id", help="Optional result ID; otherwise derived from --method")
    parser.add_argument("--frame-start", type=int, default=0)
    parser.add_argument("--frame-end", type=int)
    parser.add_argument("--max-results", type=int, default=50)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--decode-signed", action="store_true",
                        help="Experimentally interpret ATT thermal samples as signed int16")
    parser.add_argument("--vehicle-features", type=Path, help="Training track feature export")
    parser.add_argument("--vehicle-labels", type=Path, help="Reviewed training labels")
    parser.add_argument("--calibrate-detectors", action="store_true",
                        help="Experimentally calibrate E1 detector scores using previous frames")
    parser.add_argument("--fast-percentile", action="store_true",
                        help="Experimentally accelerate CPU percentile filtering")
    parser.add_argument("--representative-suppression", action="store_true",
                        help="Apply the fixed E3 representative-frame ±6 rule to E1 selection")
    parser.add_argument(
        "--method",
        choices=("baseline", "nonzero_mad_fallback", "gap_one_missing", "optimized_vehicle"),
        default="baseline",
        help="One experiment variant at a time",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.method == "optimized_vehicle":
        if args.vehicle_features is None or args.vehicle_labels is None:
            parser.error("optimized_vehicle requires --vehicle-features and --vehicle-labels")
    elif args.vehicle_features is not None or args.vehicle_labels is not None:
        parser.error("Vehicle training inputs require --method optimized_vehicle")
    if args.calibrate_detectors and args.method not in {"nonzero_mad_fallback", "optimized_vehicle"}:
        parser.error("--calibrate-detectors requires an E1-based method")
    if args.fast_percentile and args.device != "cpu":
        parser.error("--fast-percentile requires --device cpu")
    if args.representative_suppression and args.method != "nonzero_mad_fallback":
        parser.error("--representative-suppression requires --method nonzero_mad_fallback")
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"Output directory already exists: {output}")
    if args.att.stem != args.atg.stem:
        raise SystemExit("ATT and ATG file names must have the same stem")

    request = BatchRequest(
        job_id=args.job_id or {
            "baseline": "EXPERIMENT-E0",
            "nonzero_mad_fallback": "EXPERIMENT-E1",
            "gap_one_missing": "EXPERIMENT-E2",
            "optimized_vehicle": "EXPERIMENT-OPT",
        }[args.method],
        monitoring_data_id=args.att.stem,
        att_path=args.att.resolve(),
        atg_path=args.atg.resolve(),
        output_dir=output,
        frame_start=args.frame_start,
        frame_end=args.frame_end,
    )
    config = BatchConfig(analysis_device=args.device, max_results=args.max_results)
    optimized = None
    if args.method == "optimized_vehicle":
        from optimized_vehicle import OptimizedVehicleRun

        optimized = OptimizedVehicleRun(request.att_path, config, args.vehicle_features,
                                        args.vehicle_labels, args.decode_signed)
    track_export = output / "eligible_tracks.jsonl.gz"
    original_select = batch._select_tracks_across_frames
    original_build_tracks = batch.build_tracks
    original_score_maps = batch.score_maps
    exported_count = 0
    fallback_count = 0
    calibration = None
    if args.calibrate_detectors:
        from score_scale import RecentScoreCalibration

        calibration = RecentScoreCalibration(config.algorithms, config.score_threshold, args.frame_start)
    current_frame = None
    adjusted_frames = 0
    original_merge = batch._merge_candidates

    def calibrated_merge(rows, threshold):
        nonlocal adjusted_frames
        if current_frame is None:
            raise RuntimeError("Read a thermal frame before calibrating its candidates")
        uncalibrated = [{**row, "source_algorithm": row["algorithms"][0]} for row in rows]
        adjusted, gains = calibration.apply(current_frame, uncalibrated)
        adjusted_frames += any(gain != 1.0 for gain in gains.values())
        return original_merge(adjusted, threshold)

    def stable_scale(residual):
        nonlocal fallback_count
        median = np.median(residual)
        deviations = np.abs(residual - median)
        mad = float(np.median(deviations)) * 1.4826
        if mad <= 1e-5:
            # A top-hat map can have more than half of its residuals at zero.
            # Use the nonzero residual distribution instead of dividing by 1e-6.
            nonzero = deviations[deviations > 0.005]
            mad = max(0.05, float(np.median(nonzero)) * 1.4826) if nonzero.size else 0.05
            fallback_count += 1
        return ((residual - median) / (mad + 1e-6)).astype(np.float32)

    def stable_robust_z_score(grid, method="gaussian", param=45):
        return stable_scale(grid - anomaly.compute_background(grid, method, param))

    def build_tracks_with_one_missing_frame(by_frame, max_dist, max_gap):
        # Difference <= 2 bridges exactly one missing frame at stride 1.
        return original_build_tracks(by_frame, max_dist=max_dist, max_gap=max_gap + 1)

    def stable_score_maps(grid, selected_algorithms, device):
        nonlocal fallback_count
        if device == "cuda":
            maps, count = stable_cuda_score_maps(grid, selected_algorithms)
            fallback_count += count
            return maps
        return original_score_maps(grid, selected_algorithms, device)

    def export_then_select(ranked_tracks, frame_start, frame_end, limit):
        nonlocal exported_count
        baseline_selection = original_select(ranked_tracks, frame_start, frame_end, limit)
        selected = baseline_selection
        if optimized is not None:
            rescored = optimized.rank(ranked_tracks)
            selected = original_select(rescored, frame_start, frame_end, limit)
            optimized.write_selection(output / "vehicle_selection.json", baseline_selection, selected)
        if args.representative_suppression:
            from functools import partial

            from e3_rules import matches
            from ego_motion import select_distinct

            views = [{**row, "points": row["track"]["points"]} for row in ranked_tracks]
            selected, _ = select_distinct(views, frame_start, frame_end, limit,
                                          match=partial(matches, representative_radius=6))
        with gzip.open(track_export, "wt", encoding="utf-8") as stream:
            for item in ranked_tracks:
                row = {
                    "summary": item["summary"],
                    "representative_frame": item["representative_frame"],
                    "representative": item["representative"],
                    "persistence_score": item["persistence_score"],
                    "points": item["track"]["points"],
                }
                if optimized is not None:
                    row.update(features=item["features"], vehicle_rerank=item["vehicle_rerank"])
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                exported_count += 1
        return selected

    last_progress = (None, -5)

    def report_progress(update):
        nonlocal last_progress
        marker = (update.status, update.progress_percent // 5)
        if marker != last_progress:
            print(
                f"{update.status} {update.progress_percent}% "
                f"({update.processed_frame_count}/{update.total_frame_count or '?'})",
                flush=True,
            )
            last_progress = marker

    def read_signed_temperature(path, index, header=None):
        raw = read_att_frame_raw(path, index, header)
        return raw.view("<i2").astype(np.float32) / TEMP_SCALE

    # Capture the pre-cap tracks without changing the deployed batch pipeline.
    with ExitStack() as stack:
        stack.enter_context(patch.object(batch, "_select_tracks_across_frames", export_then_select))
        if optimized is not None:
            from heatpipe_service.analysis import candidates

            stack.enter_context(patch.object(batch, "read_att_frame_celsius", optimized.read_temperature))
            stack.enter_context(patch.object(candidates, "edge_sharpness", optimized.edge_sharpness))
        elif args.decode_signed:
            stack.enter_context(patch.object(batch, "read_att_frame_celsius", read_signed_temperature))
        if args.fast_percentile:
            from exact_percentile import local_percentile

            stack.enter_context(patch.object(algorithms, "percentile_filter", local_percentile))
        if calibration is not None:
            read_temperature = batch.read_att_frame_celsius

            def read_with_frame_index(path, index, header=None):
                nonlocal current_frame
                current_frame = index
                return read_temperature(path, index, header)

            stack.enter_context(patch.object(batch, "read_att_frame_celsius", read_with_frame_index))
            stack.enter_context(patch.object(batch, "_merge_candidates", calibrated_merge))
        if args.method in {"nonzero_mad_fallback", "optimized_vehicle"}:
            stack.enter_context(patch.object(algorithms, "_robust_scale", stable_scale))
            stack.enter_context(
                patch.object(algorithms, "robust_z_score_map", stable_robust_z_score)
            )
            stack.enter_context(patch.object(batch, "score_maps", stable_score_maps))
        elif args.method == "gap_one_missing":
            stack.enter_context(patch.object(batch, "build_tracks", build_tracks_with_one_missing_frame))
        artifacts = run_batch_safely(request, config, on_progress=report_progress)
    result = json.loads(artifacts.result_json.read_text(encoding="utf-8"))
    summary = {
        "method": args.method,
        "device": args.device,
        "temperature_decode": "signed_int16_experiment" if args.decode_signed else "unsigned_int16",
        "detector_score_calibration": "recent_components" if calibration is not None else None,
        "calibration_adjusted_frames": adjusted_frames,
        "fast_percentile": args.fast_percentile,
        "representative_suppression": args.representative_suppression,
        "status": result["status"],
        "processed_frame_count": (result.get("processing") or {}).get("frame_range", {}).get("processed_frame_count"),
        "detected_candidate_count": result.get("detected_candidate_count"),
        "candidate_count": result["candidate_count"],
        "exported_track_count": exported_count,
        "fallback_scale_count": fallback_count,
        "eligible_tracks_jsonl_gz": str(track_export) if track_export.exists() else None,
        "elapsed_seconds": (result.get("processing") or {}).get("elapsed_seconds"),
        "result_json": str(artifacts.result_json),
    }
    if optimized is not None:
        summary["vehicle_selection"] = str(output / "vehicle_selection.json") if artifacts.success else None
    (output / "experiment_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if artifacts.success else 1


if __name__ == "__main__":
    raise SystemExit(main())

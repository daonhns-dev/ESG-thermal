"""ATT/ATG to JSON, CSV and candidate PNG batch pipeline."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Callable

import numpy as np

from heatpipe_service.cancellation import JobCancelled
from heatpipe_service.security import redact_url_query
from heatpipe_service.analysis.algorithms import V0_CANDIDATE_ALGORITHMS, score_maps
from heatpipe_service.analysis.candidates import extract_candidates
from heatpipe_service.analysis.io import (
    AtgRecord,
    att_frame_count,
    read_atg,
    read_att_frame_celsius,
    read_att_header,
)
from heatpipe_service.analysis.rendering import draw_result_image, imwrite_unicode
from heatpipe_service.analysis.roi import Roi, clip_roi, grid_cell_roi
from heatpipe_service.analysis.tracking import build_tracks, summarize_track

SCHEMA_VERSION = "0.3.0"
KST = ZoneInfo("Asia/Seoul")

def format_captured_at(timestamp: str) -> str:
    captured = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
    return captured.replace(tzinfo=KST).isoformat()

@dataclass(frozen=True)
class BatchRequest:
    job_id: str
    monitoring_data_id: str
    att_path: Path
    atg_path: Path
    output_dir: Path
    frame_start: int = 0
    frame_end: int | None = None
    analysis_roi: Roi | None = None


@dataclass(frozen=True)
class BatchConfig:
    algorithms: tuple[str, ...] = V0_CANDIDATE_ALGORITHMS
    analysis_device: str = "cpu"
    frame_stride: int = 1
    score_threshold: float = 3.0
    min_area: int = 20
    max_area_fraction: float = 0.25
    merge_iou_threshold: float = 0.35
    max_track_distance: float = 25.0
    max_track_gap: int = 1
    min_track_frames: int = 3
    max_results: int = 50
    display_grid_rows: int = 2
    display_grid_columns: int = 3
    bottom_margin: int = 1
    growth_threshold: float = 1.3
    min_circularity: float = 0.35


@dataclass(frozen=True)
class BatchArtifacts:
    result_json: Path
    candidates_csv: Path
    image_paths: tuple[Path, ...] = field(default_factory=tuple)
    success: bool = True


@dataclass(frozen=True)
class BatchProgress:
    status: str
    progress_percent: int
    processed_frame_count: int
    total_frame_count: int | None
    message: str
    error_code: str | None = None


ProgressCallback = Callable[[BatchProgress], None]
CancelCheck = Callable[[], bool]


def run_batch(
    request: BatchRequest,
    config: BatchConfig | None = None,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> BatchArtifacts:
    """Execute one monitoring-data analysis and materialize all local artifacts."""
    config = config or BatchConfig()
    started = time.perf_counter()
    if should_cancel and should_cancel():
        raise JobCancelled()
    if on_progress:
        on_progress(BatchProgress("VALIDATING", 1, 0, None, "Validating ATT/ATG input"))
    _validate_request(request, config)

    header = read_att_header(request.att_path)
    total_frames = att_frame_count(request.att_path, header)
    atg_records = read_atg(request.atg_path)
    frame_start, frame_end = _resolve_frame_range(request, total_frames)
    selected_frame_count = len(range(frame_start, frame_end, config.frame_stride))
    default_roi = grid_cell_roi(
        (header.height, header.width), config.display_grid_rows,
        config.display_grid_columns, config.display_grid_rows - 1,
        (config.display_grid_columns - 1) // 2,
    )
    requested_roi = request.analysis_roi or default_roi
    if (requested_roi[0] < default_roi[0] or requested_roi[1] < default_roi[1]
            or requested_roi[2] > default_roi[2] or requested_roi[3] > default_roi[3]
            or requested_roi[2] <= requested_roi[0] or requested_roi[3] <= requested_roi[1]):
        raise ValueError("analysis_roi must be within the bottom-center grid cell")
    analysis_roi = clip_roi((header.height, header.width), requested_roi)

    output_dir = Path(request.output_dir)
    image_dir = output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)

    by_frame: dict[int, list[dict[str, Any]]] = {}
    processed_frames: list[int] = []
    empty_frames = 0
    if on_progress:
        on_progress(BatchProgress("ANALYZING", 10, 0, selected_frame_count, "Analyzing thermal frames"))
    for examined_count, frame_index in enumerate(range(frame_start, frame_end, config.frame_stride), start=1):
        if should_cancel and should_cancel():
            raise JobCancelled()
        frame_c = read_att_frame_celsius(request.att_path, frame_index, header)
        if not np.isfinite(frame_c).all() or float(np.max(frame_c)) <= 0:
            empty_frames += 1
        else:
            processed_frames.append(frame_index)
            candidates = _frame_candidates(frame_c, analysis_roi, config)
            if candidates:
                by_frame[frame_index] = candidates
        if on_progress:
            on_progress(BatchProgress(
                "ANALYZING", 10 + 75 * examined_count // selected_frame_count,
                len(processed_frames), selected_frame_count,
                f"Examined {examined_count} of {selected_frame_count} frames",
            ))

    if should_cancel and should_cancel():
        raise JobCancelled()
    tracks = build_tracks(
        by_frame,
        max_dist=config.max_track_distance,
        max_gap=max(config.max_track_gap, config.max_track_gap * config.frame_stride),
    )
    ranked_tracks = _rank_tracks(
        tracks,
        header.height,
        config,
    )
    eligible_tracks = [
        item for item in ranked_tracks if int(item["summary"]["n_frames"]) >= config.min_track_frames
    ]
    selected_tracks = _select_tracks_across_frames(
        eligible_tracks, frame_start, frame_end, config.max_results
    )
    if should_cancel and should_cancel():
        raise JobCancelled()

    result_candidates: list[dict[str, Any]] = []
    image_paths: list[Path] = []
    if on_progress:
        on_progress(BatchProgress("RENDERING", 90, len(processed_frames), selected_frame_count, "Rendering result images"))
    for rank, item in enumerate(selected_tracks, start=1):
        if should_cancel and should_cancel():
            raise JobCancelled()
        candidate_id = f"{request.job_id}-CAND-{rank:04d}"
        candidate, image_path = _materialize_candidate(
            request,
            header,
            atg_records,
            item,
            candidate_id,
            rank,
            analysis_roi,
            image_dir,
            config,
        )
        result_candidates.append(candidate)
        image_paths.append(image_path)

    warnings = _data_warnings(total_frames, len(atg_records), empty_frames)
    completed_at = _utc_now()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "job_id": request.job_id,
        "monitoring_data_id": request.monitoring_data_id,
        "status": "SUCCEEDED",
        "success": True,
        "completed_at": completed_at,
        "processing": {
            "input": {
                "att_file_name": request.att_path.name,
                "atg_file_name": request.atg_path.name,
            },
            "frame_range": {
                "start": frame_start,
                "end_exclusive": frame_end,
                "stride": config.frame_stride,
                "total_att_frames": total_frames,
                "total_atg_records": len(atg_records),
                "processed_frame_count": len(processed_frames),
            },
            "analysis_roi": _roi_json(analysis_roi),
            "algorithms": list(config.algorithms),
            "parameters": {
                "score_threshold": config.score_threshold,
                "min_area_px": config.min_area,
                "min_track_frames": config.min_track_frames,
                "merge_iou_threshold": config.merge_iou_threshold,
                "max_results": config.max_results,
            },
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        },
        "detected_candidate_count": len(eligible_tracks),
        "candidate_count": len(result_candidates),
        "candidates": result_candidates,
        "warnings": warnings,
        "error": None,
    }

    result_json = output_dir / "analysis_result.json"
    candidates_csv = output_dir / "analysis_candidates.csv"
    if should_cancel and should_cancel():
        raise JobCancelled()
    result_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    _write_candidates_csv(candidates_csv, result_candidates)
    if on_progress:
        on_progress(BatchProgress("SUCCEEDED", 100, len(processed_frames), selected_frame_count, "Analysis complete"))
    return BatchArtifacts(result_json, candidates_csv, tuple(image_paths))


def run_batch_safely(
    request: BatchRequest,
    config: BatchConfig | None = None,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> BatchArtifacts:
    """Run a batch and persist a schema-valid FAILED result for operational errors."""
    last_progress: BatchProgress | None = None

    def report(update: BatchProgress) -> None:
        nonlocal last_progress
        if on_progress:
            on_progress(update)
        last_progress = update

    try:
        return run_batch(request, config, report if on_progress else None, should_cancel)
    except JobCancelled:
        raise
    except Exception as error:
        safe_message = redact_url_query(str(error) or error.__class__.__name__)
        output_dir = Path(request.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        result_json = output_dir / "analysis_result.json"
        candidates_csv = output_dir / "analysis_candidates.csv"
        payload = {
            "schema_version": SCHEMA_VERSION,
            "job_id": request.job_id,
            "monitoring_data_id": request.monitoring_data_id,
            "status": "FAILED",
            "success": False,
            "completed_at": _utc_now(),
            "processing": None,
            "candidate_count": 0,
            "detected_candidate_count": None,
            "candidates": [],
            "warnings": [],
            "error": {
                "code": _error_code(error),
                "message": safe_message,
            },
        }
        result_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        _write_candidates_csv(candidates_csv, [])
        if on_progress:
            on_progress(BatchProgress(
                "FAILED", last_progress.progress_percent if last_progress else 0,
                last_progress.processed_frame_count if last_progress else 0,
                last_progress.total_frame_count if last_progress else None,
                safe_message,
                error_code=_error_code(error),
            ))
        return BatchArtifacts(result_json, candidates_csv, success=False)


def _validate_request(request: BatchRequest, config: BatchConfig) -> None:
    if not request.monitoring_data_id.strip():
        raise ValueError("monitoring_data_id must not be empty")
    for path, extension in ((request.att_path, ".att"), (request.atg_path, ".atg")):
        if not Path(path).is_file():
            raise FileNotFoundError(path)
        if Path(path).suffix.lower() != extension:
            raise ValueError(f"expected {extension} file: {path}")
    att_stem = Path(request.att_path).stem
    atg_stem = Path(request.atg_path).stem
    if att_stem != atg_stem or request.monitoring_data_id != att_stem:
        raise ValueError(
            "monitoring_data_id and ATT/ATG common file name must be identical"
        )
    if request.frame_start < 0:
        raise ValueError("frame_start must be non-negative")
    if request.frame_end is not None and request.frame_end <= request.frame_start:
        raise ValueError("frame_end must be greater than frame_start")
    if config.frame_stride <= 0:
        raise ValueError("frame_stride must be positive")
    if config.min_track_frames <= 0 or not 1 <= config.max_results <= 1000:
        raise ValueError("min_track_frames must be positive and max_results must be 1..1000")
    if not config.algorithms:
        raise ValueError("at least one algorithm is required")
    if config.analysis_device not in {"cpu", "cuda"}:
        raise ValueError("analysis_device must be cpu or cuda")


def _resolve_frame_range(request: BatchRequest, total_frames: int) -> tuple[int, int]:
    if request.frame_start >= total_frames:
        raise ValueError(
            f"frame_start {request.frame_start} is outside ATT frame count {total_frames}"
        )
    end = min(request.frame_end or total_frames, total_frames)
    return request.frame_start, end


def _frame_candidates(frame_c: np.ndarray, roi: Roi, config: BatchConfig,) -> list[dict[str, Any]]:
    x0, y0, x1, y1 = roi
    roi_frame = frame_c[y0:y1, x0:x1]
    rows: list[dict[str, Any]] = []
    for algorithm, scores in zip(
        config.algorithms, score_maps(roi_frame, config.algorithms, config.analysis_device), strict=True,
    ):
        detected = extract_candidates(
            roi_frame,
            scores,
            score_thresh=config.score_threshold,
            min_area=config.min_area,
            max_area_frac=config.max_area_fraction,
            offset_y=y0,
            offset_x=x0,
        )
        for candidate in detected:
            candidate["algorithms"] = [algorithm]
        rows.extend(detected)
    return _merge_candidates(rows, config.merge_iou_threshold)


def _merge_candidates(candidates: list[dict[str, Any]], iou_threshold: float) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda row: float(row["max_score"]), reverse=True):
        match = next(
            (existing for existing in merged if _bbox_iou(existing, candidate) >= iou_threshold),
            None,
        )
        if match is None:
            merged.append(dict(candidate))
            continue
        match["algorithms"] = sorted(
            set(match.get("algorithms", [])) | set(candidate.get("algorithms", []))
        )
    return merged


def _bbox_iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    x0 = max(int(a["bbox_x0"]), int(b["bbox_x0"]))
    y0 = max(int(a["bbox_y0"]), int(b["bbox_y0"]))
    x1 = min(int(a["bbox_x1"]), int(b["bbox_x1"]))
    y1 = min(int(a["bbox_y1"]), int(b["bbox_y1"]))
    intersection = max(0, x1 - x0 + 1) * max(0, y1 - y0 + 1)
    area_a = (int(a["bbox_x1"]) - int(a["bbox_x0"]) + 1) * (
        int(a["bbox_y1"]) - int(a["bbox_y0"]) + 1
    )
    area_b = (int(b["bbox_x1"]) - int(b["bbox_x0"]) + 1) * (
        int(b["bbox_y1"]) - int(b["bbox_y0"]) + 1
    )
    union = area_a + area_b - intersection
    return intersection / union if union else 0.0


def _rank_tracks(tracks: list[dict[str, Any]], frame_height: int, config: BatchConfig) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for track_id, track in enumerate(tracks):
        summary = summarize_track(
            track,
            track_id,
            frame_height,
            config.bottom_margin,
            config.growth_threshold,
            config.min_circularity,
        )
        representative_frame, representative = max(
            track["points"], key=lambda point: float(point[1]["max_score"])
        )
        span = max(1, int(summary["end_frame"]) - int(summary["start_frame"]) + 1)
        temporal_density = int(summary["n_frames"]) * config.frame_stride / span
        persistence_score = (
            float(summary["mean_max_score"])
            * math.log1p(int(summary["n_frames"]))
            * min(1.0, temporal_density)
        )
        ranked.append(
            {
                "track": track,
                "summary": summary,
                "representative_frame": representative_frame,
                "representative": representative,
                "persistence_score": round(persistence_score, 3),
            }
        )
    return sorted(ranked, key=lambda item: item["persistence_score"], reverse=True)


def _select_tracks_across_frames(
    ranked_tracks: list[dict[str, Any]], frame_start: int, frame_end: int, limit: int
) -> list[dict[str, Any]]:
    """Preserve score order unless capped; then share slots across ten frame intervals."""
    if len(ranked_tracks) <= limit:
        return ranked_tracks
    interval_count = min(10, limit)
    intervals: list[list[dict[str, Any]]] = [[] for _ in range(interval_count)]
    span = frame_end - frame_start
    for item in ranked_tracks:
        offset = max(0, int(item["representative_frame"]) - frame_start)
        interval = min(interval_count - 1, offset * interval_count // span)
        intervals[interval].append(item)
    selected: list[dict[str, Any]] = []
    while len(selected) < limit:
        added = False
        for interval in intervals:
            if interval and len(selected) < limit:
                selected.append(interval.pop(0))
                added = True
        if not added:
            break
    rank_order = {id(item): index for index, item in enumerate(ranked_tracks)}
    return sorted(selected, key=lambda item: rank_order[id(item)])


def _materialize_candidate(
    request: BatchRequest,
    header: Any,
    atg_records: list[AtgRecord],
    track_item: dict[str, Any],
    candidate_id: str,
    rank: int,
    analysis_roi: Roi,
    image_dir: Path,
    config: BatchConfig,
) -> tuple[dict[str, Any], Path]:
    frame_index = int(track_item["representative_frame"])
    representative = track_item["representative"]
    bbox = (
        int(representative["bbox_x0"]),
        int(representative["bbox_y0"]),
        int(representative["bbox_x1"]),
        int(representative["bbox_y1"]),
    )
    frame_c = read_att_frame_celsius(request.att_path, frame_index, header)
    temperature = _temperature_stats(frame_c, bbox)
    roi_temperature = _roi_temperature_stats(frame_c, analysis_roi)
    image_name = f"{request.monitoring_data_id}_{candidate_id}_f{frame_index:06d}.png"
    image_path = image_dir / image_name
    rendered = draw_result_image(
        frame_c,
        bbox,
        candidate_id,
        config.display_grid_rows,
        config.display_grid_columns,
        analysis_roi=analysis_roi,
    )
    if not imwrite_unicode(image_path, rendered, ".png"):
        raise OSError(f"failed to write result image: {image_path}")
    image_bytes = image_path.read_bytes()

    atg = atg_records[frame_index] if frame_index < len(atg_records) else None
    summary = track_item["summary"]
    algorithms = sorted(
        {algorithm for _, point in track_item["track"]["points"] for algorithm in point["algorithms"]}
    )
    return (
        {
            "candidate_id": candidate_id,
            "rank": rank,
            "representative_frame": frame_index,
            "captured_at": format_captured_at(atg.timestamp) if atg else None,
            "gps": {"latitude": atg.lat, "longitude": atg.lon} if atg else None,
            "analysis_roi": _roi_json(analysis_roi),
            "candidate_bbox": _bbox_json(bbox),
            "temperature_c": temperature,
            "roi_temperature_c": roi_temperature,
            "evidence": {
                "algorithms": algorithms,
                "max_anomaly_score": round(float(representative["max_score"]), 4),
                "mean_anomaly_score": round(float(representative["mean_score"]), 4),
                "persistence_score": track_item["persistence_score"],
                "tracked_frame_count": int(summary["n_frames"]),
                "track_start_frame": int(summary["start_frame"]),
                "track_end_frame": int(summary["end_frame"]),
                "likely_ground_fixed": bool(summary["likely_ground_fixed"]),
            },
            "result_image": {
                "format": "PNG",
                "width": int(header.width),
                "height": int(header.height),
                "path": image_path.relative_to(request.output_dir).as_posix(),
                "sha256": hashlib.sha256(image_bytes).hexdigest(),
                "size_bytes": len(image_bytes),
                "grid": {
                    "rows": config.display_grid_rows,
                    "columns": config.display_grid_columns,
                },
            },
        },
        image_path,
    )


def _temperature_stats(frame_c: np.ndarray, bbox: tuple[int, int, int, int]) -> dict[str, float]:
    x0, y0, x1, y1 = bbox
    region = frame_c[y0 : y1 + 1, x0 : x1 + 1]
    pad = 5
    sx0, sy0 = max(0, x0 - pad), max(0, y0 - pad)
    sx1, sy1 = min(frame_c.shape[1], x1 + pad + 1), min(frame_c.shape[0], y1 + pad + 1)
    surroundings = frame_c[sy0:sy1, sx0:sx1].copy()
    mask = np.ones(surroundings.shape, dtype=bool)
    mask[y0 - sy0 : y1 - sy0 + 1, x0 - sx0 : x1 - sx0 + 1] = False
    background = surroundings[mask]
    background_median = float(np.median(background)) if background.size else float(np.median(frame_c))
    peak = float(np.max(region))
    return {
        "minimum": round(float(np.min(region)), 2),
        "mean": round(float(np.mean(region)), 2),
        "maximum": round(peak, 2),
        "background_median": round(background_median, 2),
        "delta_from_background": round(peak - background_median, 2),
    }


def _roi_temperature_stats(frame_c: np.ndarray, roi: Roi) -> dict[str, float]:
    x0, y0, x1, y1 = roi
    pixels = frame_c[y0:y1, x0:x1]
    minimum = round(float(np.min(pixels)), 2)
    mean = round(float(np.mean(pixels)), 2)
    maximum = round(float(np.max(pixels)), 2)
    return {
        "minimum": minimum,
        "mean": mean,
        "maximum": maximum,
        "max_minus_min": round(maximum - minimum, 2),
        "max_minus_mean": round(maximum - mean, 2),
    }


def _roi_json(roi: Roi) -> dict[str, int]:
    x0, y0, x1, y1 = roi
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def _bbox_json(bbox: tuple[int, int, int, int]) -> dict[str, int]:
    x0, y0, x1, y1 = bbox
    return {"x": x0, "y": y0, "width": x1 - x0 + 1, "height": y1 - y0 + 1}


def _data_warnings(att_frames: int, atg_records: int, empty_frames: int) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if att_frames != atg_records:
        warnings.append(
            {
                "code": "ATT_ATG_COUNT_MISMATCH",
                "message": f"ATT frames ({att_frames}) and ATG records ({atg_records}) differ; unmatched candidate metadata is null.",
            }
        )
    if empty_frames:
        warnings.append(
            {
                "code": "EMPTY_THERMAL_FRAMES_SKIPPED",
                "message": f"Skipped {empty_frames} empty or invalid thermal frame(s).",
            }
        )
    return warnings


def _error_code(error: Exception) -> str:
    if isinstance(error, FileNotFoundError):
        return "INPUT_FILE_NOT_FOUND"
    if isinstance(error, ValueError):
        return "INVALID_ANALYSIS_REQUEST"
    if isinstance(error, OSError):
        return "INPUT_OUTPUT_ERROR"
    return "ANALYSIS_FAILED"


def _write_candidates_csv(path: Path, candidates: list[dict[str, Any]]) -> None:
    fields = [
        "candidate_id",
        "rank",
        "representative_frame",
        "captured_at",
        "latitude",
        "longitude",
        "bbox_x",
        "bbox_y",
        "bbox_width",
        "bbox_height",
        "temperature_delta_c",
        "max_anomaly_score",
        "persistence_score",
        "tracked_frame_count",
        "algorithms",
        "result_image_path",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for candidate in candidates:
            gps = candidate["gps"] or {}
            bbox = candidate["candidate_bbox"]
            evidence = candidate["evidence"]
            writer.writerow(
                {
                    "candidate_id": candidate["candidate_id"],
                    "rank": candidate["rank"],
                    "representative_frame": candidate["representative_frame"],
                    "captured_at": candidate["captured_at"],
                    "latitude": gps.get("latitude"),
                    "longitude": gps.get("longitude"),
                    "bbox_x": bbox["x"],
                    "bbox_y": bbox["y"],
                    "bbox_width": bbox["width"],
                    "bbox_height": bbox["height"],
                    "temperature_delta_c": candidate["temperature_c"]["delta_from_background"],
                    "max_anomaly_score": evidence["max_anomaly_score"],
                    "persistence_score": evidence["persistence_score"],
                    "tracked_frame_count": evidence["tracked_frame_count"],
                    "algorithms": ";".join(evidence["algorithms"]),
                    "result_image_path": candidate["result_image"]["path"],
                }
            )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--monitoring-data-id", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--att", type=Path, required=True)
    parser.add_argument("--atg", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frame-start", type=int, default=0)
    parser.add_argument("--frame-end", type=int)
    parser.add_argument("--frame-stride", type=int, default=1)
    parser.add_argument("--min-track-frames", type=int, default=3)
    parser.add_argument("--max-results", type=int, default=50)
    parser.add_argument("--roi", nargs=4, type=int, metavar=("X0", "Y0", "X1", "Y1"))
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    request = BatchRequest(
        job_id=args.job_id,
        monitoring_data_id=args.monitoring_data_id,
        att_path=args.att,
        atg_path=args.atg,
        output_dir=args.output,
        frame_start=args.frame_start,
        frame_end=args.frame_end,
        analysis_roi=tuple(args.roi) if args.roi else None,
    )
    config = BatchConfig(
        analysis_device=args.device,
        frame_stride=args.frame_stride,
        min_track_frames=args.min_track_frames,
        max_results=args.max_results,
    )
    artifacts = run_batch_safely(request, config)
    print(artifacts.result_json)
    if not artifacts.success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

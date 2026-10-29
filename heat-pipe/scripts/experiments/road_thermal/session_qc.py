"""Audit raw ATT/ATG/AVI sessions without modifying the source files."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.experiments.road_thermal._att_io import (  # noqa: E402
    ATG_RECORD_SIZE,
    ATT_HEADER_SIZE,
    att_frame_count,
    read_atg,
    read_att_frame_raw,
    read_att_header,
)


def inspect_session(att_path: Path, zero_sample_stride: int) -> dict:
    avi_path = att_path.with_suffix(".avi")
    atg_path = att_path.with_suffix(".atg")
    row = {
        "session": att_path.stem,
        "att_path": str(att_path.resolve()),
        "avi_path": str(avi_path.resolve()) if avi_path.exists() else None,
        "atg_path": str(atg_path.resolve()) if atg_path.exists() else None,
        "issues": [],
    }

    try:
        header = read_att_header(att_path)
        if header.width <= 0 or header.height <= 0:
            raise ValueError("invalid ATT dimensions")
        row["thermal_size"] = [header.width, header.height]
        row["att_header_version"] = header.version
        if (header.width, header.height) != (384, 288):
            row["issues"].append("unexpected_att_dimensions")
        remainder = (att_path.stat().st_size - ATT_HEADER_SIZE) % header.frame_bytes
        row["att_tail_bytes"] = remainder
        if remainder:
            row["issues"].append("partial_att_frame")
            row["thermal_frames"] = (
                att_path.stat().st_size - ATT_HEADER_SIZE
            ) // header.frame_bytes
        else:
            row["thermal_frames"] = att_frame_count(att_path, header)
        if row["thermal_frames"] == 0:
            row["issues"].append("empty_att")
    except (OSError, ValueError) as exc:
        row["issues"].append(f"att_unreadable: {exc}")
        return row

    if atg_path.exists():
        row["atg_tail_bytes"] = atg_path.stat().st_size % ATG_RECORD_SIZE
        records = read_atg(atg_path)
        row["atg_records"] = len(records)
        if row["atg_tail_bytes"]:
            row["issues"].append("partial_atg_record")
        if len(records) != row["thermal_frames"]:
            row["issues"].append("att_atg_count_mismatch")

        times = []
        invalid_timestamps = 0
        invalid_gps = 0
        for record in records:
            try:
                times.append(datetime.fromisoformat(record.timestamp))
            except ValueError:
                invalid_timestamps += 1
            if not (
                math.isfinite(record.lat)
                and math.isfinite(record.lon)
                and -90 <= record.lat <= 90
                and -180 <= record.lon <= 180
                and (record.lat, record.lon) != (0.0, 0.0)
            ):
                invalid_gps += 1

        row["invalid_timestamps"] = invalid_timestamps
        row["invalid_gps_records"] = invalid_gps
        if invalid_timestamps:
            row["issues"].append("invalid_atg_timestamps")
        if invalid_gps:
            row["issues"].append("invalid_gps_coordinates")
        if times:
            row["first_timestamp"] = times[0].isoformat(sep=" ")
            row["last_timestamp"] = times[-1].isoformat(sep=" ")
            steps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
            row["timestamp_backsteps"] = sum(step < 0 for step in steps)
            row["timestamp_gaps_over_1s"] = sum(step > 1 for step in steps)
            row["timestamp_zero_steps"] = sum(step == 0 for step in steps)
            row["max_timestamp_step_seconds"] = max(steps, default=0)
            elapsed = (times[-1] - times[0]).total_seconds()
            row["elapsed_seconds"] = elapsed
            row["mean_record_rate_hz"] = (
                round((len(times) - 1) / elapsed, 3) if elapsed > 0 else None
            )
            if row["timestamp_backsteps"]:
                row["issues"].append("timestamp_backsteps")
            if row["timestamp_gaps_over_1s"]:
                row["issues"].append("timestamp_gaps_over_1s")
    else:
        row["issues"].append("atg_missing")

    if avi_path.exists():
        capture = cv2.VideoCapture(str(avi_path))
        try:
            if capture.isOpened():
                row["avi_metadata_frames"] = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
                row["avi_nominal_fps"] = capture.get(cv2.CAP_PROP_FPS)
                if row["avi_metadata_frames"] != row["thermal_frames"]:
                    row["issues"].append("att_avi_metadata_count_mismatch")
            else:
                row["issues"].append("avi_unreadable")
        finally:
            capture.release()
    else:
        row["issues"].append("avi_missing")

    count = row["thermal_frames"]
    indices = sorted(set(range(0, count, zero_sample_stride)) | ({count - 1} if count else set()))
    zero_indices = []
    for index in indices:
        if not np.any(read_att_frame_raw(att_path, index, header)):
            zero_indices.append(index)
    row["thermal_frames_sampled"] = len(indices)
    row["sampled_zero_frames"] = zero_indices[:20]
    row["sampled_zero_count"] = len(zero_indices)
    row["zero_scan_complete"] = len(indices) == count
    if zero_indices:
        row["issues"].append("sampled_zero_thermal_frames")
    return row


def audit(source: Path, zero_sample_stride: int) -> dict:
    if not source.is_dir():
        raise ValueError(f"source is not a directory: {source}")
    att_paths = sorted(source.rglob("*.att"))
    if not att_paths:
        raise ValueError(f"no ATT files found under {source}")
    sessions = [inspect_session(path, zero_sample_stride) for path in att_paths]
    return {
        "schema_version": 1,
        "source": str(source.resolve()),
        "zero_sample_stride": zero_sample_stride,
        "sessions": sessions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--zero-sample-stride", type=int, default=30)
    args = parser.parse_args()
    if args.zero_sample_stride < 1:
        parser.error("--zero-sample-stride must be at least 1")
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    try:
        report = audit(args.source, args.zero_sample_stride)
    except ValueError as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Audited {len(report['sessions'])} sessions: {args.output}")


if __name__ == "__main__":
    main()

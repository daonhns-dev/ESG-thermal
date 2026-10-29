"""Assess RGB/thermal frame offsets from independently annotated shared events."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

REQUIRED_COLUMNS = (
    "session",
    "event_id",
    "rgb_idx",
    "thermal_idx",
    "rgb_uncertainty_frames",
    "thermal_uncertainty_frames",
    "description",
)


def read_events(path: Path, session_by_name: dict[str, dict]) -> dict[str, list[dict]]:
    events: dict[str, list[dict]] = defaultdict(list)
    seen_ids = set()
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        missing = set(REQUIRED_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"missing event columns: {sorted(missing)}")
        for line_number, row in enumerate(reader, start=2):
            try:
                if None in row:
                    raise ValueError("event row has extra values")
                if any(row.get(column) is None for column in REQUIRED_COLUMNS):
                    raise ValueError("event row has missing values")
                session = row["session"].strip()
                event_id = row["event_id"].strip()
                description = row["description"].strip()
                rgb_idx = int(row["rgb_idx"])
                thermal_idx = int(row["thermal_idx"])
                rgb_uncertainty = float(row["rgb_uncertainty_frames"])
                thermal_uncertainty = float(row["thermal_uncertainty_frames"])
                if not session or not event_id or not description:
                    raise ValueError("session, event_id and description are required")
                if session not in session_by_name:
                    raise ValueError(f"unknown session: {session}")
                if (session, event_id) in seen_ids:
                    raise ValueError(f"duplicate event_id: {event_id}")
                if min(rgb_idx, thermal_idx, rgb_uncertainty, thermal_uncertainty) < 0:
                    raise ValueError("indices and uncertainties must be nonnegative")
                if not np.isfinite(rgb_uncertainty + thermal_uncertainty):
                    raise ValueError("uncertainties must be finite")
                info = session_by_name[session]
                rgb_count = info.get("avi_metadata_frames")
                thermal_count = info.get("thermal_frames")
                if rgb_count is None or thermal_count is None:
                    raise ValueError("session has no readable AVI or ATT frame count")
                if rgb_idx >= rgb_count or thermal_idx >= thermal_count:
                    raise ValueError("event index exceeds the recorded frame count")
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
            seen_ids.add((session, event_id))
            events[session].append({
                "event_id": event_id,
                "rgb_idx": rgb_idx,
                "thermal_idx": thermal_idx,
                "offset_frames": thermal_idx - rgb_idx,
                "uncertainty_frames": rgb_uncertainty + thermal_uncertainty,
                "description": description,
            })
    return events


def summarize_session(info: dict, events: list[dict]) -> dict:
    ordered = sorted(events, key=lambda event: event["rgb_idx"])
    rgb_count = info.get("avi_metadata_frames")
    phases = sorted({
        min(2, 3 * event["rgb_idx"] // max(rgb_count, 1))
        for event in ordered
    }) if rgb_count else []
    summary = {
        "session": info["session"],
        "events": ordered,
        "event_count": len(ordered),
        "covered_phases": phases,
        "qc_issues": info.get("issues", []),
        "classification": "unconfirmed_insufficient_coverage",
    }
    if not ordered:
        return summary

    x = np.asarray([event["rgb_idx"] for event in ordered], dtype=float)
    offsets = np.asarray([event["offset_frames"] for event in ordered], dtype=float)
    uncertainty = np.asarray([event["uncertainty_frames"] for event in ordered], dtype=float)
    summary["offset_range_frames"] = [float(offsets.min()), float(offsets.max())]

    lower = float(np.max(offsets - uncertainty))
    upper = float(np.min(offsets + uncertainty))
    if lower <= upper:
        summary["compatible_constant_offset_interval_frames"] = [lower, upper]

    if len(ordered) < 3 or phases != [0, 1, 2] or len(set(x)) < 3:
        return summary
    if lower <= upper:
        summary["classification"] = "constant_offset_compatible"
        return summary

    slope, intercept = np.polyfit(x, offsets, deg=1)
    residual = offsets - (slope * x + intercept)
    summary["linear_drift_frames_per_1000_rgb_frames"] = float(slope * 1000)
    summary["linear_max_residual_frames"] = float(np.max(np.abs(residual)))
    if np.all(np.abs(residual) <= uncertainty + 1e-6):
        summary["classification"] = "linear_drift_compatible"
    else:
        summary["classification"] = "inconsistent_offsets"
    return summary


def audit(manifest: dict, events_path: Path) -> dict:
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported QC manifest schema_version")
    session_by_name = {row["session"]: row for row in manifest["sessions"]}
    if len(session_by_name) != len(manifest["sessions"]):
        raise ValueError("duplicate session names in QC manifest")
    events = read_events(events_path, session_by_name)
    return {
        "schema_version": 1,
        "events_path": str(events_path.resolve()),
        "interpretation": (
            "Compatible means not contradicted by these annotations; "
            "it does not prove exposure-time synchronization."
        ),
        "sessions": [
            summarize_session(info, events.get(name, []))
            for name, info in sorted(session_by_name.items())
            if info.get("avi_metadata_frames") is not None
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        report = audit(manifest, args.events)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    counts = defaultdict(int)
    for session in report["sessions"]:
        counts[session["classification"]] += 1
    print(f"Audited {len(report['sessions'])} sessions: {dict(counts)}")
    print(args.output)


if __name__ == "__main__":
    main()

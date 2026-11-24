"""Pilot E2 camera motion compensation on two short ATT windows."""

from __future__ import annotations

import argparse
from collections import Counter
from itertools import pairwise
from pathlib import Path

import cv2
import numpy as np

from compare_score_scale import PROJECT, batch, read_components
from camera_motion import build_tracks_with_motion, estimate_motion, stitch_with_motion
from prepare_visual_review import (
    COLORS, colorize, coordinates, label_image, read_att_frame_celsius,
    read_att_header, write_json, write_png,
)


WINDOWS = (("20260812_012856", 2630, 61), ("20260814_122052", 2220, 80))
METHODS = ("e1_tracking", "camera_motion")


def track_links(tracks):
    return {(frame, id(point)): (point, later, next_point)
            for track in tracks
            for (frame, point), (later, next_point) in pairwise(track["points"])}


def summarize(tracks, config):
    eligible = [track for track in tracks if len(track["points"]) >= config.min_track_frames]
    links = track_links(tracks)
    return {"links": len(links), "tracks": len(tracks), "eligible_tracks": len(eligible)}


def changed_links(sides):
    keys = dict.fromkeys(key for side in sides for key in side)
    changes = []
    for frame, identity in keys:
        options = [side.get((frame, identity)) for side in sides]
        old, new = options
        if old is not None and new is not None and old[2] is new[2]:
            continue
        changes.append((frame, (old or new)[0], options))
    return sorted(changes, key=lambda row: (row[0], coordinates(row[1])))


def render_change(path, tag, frames, frame, source, options):
    marked = {frame: [("S", coordinates(source), COLORS[0])]}
    for marker, option, color in zip(("A", "B"), options, COLORS[1:3], strict=True):
        if option is not None:
            _, end, dest = option
            marked.setdefault(end, []).append((marker, coordinates(dest), color))
    # Keep the same image scale while stepping through consecutive frames.
    indices = range(frame - 1, frame + 3)
    arrays = [frames[i] for i in indices]
    low, high = np.percentile(np.stack(arrays), (2, 99.9))
    panels = [label_image(colorize(raw, float(low), float(high)), f"{tag} frame {i}",
                          marked.get(i, [])) for i, raw in zip(indices, arrays, strict=True)]
    write_png(path, np.vstack([np.hstack(panels[:2]), np.hstack(panels[2:])]))


def render_motion(path, before, after, tag, detail):
    low, high = np.percentile(np.stack((before, after)), (2, 99.9))
    panels = [label_image(colorize(raw, float(low), float(high)), f"{tag} {side}", [])
              for raw, side in zip((before, after), ("previous", "current"), strict=True)]
    points = zip(detail["source_points"], detail["target_points"], strict=True)
    width = before.shape[1]
    review = np.hstack(panels)
    for a, b in points:
        x, y = map(lambda n: int(round(n)), a)
        xx, yy = map(lambda n: int(round(n)), b)
        cv2.circle(review, (x, y + 22), 2, COLORS[1], -1)
        cv2.circle(review, (xx + width, yy + 22), 2, COLORS[1], -1)
    write_png(path, review)


def compare_window(att, start, length, config, output, first_case, road_band_start):
    components, _ = read_components(att, start, length, config)
    by_frame = {frame: batch._merge_candidates(rows, config.merge_iou_threshold)
                for frame, rows in components.items()}
    header = read_att_header(att)
    frames = {frame: read_att_frame_celsius(att, frame, header) for frame in components}
    roi = batch.grid_cell_roi((header.height, header.width), 2, 3, 1, 1)
    transforms, diagnostics = {}, {}
    for frame in sorted(frames):
        if frame - 1 in frames:
            transform, detail = estimate_motion(frames[frame - 1], frames[frame],
                                                by_frame[frame - 1], by_frame[frame], roi, road_band_start)
            if transform is not None:
                transforms[frame] = transform
                detail["matrix"] = transform.tolist()
            diagnostics[frame] = detail
    baseline = batch.build_tracks(by_frame, config.max_track_distance, 1)
    tracks = (baseline, build_tracks_with_motion(by_frame, config.max_track_distance, transforms))
    sides = [track_links(item) for item in tracks]
    no_transform = track_links(build_tracks_with_motion(by_frame, config.max_track_distance, {}))
    if no_transform != sides[0]:
        raise ValueError("Camera motion fallback changed existing links")
    protected = stitch_with_motion(baseline, transforms)
    protected_links = track_links(protected)
    if any(protected_links.get(key) != value for key, value in sides[0].items()):
        raise ValueError("Protected stitching changed a baseline link")
    expected = {(frame, id(point)) for frame, candidates in by_frame.items() for point in candidates}
    for variant in (*tracks, protected):
        assigned = [(frame, id(point)) for track in variant for frame, point in track["points"]]
        if len(assigned) != len(expected) or set(assigned) != expected:
            raise ValueError("Candidate duplication or loss")
    reviewed = []
    changed = changed_links(sides)
    for frame, source, links in changed:
        if not all(i in frames for i in range(frame - 1, frame + 3)):
            continue
        tag = f"M{first_case + len(reviewed):02d}"
        render_change(output / f"{tag}.png", tag, frames, frame, source, links)
        reviewed.append({"id": tag, "stem": att.stem, "frame": frame, "source": coordinates(source),
                         "targets": {method: None if link is None else
                                     {"frame": link[1], "box": coordinates(link[2])}
                                     for method, link in zip(METHODS, links, strict=True)}})
    added = changed_links((sides[0], protected_links))
    protected_cases = []
    for frame, source, links in added:
        if not all(i in frames for i in range(frame - 1, frame + 3)):
            continue
        tag = f"P{att.stem[:8]}-{frame}-{len(protected_cases) + 1}"
        render_change(output / f"{tag}.png", tag, frames, frame, source, links)
        _, end, dest = links[1]
        protected_cases.append({"id": tag, "stem": att.stem, "frame": frame,
                                "source": coordinates(source), "target": {"frame": end, "box": coordinates(dest)}})
    chosen = sorted(transforms, key=lambda f: -diagnostics[f]["center_shift_px"])[:3]
    for frame in chosen:
        tag = f"{att.stem} frame {frame}"
        render_motion(output / f"{att.stem}-{frame}-features.png", frames[frame - 1],
                      frames[frame], tag, diagnostics[frame])
    return {
        "stem": att.stem, "start": start, "frames": len(frames),
        "frame_candidates": sum(map(len, by_frame.values())), "road_band_start": road_band_start,
        "motion_reasons": dict(Counter(x["reason"] for x in diagnostics.values())),
        "tracking": {method: summarize(item, config)
                     for method, item in zip(METHODS, tracks, strict=True)},
        "changed_link_sources": len(changed), "cases": reviewed, "motion": diagnostics,
        "protected_stitch": {**summarize(protected, config), "added_links": len(added),
                             "cases": protected_cases},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--road-band-start", type=float, default=0.70)
    args = parser.parse_args()
    if not 0.5 <= args.road_band_start < 0.94:
        parser.error("--road-band-start must be between 0.50 and 0.94 (exclusive)")
    args.output.mkdir(parents=True)
    cv2.setRNGSeed(0)
    config = batch.BatchConfig()
    results = []
    case_index = 1
    for stem, start, length in WINDOWS:
        att = PROJECT / "data/experiments/raw" / stem[:8] / f"{stem}.att"
        row = compare_window(att, start, length, config, args.output, case_index, args.road_band_start)
        case_index += len(row["cases"])
        results.append(row)
        print(stem, row["motion_reasons"], row["tracking"], row["changed_link_sources"],
              row["protected_stitch"]["added_links"], flush=True)
    write_json(args.output / "comparison.json", results)


if __name__ == "__main__":
    main()

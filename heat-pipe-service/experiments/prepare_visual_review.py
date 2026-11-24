"""Prepare blinded rank-change snapshots and consecutive-frame tracking cases."""

from __future__ import annotations

import argparse
import gzip
import json
import random
from itertools import pairwise
from pathlib import Path

import cv2
import numpy as np

from compare_tracking import PROJECT, compare
from heatpipe_service.analysis.io import att_frame_count, read_att_frame_celsius, read_att_header
from heatpipe_service.analysis.rendering import colorize, imwrite_unicode


METHODS = ("e1_peak_delta", "e1_track_delta", "e1_track_delta_area_duration")
BOX = ("bbox_x0", "bbox_y0", "bbox_x1", "bbox_y1")
WINDOWS = (("20260813_132634", 1600), ("20260816_120017", 9950))
CONTEXT_SAMPLES = ("D013", "D034", "D053", "D063")
COLORS = ((255, 255, 255), (255, 255, 0), (255, 35, 255), (0, 230, 0), (255, 160, 20),
          (0, 145, 255))


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def write_png(path, image):
    if not imwrite_unicode(path, image):
        raise OSError(f"Could not write {path}")


def coordinates(point):
    return tuple(point[key] for key in BOX)


def label_image(image, title, boxes):
    panel = cv2.copyMakeBorder(image, 22, 0, 0, 0, cv2.BORDER_CONSTANT)
    cv2.putText(panel, title, (7, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.48, COLORS[0], 1, cv2.LINE_AA)
    for i, (tag, rect, color) in enumerate(boxes):
        x0, y0, x1, y1 = rect
        cv2.rectangle(panel, (x0, y0 + 22), (x1, y1 + 22), color, 2)
        label_y = max(36, y0 + 19 - 12 * i)
        cv2.line(panel, (x0, label_y), (x0, y0 + 22), color, 1)
        cv2.putText(panel, tag, (x0 + 2, label_y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
    return panel


def rank_review(source, output, inputs):
    comparison = json.loads((source / "comparison.json").read_text(encoding="utf-8"))
    differences = {}
    samples = set()
    rng = random.Random(1711)
    for stem, limits in sorted(comparison.items()):
        e1 = limits["50"]["e1"]["interval"]["track_ids"]
        for method in METHODS:
            variant = limits["50"][method]["interval"]["track_ids"]
            for change, ranked, other in (("entered", variant, e1), ("exited", e1, variant)):
                other_ids = set(other)
                changed = [(rank, track_id) for rank, track_id in enumerate(ranked, 1)
                           if track_id not in other_ids]
                for rank, track_id in changed:
                    differences.setdefault((stem, track_id), []).append({
                        "method": method, "change": change, "rank": rank,
                    })
                if changed:
                    samples.add((stem, changed[0][1]))
                if method == "e1_track_delta" and len(changed) > 1:
                    samples.add((stem, rng.choice(changed[1:])[1]))

    visual = output / "rank"
    visual.mkdir()
    keys = sorted(samples)
    rng.shuffle(keys)
    sample_ids = {key: f"D{i:03d}" for i, key in enumerate(keys, 1)}
    results, panels = [], {}
    headers = {stem: read_att_header(inputs[stem]) for stem in comparison}
    with gzip.open(source / "track_features.jsonl.gz", "rt", encoding="utf-8") as stream:
        for row in map(json.loads, stream):
            stem, track_id = row["stem"], row["summary"]["track_id"]
            key = stem, track_id
            if key not in differences:
                continue
            frame = row["representative_frame"]
            box = coordinates(row["representative"])
            result = {
                "sample_id": sample_ids.get(key), "stem": stem, "track_id": track_id,
                "frame": frame, "box": box, "changes": differences[key],
                "peak_c": row["representative"]["peak_temp_c"],
                "label": "pending",
            }
            results.append(result)
            if key in samples:
                tag = sample_ids[key]
                raw = read_att_frame_celsius(inputs[stem], frame, headers[stem])
                low, high = np.percentile(raw, (2, 99.9))
                panels[tag] = label_image(colorize(raw, float(low), float(high)), tag,
                                          [("", box, COLORS[2])])
    if len(results) != len(differences) or len(panels) != len(samples):
        raise ValueError("Review tracks are missing from the feature export")
    for start in range(0, len(panels), 6):
        tiles = [panels[sample_ids[key]] for key in keys[start:start + 6]]
        tiles.extend(np.zeros_like(tiles[0]) for _ in range(6 - len(tiles)))
        sheet = np.vstack([np.hstack(tiles[i:i + 3]) for i in (0, 3)])
        write_png(visual / f"sheet-{start // 6 + 1:02d}.png", sheet)
    write_json(output / "rank_changes.json", results)
    print(f"{len(results)} unique rank changes, {len(panels)} selected for visual review", flush=True)
    return {row["sample_id"]: row for row in results if row["sample_id"] in CONTEXT_SAMPLES}


def tracking_cases(traces, start, length, anchor):
    # Distinguish links by candidate identity, including candidates with the same box.
    edges = {}
    for method, tracks in traces.items():
        for track in tracks:
            for frame, point in track["points"]:
                key = frame, id(point)
                edges.setdefault(key, {"point": point, "next": {}})["next"][method] = None
            for (frame, point), (end, dest) in pairwise(track["points"]):
                edges[frame, id(point)]["next"][method] = (end, id(dest), dest)

    categories = {"missed_frame": [], "competing_links": [], "geometry_change": []}
    for (frame, _), row in edges.items():
        if not start + 1 <= frame < start + length - 4:
            continue
        if anchor is not None:
            x0, y0, x1, y1 = coordinates(row["point"])
            a0, b0, a1, b1 = anchor["box"]
            distance = np.hypot((x0 + x1 - a0 - a1) / 2, (y0 + y1 - b0 - b1) / 2)
            row["anchor_distance"] = abs(frame - anchor["frame"]) + distance
            if abs(frame - anchor["frame"]) > 18 or distance > 35:
                continue
        successors = row["next"]
        distinct = {(item[0], item[1]) for item in successors.values() if item is not None}
        if successors["greedy"] is None and any(end == frame + 2 for end, _ in distinct):
            categories["missed_frame"].append((frame, row))
        if len(distinct) >= 2:
            categories["competing_links"].append((frame, row))
        left, right = (successors[name] for name in ("assignment_motion", "assignment_motion_geometry"))
        if left and right and left[:2] != right[:2]:
            categories["geometry_change"].append((frame, row))
    used_frames = []
    for name, candidates in categories.items():
        ordered = sorted(candidates, key=lambda item: (
            item[1].get("anchor_distance", 0),
            -item[1]["point"]["peak_temp_c"], -item[1]["point"]["area_px"],
            item[0], coordinates(item[1]["point"]),
        ))
        selected = 0
        for frame, row in ordered:
            if any(abs(frame - other) < 7 for other in used_frames):
                continue
            yield name, frame, row
            used_frames.append(frame)
            selected += 1
            if selected == (1 if anchor else 2):
                break


def tracking_review(output, inputs, anchors):
    visual = output / "tracking"
    visual.mkdir()
    cases = []
    windows = [(stem, start, 180, None) for stem, start in WINDOWS]
    windows.extend((row["stem"], row["frame"] - 25, 51, row) for row in
                   (anchors[tag] for tag in CONTEXT_SAMPLES))
    for stem, start, length, anchor in windows:
        traces = {}
        compare(inputs[stem], start, length, track_trace=traces)
        header = read_att_header(inputs[stem])
        for category, frame, row in tracking_cases(traces, start, length, anchor):
            case_id = f"T{len(cases) + 1:02d}"
            indices = range(frame - 1, frame + 5)
            assert indices.stop <= att_frame_count(inputs[stem], header)
            arrays = [read_att_frame_celsius(inputs[stem], i, header) for i in indices]
            low, high = np.percentile(np.stack(arrays), (2, 99.9))
            targets = {}
            successors = {}
            boxes = {frame: [("S", coordinates(row["point"]), COLORS[0])]}
            for method, dest in row["next"].items():
                if dest is None:
                    successors[method] = None
                    continue
                end, identity, point = dest
                key = end, identity
                if key not in targets:
                    tag = chr(65 + len(targets))
                    box = coordinates(point)
                    boxes.setdefault(end, []).append((tag, box, COLORS[len(targets) + 1]))
                    targets[key] = {"tag": tag, "frame": end, "box": box}
                successors[method] = targets[key]["tag"]
            panels = [label_image(colorize(raw, float(low), float(high)), f"{case_id}  frame {i}",
                                  boxes.get(i, []))
                      for i, raw in zip(indices, arrays, strict=True)]
            sheet = np.vstack([np.hstack(panels[:3]), np.hstack(panels[3:])])
            write_png(visual / f"{case_id}.png", sheet)
            cases.append({
                "id": case_id, "stem": stem, "category": category, "source_frame": frame,
                "context_sample": anchor["sample_id"] if anchor else None,
                "source_box": coordinates(row["point"]), "destinations": list(targets.values()),
                "successors": successors,
            })
    write_json(output / "tracking_cases.json", cases)
    print(f"{len(cases)} tracking cases", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True)
    inputs = {path.stem: path for path in (PROJECT / "data/experiments/raw").glob("*/*.att")}
    anchors = rank_review(args.source, args.output, inputs)
    tracking_review(args.output, inputs, anchors)


if __name__ == "__main__":
    main()

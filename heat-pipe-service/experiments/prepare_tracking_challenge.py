"""Render local link changes in visually selected competing-source scenes."""

from __future__ import annotations

import argparse
import gzip
import json
from itertools import pairwise
from pathlib import Path

import numpy as np

from compare_tracking import compare
from prepare_visual_review import (
    COLORS, PROJECT, att_frame_count, colorize, coordinates, label_image,
    read_att_frame_celsius, read_att_header, write_json, write_png,
)


SCENES = ("V045", "V151", "V062", "V104", "V020", "V121")
METHODS = ("greedy", "greedy_gap")


def changed_links(traces, center, anchor):
    nodes = {}
    for name in METHODS:
        for track in traces[name]:
            for (frame, point), (end, dest) in pairwise(track["points"]):
                if abs(frame - center) <= 18:
                    node = nodes.setdefault((frame, id(point)), (point, {}))
                    node[1][name] = end, id(dest), dest
    ax, ay, bx, by = anchor
    rows = []
    for (frame, _), (source, links) in nodes.items():
        options = [links.get(name) for name in METHODS]
        if all(options) and options[0][:2] == options[1][:2]:
            continue
        x0, y0, x1, y1 = coordinates(source)
        distance = float(np.hypot((x0 + x1 - ax - bx) / 2, (y0 + y1 - ay - by) / 2))
        if distance <= 38:
            rows.append((frame, source, options, distance))
    chosen = []
    for frame, source, options, _ in sorted(rows, key=lambda r: (
        sum(dest is None for dest in r[2]), abs(r[0] - center) + r[3], r[0], coordinates(r[1]),
    )):
        if all(abs(frame - f) >= 7 for f in chosen):
            chosen.append(frame)
            yield frame, source, options
            if len(chosen) == 2:
                break


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review", type=Path)
    parser.add_argument("tracks", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.review / "review_manifest.json").read_text())
    anchors = {r["review_id"]: r for r in manifest if r["review_id"] in SCENES}
    keys = {(r["stem"], r["track_id"]): r for r in anchors.values()}
    with gzip.open(args.tracks, "rt", encoding="utf-8") as stream:
        for row in map(json.loads, stream):
            key = row["stem"], row["summary"]["track_id"]
            if key in keys:
                keys[key]["box"] = coordinates(row["representative"])
    paths = {p.stem: p for p in (PROJECT / "data/experiments/raw").glob("*/*.att")}
    args.output.mkdir(parents=True)
    cases = []
    for scene in SCENES:
        anchor = anchors[scene]
        att = paths[anchor["stem"]]
        header = read_att_header(att)
        end = att_frame_count(att, header)
        center = anchor["frame"]
        start = max(0, center - 30)
        traces = {}
        compare(att, start, min(end - start, 61), track_trace=traces)
        for frame, source, options in changed_links(traces, center, anchor["box"]):
            if frame < 2 or frame + 3 >= end:
                continue
            case = f"C{len(cases) + 1:02d}"
            overlays = {frame: [("S", coordinates(source), COLORS[0])]}
            target = {}
            for method, letter, option, color in zip(METHODS, ("A", "B"), options, COLORS[1:3], strict=True):
                target[method] = None
                if option is not None:
                    index, _, hit = option
                    box = coordinates(hit)
                    overlays.setdefault(index, []).append((letter, box, color))
                    target[method] = {"frame": index, "box": box}
            indices = range(frame - 2, frame + 4)
            arrays = [read_att_frame_celsius(att, i, header) for i in indices]
            low, high = np.percentile(np.stack(arrays), (2, 99.9))
            panels = [label_image(colorize(data, float(low), float(high)),
                                  f"{case} frame {i}", overlays.get(i, []))
                      for i, data in zip(indices, arrays, strict=True)]
            write_png(args.output / f"{case}.png", np.vstack([np.hstack(panels[:3]), np.hstack(panels[3:])]))
            cases.append({"case": case, "scene": scene, "stem": anchor["stem"],
                          "frame": frame, "source": coordinates(source), "target": target})
    write_json(args.output / "cases.json", cases)
    print(f"{len(cases)} changed-link cases from {len(SCENES)} scene windows")


if __name__ == "__main__":
    main()

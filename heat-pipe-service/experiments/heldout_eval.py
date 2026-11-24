"""Held-out evaluation on the October demonstration recordings.

`prepare` reads the frozen configuration, ranks every method on the E1 tracks of
the held-out files, and writes one blinded review sheet set from the union of
the selected candidates. `score` joins the filled labels back to each method.
Nothing in the frozen configuration may be changed after `prepare` has run.
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
from collections import Counter
from pathlib import Path

import numpy as np

from aggregate_14_pairs import OUTPUT, PROJECT
from compare_reranking import track_features
from dev_labels import load_labels
from dev_validate import labeled_rows, load_tracks, make_model, percentile, vector
from ego_motion import frame_transforms, load_run, motion_features, open_frames, select_distinct
from prepare_visual_review import COLORS, coordinates, label_image, write_json, write_png
from vehicle_rerank import fit_model, score_tracks

import sys

sys.path.insert(0, str(PROJECT / "src"))
from heatpipe_service.analysis.rendering import colorize  # noqa: E402
from heatpipe_service.analysis.io import TEMP_SCALE  # noqa: E402
from heatpipe_service.jobs.batch import _select_tracks_across_frames  # noqa: E402

CLASSES = ("vehicle", "road", "other", "uncertain", "artifact")


def load_heldout(config):
    data = {}
    for stem in config["heldout_files"]:
        run = Path(config["heldout_runs"]) / stem
        tracks, roi, frames = load_run(run)
        att = Path(config["heldout_att_dir"]) / f"{stem}.att"
        memmap = open_frames(att, frames["total_att_frames"])
        transforms = frame_transforms(memmap, tracks, roi, config["min_inlier_ratio"])
        for rank, row in enumerate(tracks):
            row["features"] = track_features(row, row["points"], memmap, roi)
            row["motion"] = {"e1_rank": rank, **motion_features(row, transforms)}
        data[stem] = {"tracks": tracks, "roi": roi, "frames": frames, "att": att,
                      "motion_reasons": Counter(reason for _, reason in transforms.values())}
    return data


def rankings(config, tracks):
    base = percentile([row["persistence_score"] for row in tracks])
    old_model, old_training = fit_model(OUTPUT / "reranking-20261117-v2/track_features.jsonl.gz",
                                        OUTPUT / "visual-review-20261117-v2/rank_observations.json")
    old_score, _ = score_tracks(old_model, tracks, penalty=0.05)
    labels, _ = load_labels()
    dev = load_tracks(OUTPUT / "reranking-20261117-v2/track_features.jsonl.gz",
                      Path(config["dev_motion_dir"]))
    rows = labeled_rows(dev, labels)
    model = make_model().fit([vector(r, config["vehicle_features"]) for _, r, _ in rows],
                             [y for _, _, y in rows])
    vehicle = model.predict_proba([vector(r, config["vehicle_features"]) for r in tracks])[:, 1]
    new_score = base - config["vehicle_weight"] * vehicle
    order = lambda score: [tracks[i] for i in np.argsort(-score, kind="stable")]  # noqa: E731
    for row, probability in zip(tracks, vehicle, strict=True):
        row["vehicle_motion_score"] = float(probability)
    return {"e1": order(base), "vehicle_005": order(old_score), "proposed": order(new_score)}, {
        "old_training": old_training, "new_training_rows": len(rows),
        "new_training_vehicle": sum(y for _, _, y in rows)}


def select(config, ranked, frames):
    start, end = frames["start"], frames["end_exclusive"]
    limit = config["limit"]
    gap, pad = config["distinct_gap_frames"], config["distinct_pad_px"]
    return {
        "e1": _select_tracks_across_frames(ranked["e1"], start, end, limit),
        "e1_distinct": select_distinct(ranked["e1"], start, end, limit, gap, pad)[0],
        "vehicle_005": _select_tracks_across_frames(ranked["vehicle_005"], start, end, limit),
        "proposed_no_distinct": _select_tracks_across_frames(ranked["proposed"], start, end, limit),
        "proposed": select_distinct(ranked["proposed"], start, end, limit, gap, pad)[0],
    }


def display(raw):
    """Colour range from plausible temperatures; 20261029 has 654 C high-bit sky codes."""
    plausible = raw[raw < 200]
    low, high = np.percentile(plausible if plausible.size else raw, (2, 99.9))
    return colorize(raw, float(low), float(high))


def context_strip(memmap, row, offsets=(-6, -3, 0, 3, 6)):
    points = dict((frame, point) for frame, point in row["points"])
    frame = row["representative_frame"]
    panels = []
    for offset in offsets:
        index = min(max(frame + offset, 0), len(memmap) - 1)
        raw = memmap[index].astype(np.float32) / TEMP_SCALE
        boxes = [("", coordinates(points[index]), COLORS[2])] if index in points else []
        panels.append(label_image(display(raw), f"{offset:+d}", boxes))
    return np.hstack(panels)


def prepare(args):
    config = json.loads(args.config.read_text(encoding="utf-8"))
    output = args.output
    (output / "sheets").mkdir(parents=True)
    (output / "context").mkdir()
    data = load_heldout(config)
    manifest, sets, meta = [], {}, {}
    for stem, item in data.items():
        ranked, meta[stem] = rankings(config, item["tracks"])
        meta[stem]["motion_reasons"] = dict(item["motion_reasons"])
        chosen = select(config, ranked, item["frames"])
        sets[stem] = {name: [row["summary"]["track_id"] for row in rows] for name, rows in chosen.items()}
        by_id = {row["summary"]["track_id"]: row for row in item["tracks"]}
        for tid in sorted({tid for ids in sets[stem].values() for tid in ids}):
            manifest.append({"stem": stem, "track_id": tid, "frame": by_id[tid]["representative_frame"]})
        item["by_id"] = by_id
    random.Random(1118).shuffle(manifest)
    panels = []
    for i, entry in enumerate(manifest, 1):
        entry["review_id"] = f"H{i:03d}"
        item = data[entry["stem"]]
        row = item["by_id"][entry["track_id"]]
        memmap = open_frames(item["att"], item["frames"]["total_att_frames"])
        raw = memmap[entry["frame"]].astype(np.float32) / TEMP_SCALE
        panels.append(label_image(display(raw), entry["review_id"],
                                  [("", coordinates(row["representative"]), COLORS[2])]))
        write_png(output / "context" / f"{entry['review_id']}.png", context_strip(memmap, row))
    for start in range(0, len(panels), 6):
        group = panels[start:start + 6]
        group.extend(np.zeros_like(group[0]) for _ in range(6 - len(group)))
        write_png(output / "sheets" / f"sheet-{start // 6 + 1:02d}.png",
                  np.vstack([np.hstack(group[:3]), np.hstack(group[3:])]))
    write_json(output / "review_manifest.json", manifest)
    write_json(output / "review_labels.json",
               [{"review_id": e["review_id"], "label": "", "note": ""} for e in manifest])
    write_json(output / "selection.json", {"config": config, "meta": meta, "sets": sets})
    print(f"{len(manifest)} distinct candidates in {len(panels) // 6 + bool(len(panels) % 6)} sheets")


def render(args):
    """Redraw sheets and context strips for an existing manifest (display only)."""
    output = args.output
    manifest = json.loads((output / "review_manifest.json").read_text(encoding="utf-8"))
    config = json.loads((output / "selection.json").read_text(encoding="utf-8"))["config"]
    runs = {stem: load_run(Path(config["heldout_runs"]) / stem) for stem in config["heldout_files"]}
    panels = []
    for entry in manifest:
        tracks, _, frames = runs[entry["stem"]]
        row = next(r for r in tracks if r["summary"]["track_id"] == entry["track_id"])
        memmap = open_frames(Path(config["heldout_att_dir"]) / f"{entry['stem']}.att",
                             frames["total_att_frames"])
        raw = memmap[entry["frame"]].astype(np.float32) / TEMP_SCALE
        panels.append(label_image(display(raw), entry["review_id"],
                                  [("", coordinates(row["representative"]), COLORS[2])]))
        write_png(output / "context" / f"{entry['review_id']}.png", context_strip(memmap, row))
    for start in range(0, len(panels), 6):
        group = panels[start:start + 6]
        group.extend(np.zeros_like(group[0]) for _ in range(6 - len(group)))
        write_png(output / "sheets" / f"sheet-{start // 6 + 1:02d}.png",
                  np.vstack([np.hstack(group[:3]), np.hstack(group[3:])]))


def score(args):
    folder = args.output
    manifest = json.loads((folder / "review_manifest.json").read_text(encoding="utf-8"))
    labels = {r["review_id"]: r["label"] for r in
              json.loads((folder / "review_labels.json").read_text(encoding="utf-8"))}
    missing = [rid for rid, label in labels.items() if label not in CLASSES]
    if missing:
        raise SystemExit(f"Unlabeled or unknown labels: {missing[:10]}")
    by_key = {(e["stem"], e["track_id"]): labels[e["review_id"]] for e in manifest}
    selection = json.loads((folder / "selection.json").read_text(encoding="utf-8"))
    result = {}
    for stem, methods in selection["sets"].items():
        e1_road = {tid for tid in methods["e1"] if by_key[stem, tid] == "road"}
        result[stem] = {}
        for name, ids in methods.items():
            counts = Counter(by_key[stem, tid] for tid in ids)
            road = {tid for tid in ids if by_key[stem, tid] == "road"}
            result[stem][name] = {**{k: counts.get(k, 0) for k in CLASSES},
                                  "e1_road_kept": len(road & e1_road), "e1_road_total": len(e1_road)}
    total = {}
    for stem_result in result.values():
        for name, stats in stem_result.items():
            for key, value in stats.items():
                total.setdefault(name, Counter())[key] += value
    result["total"] = {name: dict(c) for name, c in total.items()}
    write_json(folder / "scores.json", result)
    print(json.dumps(result, indent=1))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "render", "score"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    {"prepare": prepare, "render": render, "score": score}[args.command](args)


if __name__ == "__main__":
    main()

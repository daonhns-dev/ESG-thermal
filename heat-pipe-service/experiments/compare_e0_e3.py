"""Compare saved E0, E1 and E1 + representative ±6 results on matching inputs."""

from __future__ import annotations

import argparse
from collections import Counter
from functools import partial
from pathlib import Path

from aggregate_14_pairs import candidate_key, directory, OUTPUT
from e3_review import ANSWERS, CLASSES, OCTOBER, baseline as interval_selection, open_runs, read, track_id, write_json
from e3_rules import matches
from ego_motion import load_run, select_distinct
from prepare_visual_review import coordinates


def track_key(row):
    x0, y0, x1, y1 = coordinates(row["representative"])
    return row["representative_frame"], x0, y0, x1 - x0 + 1, y1 - y0 + 1


def human_judgments(root):
    cards = {r["id"]: (r["stem"], r["track_id"]) for r in read(root / "internal/manifest.json")}
    confirmed = read(root / "reviewer/candidate_labels_R001-R020_round2.json")
    sample = read(root / "reviewer/rank-sample/labels.json")
    pairs = read(root / "reviewer/pair_labels.json")
    if confirmed["reviewer_type"] != "human" or pairs["reviewer_type"] != "human":
        raise ValueError("Expected confirmed human labels and pairs")
    if any(r["same_source"] not in ANSWERS for r in pairs["labels"]):
        raise ValueError("Incomplete human pair judgment")
    labels = {}
    for form in (confirmed, sample):
        for row in form["labels"]:
            if row["label"] and row["label"] not in CLASSES:
                raise ValueError(f"Invalid label: {row['id']}")
            if row["label"] and form["reviewer_type"] == "human":
                labels[cards[row["id"]]] = row["label"]
    return labels, [
        (row["pair_id"], cards[row["a"]], cards[row["b"]], row["same_source"])
        for row in pairs["labels"]
    ], sum(bool(r["label"]) for r in sample["labels"] if sample["reviewer_type"] != "human")


def compare(selection, output, human_review=None, factorial=False):
    saved = read(selection)["files"]
    overnight = OUTPUT / "overnight-20261116-v2"
    runs = open_runs()
    if set(runs) != set(saved):
        raise ValueError("E1 track sets and saved E3 selection cover different inputs")
    labels, pairs, pending = human_judgments(human_review) if human_review else ({}, [], 0)
    files = {}
    methods = ("e0", "e0_e3", "e1", "e1_e3") if factorial else ("e0", "e1", "e1_e3")
    for stem, (tracks, roi, frame_range) in runs.items():
        e0_dir = OUTPUT / "demo-20261118" / stem if stem in OCTOBER else directory(stem, "E0", overnight)
        e0 = read(e0_dir / "analysis_result.json")
        processing = e0["processing"]
        recorded_roi = processing["analysis_roi"]
        x, y = recorded_roi["x"], recorded_roi["y"]
        if (e0["status"] != "SUCCEEDED" or processing["frame_range"] != frame_range
                or roi != (x, y, x + recorded_roi["width"], y + recorded_roi["height"])):
            raise ValueError(f"Mismatched frame range, ROI or status: {stem}")
        baseline = {candidate_key(row) for row in e0["candidates"]}
        known = {track_id(row): row for row in tracks}
        current = saved[stem]
        paths = [current["e1_track_ids"], current["methods"]["representative_6"]["track_ids"]]
        e1, e3 = ({track_key(known[tid]) for tid in path} for path in paths)
        if any(len(group) != 50 for group in (baseline, e1, e3)):
            raise ValueError(f"Expected 50 distinct representative frame/box keys per method: {stem}")
        files[stem] = {
            "frames": frame_range["processed_frame_count"],
            "e0_eligible_tracks": e0["detected_candidate_count"],
            "e1_eligible_tracks": len(tracks),
            "e0_e1_exact_shared": len(baseline & e1),
            "e0_e1_e3_exact_shared": len(baseline & e3),
            "e1_e1_e3_exact_shared": len(e1 & e3),
            "e0_only_vs_e1_e3": sorted(baseline - e3),
            "e1_e3_only_vs_e0": sorted(e3 - baseline),
        }
        selections = {"e0": baseline, "e1": e1, "e1_e3": e3}
        if factorial:
            original, _, _ = load_run(e0_dir)
            e0_replay = [track_key(row) for row in interval_selection(original, frame_range)]
            if len(original) != e0["detected_candidate_count"] or e0_replay != [
                candidate_key(row) for row in e0["candidates"]
            ]:
                raise ValueError(f"E0 replay does not reproduce saved candidate order: {stem}")
            suppressed, skipped = select_distinct(
                original, frame_range["start"], frame_range["end_exclusive"], 50,
                match=partial(matches, representative_radius=6),
            )
            e0_e3 = {track_key(row) for row in suppressed}
            if len(e0_e3) != 50:
                raise ValueError(f"Expected 50 distinct E0+E3 representatives: {stem}")
            selections["e0_e3"] = e0_e3
            files[stem]["factorial"] = {
                "e0_replay_order_equal": True,
                "e0_e3_rule_skips": skipped,
                "selected_representatives": {method: sorted(rows) for method, rows in selections.items()},
                "exact_replacements": {
                    "e1_without_e3": len(baseline - e1),
                    "e1_with_e3": len(e0_e3 - e3),
                    "e3_on_e0": len(baseline - e0_e3),
                    "e3_on_e1": len(e1 - e3),
                    "e0_vs_e1_e3": len(baseline - e3),
                },
            }
        if human_review:
            judged = {track_key(known[tid]): label for (source, tid), label in labels.items() if source == stem}
            pair_keys = [(pid, track_key(known[a[1]]), track_key(known[b[1]]), answer)
                         for pid, a, b, answer in pairs if a[0] == b[0] == stem]
            files[stem]["human"] = {
                method: {
                    "classified": dict(Counter(judged[key] for key in rows if key in judged)),
                    "unreviewed": len(rows - judged.keys()),
                    "same_source_pairs_both_selected": [
                        pid for pid, a, b, answer in pair_keys if answer == "yes" and a in rows and b in rows
                    ],
                }
                for method, rows in selections.items()
            }
            files[stem]["human_confirmed_road_representatives"] = {
                "shared": sorted(key for key in baseline & e3 if judged.get(key) == "road"),
                "e0_only": sorted(key for key in baseline - e3 if judged.get(key) == "road"),
                "e1_e3_only": sorted(key for key in e3 - baseline if judged.get(key) == "road"),
            }
    report = {
        "definition": "Exact representative frame and pixel bbox. Track or heat-source identity was not measured.",
        "file_count": len(files), "files": files,
    }
    if human_review:
        report["human_summary"] = {
            "confirmed_candidate_labels_available": len(labels),
            "unconfirmed_rank_sample_entries": pending,
            "known_pair_judgments": len(pairs),
            "methods": {
                method: {
                    "classified": dict(sum((Counter(f["human"][method]["classified"]) for f in files.values()), Counter())),
                    "unreviewed": sum(f["human"][method]["unreviewed"] for f in files.values()),
                    "same_source_pairs_both_selected": sorted(
                        pid for f in files.values() for pid in f["human"][method]["same_source_pairs_both_selected"]),
                }
                for method in methods
            },
        }
    if factorial:
        report["factorial_totals"] = dict(sum(
            (Counter(f["factorial"]["exact_replacements"]) for f in files.values()), Counter()
        ))
    write_json(output, report)
    print(f"{len(files)} matched E0/E1/E3 files")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selection", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--human-review", type=Path)
    parser.add_argument("--factorial", action="store_true", help="Also replay the fixed E3 rule on E0 tracks")
    args = parser.parse_args()
    compare(args.selection, args.output, args.human_review, args.factorial)


if __name__ == "__main__":
    main()

"""Compare date-separated E4 and E5 ranks with a fixed E3 selection on August data."""

from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter, defaultdict
from functools import partial
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from aggregate_14_pairs import OUTPUT
from compare_e0_e3 import human_judgments
from compare_selection import interval
from dev_labels import load_labels
from dev_validate import make_model
from e3_review import open_runs, read, track_id, write_json
from e3_rules import matches
from ego_motion import select_distinct
from vehicle_rerank import PENALTY, features, score_tracks


METHODS = ("e1_e3", "isolation_forest_e3", "lof_e3", "vehicle_no_delta_e3", "vehicle_delta_e3")


def load_features(path):
    cached = defaultdict(dict)
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for row in map(json.loads, stream):
            cached[row["stem"]][track_id(row)] = row
    return cached


def count_labels(ids, stem, labels):
    return dict(Counter(labels.get((stem, tid), "unreviewed") for tid in ids))


def compare(args):
    history, conflicts = load_labels()
    humans, pairs, _ = human_judgments(args.human_review)
    cached = load_features(args.features)
    runs = open_runs()
    reference = read(args.selection)["files"]
    if len(cached) != 11 or set(cached) != {stem for stem in runs if stem[:6] == "202608"}:
        raise ValueError("Expected cached features for all 11 August recordings")
    training = [(stem, row, int(history[stem, tid] == "vehicle"))
                for stem, rows in cached.items() for tid, row in rows.items()
                if history.get((stem, tid)) in {"vehicle", "road", "other"}]
    files = {}
    confirmed_august = {key: label for key, label in humans.items() if key[0] in cached}
    for stem, items in sorted(cached.items()):
        ordered, _, frames = runs[stem]
        tids = [track_id(row) for row in ordered]
        if set(tids) != set(items) or len(tids) != len(items):
            raise ValueError(f"Track export and cached features differ: {stem}")
        rows = [items[tid] for tid in tids]
        date = stem[:8]
        train = [(row, label) for source, row, label in training if source[:8] != date]
        training_dates = sorted({source[:8] for source, _, _ in training if source[:8] != date})
        targets = [label for _, label in train]
        if set(targets) != {0, 1}:
            raise ValueError(f"Vehicle model requires two training classes: {stem}")
        rankings = {"e1_e3": ordered}
        probabilities = {}
        for detector in ("isolation_forest", "lof"):
            scores = [row["model_scores"][detector] for row in rows]
            rankings[f"{detector}_e3"] = [ordered[i] for i in np.argsort(-np.asarray(scores), kind="stable")]
        for omit_delta, name in ((True, "vehicle_no_delta_e3"), (False, "vehicle_delta_e3")):
            model = make_model().fit([features(row, omit_delta) for row, _ in train], targets)
            values, vehicle = score_tracks(model, rows, penalty=PENALTY, omit_delta=omit_delta)
            rankings[name] = [ordered[i] for i in np.argsort(-values, kind="stable")]
            probabilities[name] = dict(zip(tids, map(float, vehicle), strict=True))
        chosen, blockers, details = {}, {}, {}
        for name, ranked in rankings.items():
            blocked = {}
            selected, _ = select_distinct(
                ranked, frames["start"], frames["end_exclusive"], 50,
                match=partial(matches, representative_radius=6),
                on_skip=lambda row, kept: blocked.update({track_id(row): track_id(kept)}),
            )
            chosen[name] = [track_id(row) for row in selected]
            blockers[name] = blocked
        if chosen["e1_e3"] != reference[stem]["methods"]["representative_6"]["track_ids"]:
            raise ValueError(f"Fixed E3 selection does not reproduce the recorded baseline: {stem}")
        control = set(chosen["e1_e3"])
        historical_road = {tid for tid in control if history.get((stem, tid)) == "road"}
        human_road = {tid for tid in control if humans.get((stem, tid)) == "road"}
        intervals = {track_id(row): interval(row["representative_frame"], frames["start"],
                                             frames["end_exclusive"]) for row in ordered}
        baseline_positions = {tid: i for i, tid in enumerate(tids, 1)}
        for name, ids in chosen.items():
            selected = set(ids)
            order = [track_id(row) for row in rankings[name]]
            position = {tid: rank for rank, tid in enumerate(order, 1)}
            if len(ids) != 50 or len(selected) != len(ids):
                raise ValueError(f"Unexpected selection size for {stem}/{name}")
            details[name] = {
                "track_ids": ids,
                "changed": len(selected - control),
                "historical_labels": count_labels(ids, stem, history),
                "human_labels": count_labels(ids, stem, confirmed_august),
                "historical_road_removed": sorted(historical_road - selected),
                "historical_road_removal_path": [
                    {"track_id": tid, "baseline_rank": baseline_positions[tid],
                     "rank_before_selection": position[tid],
                     "rank_in_segment": sum(intervals[other] == intervals[tid] for other in order[:position[tid]]),
                     "vehicle_model_probability": probabilities.get(name, {}).get(tid),
                     "blocked_by": blockers[name].get(tid),
                     "reason": "deduplication" if tid in blockers[name] else "top50_cutoff"}
                    for tid in sorted(historical_road - selected)
                ],
                "human_road_removed": sorted(human_road - selected),
                "human_same_source_pairs_both": [
                    pid for pid, a, b, answer in pairs
                    if a[0] == b[0] == stem and answer == "yes" and {a[1], b[1]} <= selected
                ],
            }
        files[stem] = {"training_excluded_date": date, "training_dates": training_dates,
                       "training_label_count": len(train), "methods": details}

    totals = {}
    for method in METHODS:
        rows = [record["methods"][method] for record in files.values()]
        totals[method] = {
            "changed": sum(r["changed"] for r in rows),
            "historical_labels": dict(sum((Counter(r["historical_labels"]) for r in rows), Counter())),
            "human_labels": dict(sum((Counter(r["human_labels"]) for r in rows), Counter())),
            "historical_road_removed": sum(len(r["historical_road_removed"]) for r in rows),
            "human_road_removed": sum(len(r["human_road_removed"]) for r in rows),
            "human_same_source_pairs_both": sum(len(r["human_same_source_pairs_both"]) for r in rows),
        }
    write_json(args.output, {
        "vehicle_penalty": PENALTY, "files": files, "totals": totals,
        "confirmed_august_labels_available": len(confirmed_august),
        "historical_conflicts_excluded": len(conflicts),
        "model_source": "E4 cached leave-date-out scores; E5 trained on historical labels from other dates",
    })
    print(f"{len(files)} files; {len(METHODS)} methods; unchanged representative ±6")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--features", type=Path, default=OUTPUT / "reranking-20261117-v2/track_features.jsonl.gz")
    parser.add_argument("--human-review", type=Path, default=OUTPUT / "e3-human-review-20261117")
    parser.add_argument("--selection", type=Path,
                        default=OUTPUT / "e3-development-20261117/selection-stationary-development.json")
    args = parser.parse_args()
    with threadpool_limits(limits=2):
        compare(args)


if __name__ == "__main__":
    main()

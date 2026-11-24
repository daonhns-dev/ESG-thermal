"""Development-set check of road-motion residual features and event deduplication.

Uses only the August recordings and their labels. The held-out October files
are not read here; settings chosen from this output are frozen before them.
"""

from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from scipy.stats import rankdata
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import RobustScaler

from aggregate_14_pairs import OUTPUT, PROJECT, directory
from dev_labels import is_night, load_labels
from ego_motion import load_run, same_object, select_distinct
from vehicle_rerank import features as base_features

import sys

sys.path.insert(0, str(PROJECT / "src"))
from heatpipe_service.jobs.batch import _select_tracks_across_frames  # noqa: E402

MOTION_KEYS = ("residual_median_px", "residual_p90_px", "residual_relative",
               "residual_per_size", "flow_median_px", "raw_median_px")
RANKED_FILES = ("20260814_122052", "20260817_225314")
LIMIT = 50


def percentile(values):
    return (rankdata(values, method="average") - 0.5) / len(values)


def load_tracks(feature_export, motion_dir):
    motion = {}
    for path in motion_dir.glob("*.jsonl.gz"):
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for row in map(json.loads, stream):
                motion[row["stem"], row["track_id"]] = row
    tracks = defaultdict(list)
    with gzip.open(feature_export, "rt", encoding="utf-8") as stream:
        for row in map(json.loads, stream):
            key = row["stem"], row["summary"]["track_id"]
            row["motion"] = motion[key]
            tracks[row["stem"]].append(row)
    for rows in tracks.values():
        rows.sort(key=lambda row: row["motion"]["e1_rank"])
    return tracks


def motion_vector(row):
    m = row["motion"]
    return [np.nan if m[key] is None else m[key] for key in MOTION_KEYS]


def vector(row, kind):
    if kind == "base":
        return base_features(row)
    if kind == "motion":
        return motion_vector(row)
    return [*base_features(row), *motion_vector(row)]


def make_model():
    return make_pipeline(SimpleImputer(strategy="median"), RobustScaler(),
                         LogisticRegression(class_weight="balanced", random_state=0, max_iter=1000))


def auc_ci(y, s, rounds=2000):
    y, s = np.asarray(y), np.asarray(s)
    point = roc_auc_score(y, s)
    rng = np.random.default_rng(0)
    draws = []
    for _ in range(rounds):
        i = rng.integers(0, len(y), len(y))
        if len(set(y[i])) == 2:
            draws.append(roc_auc_score(y[i], s[i]))
    lo, hi = np.percentile(draws, (2.5, 97.5))
    return {"auc": round(float(point), 3), "ci95": [round(float(lo), 3), round(float(hi), 3)]}


def labeled_rows(tracks, labels):
    rows = []
    for stem, items in tracks.items():
        for row in items:
            label = labels.get((stem, row["summary"]["track_id"]))
            if label in ("vehicle", "road", "other"):
                rows.append((stem, row, int(label == "vehicle")))
    return rows


def single_feature_aucs(rows):
    result = {}
    y = [target for _, _, target in rows]
    for key in MOTION_KEYS + ("motion_valid_links",):
        values = [row["motion"][key] for _, row, _ in rows]
        keep = [i for i, v in enumerate(values) if v is not None]
        result[key] = {**auc_ci([y[i] for i in keep], [values[i] for i in keep]),
                       "n": len(keep), "vehicle": sum(y[i] for i in keep)}
    for name, index in (("median_delta_c", 1), ("peak_delta_c", 0)):
        values = [base_features(row)[index] for _, row, _ in rows]
        keep = [i for i, v in enumerate(values) if v is not None]
        result[name] = {**auc_ci([y[i] for i in keep], [values[i] for i in keep]), "n": len(keep)}
    return result


def leave_one_date_out(rows):
    dates = sorted({stem[:8] for stem, _, _ in rows})
    result = {}
    for kind in ("base", "motion", "base_motion"):
        y_all, s_all = [], []
        for date in dates:
            train = [(row, y) for stem, row, y in rows if stem[:8] != date]
            test = [(row, y) for stem, row, y in rows if stem[:8] == date]
            if not test or len({y for _, y in train}) < 2:
                continue
            model = make_model().fit([vector(r, kind) for r, _ in train], [y for _, y in train])
            s_all += list(model.predict_proba([vector(r, kind) for r, _ in test])[:, 1])
            y_all += [y for _, y in test]
        result[kind] = {**auc_ci(y_all, s_all), "n": len(y_all), "vehicle": sum(y_all)}
    return result


def peak_delta_percentile(items):
    """File-wise percentile of the representative peak minus local background; missing -> 0.5."""
    values = np.array([np.nan if row["features"]["peak_delta_c"] is None
                       else row["features"]["peak_delta_c"] for row in items])
    rank = np.full(len(values), 0.5)
    valid = np.isfinite(values)
    if valid.any():
        rank[valid] = percentile(values[valid])
    return rank


def rank_variants(items, vehicle, residual, weights):
    base = percentile([row["persistence_score"] for row in items])
    delta = peak_delta_percentile(items)
    variants = {"e1": base}
    for w in weights:
        variants[f"delta_rule_w{w:.2f}"] = base - w * delta
    for name, probability in vehicle.items():
        for w in weights:
            variants[f"{name}_w{w:.2f}"] = base - w * probability
    for w in weights:
        variants[f"residual_rule_w{w:.2f}"] = base - w * residual
    return {name: [(i, items[i]) for i in np.argsort(-score, kind="stable")]
            for name, score in variants.items()}


def redundancy(chosen, max_gap=3, pad=8):
    """Selected tracks that touch a higher ranked selected track (same object, by box rule)."""
    return sum(any(same_object(row, earlier, max_gap, pad) for earlier in chosen[:i])
               for i, row in enumerate(chosen))


DEDUP = {"": None, "+distinct3": (3, 8), "+distinct15": (15, 8)}


def ranking_check(tracks, labels, rows, weights):
    output = {}
    for stem in RANKED_FILES:
        items = tracks[stem]
        run_tracks, _, frames = load_run(directory(stem, "E1", OUTPUT / "overnight-20261116-v2"))
        points = {row["summary"]["track_id"]: row["points"] for row in run_tracks}
        for row in items:
            row["points"] = points[row["summary"]["track_id"]]
        train = [(row, y) for s, row, y in rows if s[:8] != stem[:8]]
        vehicle = {}
        for kind in ("base", "base_motion"):
            model = make_model().fit([vector(r, kind) for r, _ in train], [y for _, y in train])
            vehicle[f"vehicle_{kind}"] = model.predict_proba([vector(r, kind) for r in items])[:, 1]
        relative = np.array([np.nan if r["motion"]["residual_relative"] is None
                             else r["motion"]["residual_relative"] for r in items])
        relative[np.isnan(relative)] = np.nanmedian(relative)
        residual = percentile(relative)
        ranks = rank_variants(items, vehicle, residual, weights)
        e1_road = None
        output[stem] = {}
        for name, ranked in ranks.items():
            ordered = [row for _, row in ranked]
            for suffix, rule in DEDUP.items():
                if rule is None:
                    chosen, skipped = _select_tracks_across_frames(
                        ordered, frames["start"], frames["end_exclusive"], LIMIT), 0
                else:
                    chosen, skipped = select_distinct(ordered, frames["start"],
                                                      frames["end_exclusive"], LIMIT, *rule)
                ids = [row["summary"]["track_id"] for row in chosen]
                classes = Counter(labels.get((stem, tid), "unlabeled") for tid in ids)
                road = {tid for tid in ids if labels.get((stem, tid)) == "road"}
                if name == "e1" and rule is None:
                    e1_road = road
                    e1_road_rows = [row for row in chosen if row["summary"]["track_id"] in road]
                # An E1 road track still counts as shown if a selected track is the same object.
                shown = sum(any(row is c or same_object(row, c) for c in chosen) for row in e1_road_rows)
                output[stem][name + suffix] = {
                    **{k: classes.get(k, 0) for k in
                       ("vehicle", "road", "other", "uncertain", "unlabeled")},
                    "e1_road_kept": len(road & e1_road),
                    "e1_road_shown": shown,
                    "e1_road_total": len(e1_road),
                    "redundant": redundancy(chosen),
                    "skipped": skipped,
                    "track_ids": ids,
                }
    return output


def day_night(tracks, labels):
    table = defaultdict(Counter)
    for (stem, _), label in labels.items():
        table["night" if is_night(stem) else "day"][label] += 1
    files = {stem: {"night": is_night(stem), "tracks": len(items)} for stem, items in tracks.items()}
    return {"labels": {k: dict(v) for k, v in table.items()}, "files": files}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("motion_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--features", type=Path,
                        default=OUTPUT / "reranking-20261117-v2/track_features.jsonl.gz")
    args = parser.parse_args()
    labels, conflicts = load_labels()
    tracks = load_tracks(args.features, args.motion_dir)
    rows = labeled_rows(tracks, labels)
    by_motion = defaultdict(Counter)
    for _, row, y in rows:
        value = row["motion"]["residual_relative"]
        by_motion["vehicle" if y else "non_vehicle"]["missing" if value is None else "present"] += 1
    result = {
        "labels": {"tracks": len(labels), "conflicts": conflicts,
                   "classes": dict(Counter(labels.values())),
                   "binary_rows": len(rows), "vehicle_rows": sum(y for _, _, y in rows),
                   "motion_coverage": {k: dict(v) for k, v in by_motion.items()}},
        "single_feature_auc": single_feature_aucs(rows),
        "leave_one_date_out": leave_one_date_out(rows),
        "day_night": day_night(tracks, labels),
        "ranking": ranking_check(tracks, labels, rows, (0.05, 0.10, 0.20, 0.30)),
        "notes": "vehicle_* models are leave-one-date-out logistic regressions",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "ranking"}, indent=1,
                     ensure_ascii=False)[:6000])
    for stem, variants in result["ranking"].items():
        print(stem)
        for name, stats in variants.items():
            print(f"  {name:32s} " + " ".join(f"{k}={stats[k]}" for k in (
                "vehicle", "road", "other", "uncertain", "unlabeled", "e1_road_kept", "e1_road_shown", "redundant")))


if __name__ == "__main__":
    main()

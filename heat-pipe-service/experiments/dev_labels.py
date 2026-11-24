"""Consolidate the August visual-review labels to one class per (stem, track_id)."""

from __future__ import annotations

import json
from collections import defaultdict

from aggregate_14_pairs import OUTPUT

CLASS = {
    "vehicle_related": "vehicle",
    "road_review": "road",
    "road_surface_origin_unknown": "road",
    "other_source": "other",
    "uncertain": "uncertain",
}
REVIEWS = ("vehicle-review-20261117", "vehicle-review-weak-20261117",
           "vehicle-review-no-delta-20261117")


def load_labels():
    """Return ({(stem, track_id): class}, [conflicts]); unreviewed rows are skipped."""
    seen = defaultdict(set)
    for name in REVIEWS:
        folder = OUTPUT / name
        labels = {row["review_id"]: row["label"] for row in
                  json.loads((folder / "review_labels.json").read_text(encoding="utf-8"))}
        for row in json.loads((folder / "review_manifest.json").read_text(encoding="utf-8")):
            label = labels.get(row["review_id"])
            if label in CLASS:
                seen[row["stem"], row["track_id"]].add(CLASS[label])
    observations = OUTPUT / "visual-review-20261117-v2" / "rank_observations.json"
    for row in json.loads(observations.read_text(encoding="utf-8")):
        if row.get("label") in CLASS:
            seen[row["stem"], row["track_id"]].add(CLASS[row["label"]])
    conflicts = [(key, sorted(value)) for key, value in seen.items() if len(value) > 1]
    return {key: next(iter(value)) for key, value in seen.items() if len(value) == 1}, conflicts


def is_night(stem):
    hour = int(stem[9:11])
    return hour >= 19 or hour < 6

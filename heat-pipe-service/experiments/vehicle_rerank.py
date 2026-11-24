"""Shared training and ranking for the experimental vehicle penalty."""

from __future__ import annotations

import gzip
import json

import numpy as np
from scipy.stats import rankdata
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import RobustScaler


PENALTY = 0.05
EVALUATION_FILES = ("20260814_122052", "20260817_225314")
LABELS = {"vehicle_related": 1, "other_source": 0, "road_surface_origin_unknown": 0}


def features(row, omit_delta=False):
    f, r, summary = row["features"], row["representative"], row["summary"]
    x0, y0, x1, y1 = (r[key] for key in ("bbox_x0", "bbox_y0", "bbox_x1", "bbox_y1"))
    values = [
        f["median_area_px"], f["area_growth_ratio"], f["mean_circularity"],
        f["n_frames"], f["density"],
        y1 - y0 + 1, x1 - x0 + 1, (x0 + x1) / 2, (y0 + y1) / 2,
        r["peak_temp_c"] - r["mean_temp_c"], r["edge_sharpness"],
        (summary["y_center_end"] - summary["y_center_start"]) / max(1, f["n_frames"] - 1),
    ]
    return values if omit_delta else [f["peak_delta_c"], f["median_delta_c"], *values]


def fit_model(feature_export, observations_file, omit_delta=False):
    excluded = {stem[:8] for stem in EVALUATION_FILES}
    observations = json.loads(observations_file.read_text(encoding="utf-8"))
    labels = {(row["stem"], row["track_id"]): LABELS[row["label"]] for row in observations
              if row["stem"][:8] not in excluded and row["label"] in LABELS}
    training_x, training_y = [], []
    found = set()
    with gzip.open(feature_export, "rt", encoding="utf-8") as stream:
        for row in map(json.loads, stream):
            key = row["stem"], row["summary"]["track_id"]
            if key in labels:
                if key in found:
                    raise ValueError(f"Duplicate training track: {key}")
                training_x.append(features(row, omit_delta))
                training_y.append(labels[key])
                found.add(key)
    if found != labels.keys():
        raise ValueError("Training labels are missing from the track export")
    if set(training_y) != {0, 1}:
        raise ValueError("Training observations must contain both vehicle and other tracks")
    model = make_pipeline(SimpleImputer(strategy="median"), RobustScaler(),
                          LogisticRegression(class_weight="balanced", random_state=0))
    model.fit(training_x, training_y)
    return model, {
        "training_samples": len(training_y),
        "training_vehicle": sum(training_y),
        "training_dates": sorted({stem[:8] for stem, _ in found}),
        "held_dates": sorted(excluded),
        "uses_delta": not omit_delta,
    }


def score_tracks(model, tracks, penalty=PENALTY, omit_delta=False):
    if not tracks:
        return np.empty(0), np.empty(0)
    score = [row["persistence_score"] for row in tracks]
    percentile = (rankdata(score, method="average") - 0.5) / len(score)
    vehicle = model.predict_proba([features(row, omit_delta) for row in tracks])[:, 1]
    return percentile - penalty * vehicle, vehicle

"""Explainable frame-candidate ranking inherited from the PoC."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

DEFAULT_WEIGHTS = {
    "max_z": 0.45,
    "mean_z": 0.25,
    "area_log": 0.15,
    "edge_sharpness": 0.10,
    "circularity": 0.05,
}


def score_candidate(
    candidate: Mapping[str, Any], weights: Mapping[str, float] | None = None
) -> float:
    weights = weights or DEFAULT_WEIGHTS
    features = {
        "max_z": _to_float(candidate.get("max_z")),
        "mean_z": _to_float(candidate.get("mean_z")),
        "area_log": math.log1p(max(0.0, _to_float(candidate.get("area_px")))),
        "edge_sharpness": _to_float(candidate.get("edge_sharpness")),
        "circularity": _to_float(candidate.get("circularity")),
    }
    return sum(features.get(key, 0.0) * float(weight) for key, weight in weights.items())


def rank_candidates(
    candidates: list[dict[str, Any]], weights: Mapping[str, float] | None = None
) -> list[dict[str, Any]]:
    ranked = []
    for item in candidates:
        row = dict(item)
        row["review_score"] = score_candidate(row, weights)
        ranked.append(row)
    return sorted(ranked, key=lambda row: row["review_score"], reverse=True)


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


"""Summarize manually labeled re_testbed candidate cases.

The input is the `label_candidates.csv` produced by `label_candidate_export.py`
after `reviewer_label` has been filled by a human reviewer.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DEFAULT_LABELS = [
    "vehicle",
    "road_paint",
    "structure_edge",
    "road_surface_hotspot",
    "boundary_artifact",
    "unknown",
]
DEFAULT_TARGET_LABELS = ["road_surface_hotspot"]
DEFAULT_FALSE_POSITIVE_LABELS = ["vehicle", "road_paint", "structure_edge", "boundary_artifact"]
ALGORITHM_COLUMNS = {
    "robust_zscore": "robust_quality",
    "top_hat_zscore": "top_hat_quality",
    "multiscale_top_hat": "multiscale_quality",
    "percentile_contrast": "percentile_quality",
}


def read_csv(path: Path) -> list[dict[str, Any]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8", errors="replace") as f:
        if not rows:
            f.write("")
            return
        fieldnames: list[str] = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def as_float(value: Any, default: float = 0.0) -> float:
    if value in ("", None):
        return default
    try:
        return float(value)
    except ValueError:
        return default


def split_algorithms(row: dict[str, Any]) -> list[str]:
    return [value for value in str(row.get("algorithms_present", "")).split("|") if value]


def has_algorithm(row: dict[str, Any], algorithm: str) -> bool:
    return algorithm in split_algorithms(row)


def score_for(row: dict[str, Any], algorithm: str) -> float | None:
    column = ALGORITHM_COLUMNS[algorithm]
    value = row.get(column, "")
    if value == "":
        return None
    return as_float(value)


def label_status(label: str, target_labels: set[str], false_positive_labels: set[str]) -> str:
    if label in target_labels:
        return "target"
    if label in false_positive_labels:
        return "false_positive"
    if label == "unknown":
        return "unknown"
    return "other"


def summarize(rows: list[dict[str, Any]], target_labels: set[str], false_positive_labels: set[str]) -> dict[str, Any]:
    labeled = [row for row in rows if row.get("reviewer_label", "").strip()]
    unlabeled = [row for row in rows if not row.get("reviewer_label", "").strip()]
    invalid = [
        row
        for row in labeled
        if row.get("reviewer_label", "").strip() not in set(DEFAULT_LABELS)
    ]

    label_counts = Counter(row["reviewer_label"].strip() for row in labeled)
    reason_counts: dict[str, Counter[str]] = defaultdict(Counter)
    best_algorithm_counts: dict[str, Counter[str]] = defaultdict(Counter)
    algorithm_presence_counts: dict[str, Counter[str]] = defaultdict(Counter)
    algorithm_score_sums: dict[str, Counter[str]] = defaultdict(Counter)
    algorithm_score_counts: dict[str, Counter[str]] = defaultdict(Counter)

    for row in labeled:
        label = row["reviewer_label"].strip()
        status = label_status(label, target_labels, false_positive_labels)
        reason_counts[row.get("sampling_reason", "")][label] += 1
        best_algorithm_counts[row.get("best_algorithm", "")][label] += 1
        for algorithm in ALGORITHM_COLUMNS:
            if not has_algorithm(row, algorithm):
                continue
            algorithm_presence_counts[algorithm][label] += 1
            algorithm_presence_counts[algorithm][f"status:{status}"] += 1
            score = score_for(row, algorithm)
            if score is not None:
                algorithm_score_sums[algorithm][label] += score
                algorithm_score_counts[algorithm][label] += 1

    algorithm_rows = []
    for algorithm in ALGORITHM_COLUMNS:
        counts = algorithm_presence_counts[algorithm]
        total = sum(counts[label] for label in DEFAULT_LABELS)
        target_count = counts["status:target"]
        false_positive_count = counts["status:false_positive"]
        unknown_count = counts["status:unknown"]
        algorithm_rows.append(
            {
                "algorithm": algorithm,
                "labeled_cases_present": total,
                "target_cases_present": target_count,
                "false_positive_cases_present": false_positive_count,
                "unknown_cases_present": unknown_count,
                "target_rate_in_labeled_sample": round(target_count / total, 3) if total else 0.0,
                "false_positive_rate_in_labeled_sample": round(false_positive_count / total, 3) if total else 0.0,
                **{f"label_{label}": counts[label] for label in DEFAULT_LABELS},
            }
        )

    score_rows = []
    for algorithm in ALGORITHM_COLUMNS:
        for label in DEFAULT_LABELS:
            count = algorithm_score_counts[algorithm][label]
            if not count:
                continue
            score_rows.append(
                {
                    "algorithm": algorithm,
                    "label": label,
                    "count": count,
                    "mean_quality_score": round(algorithm_score_sums[algorithm][label] / count, 3),
                }
            )

    return {
        "total_rows": len(rows),
        "labeled_rows": len(labeled),
        "unlabeled_rows": len(unlabeled),
        "invalid_label_rows": len(invalid),
        "label_counts": dict(label_counts),
        "algorithm_summary": algorithm_rows,
        "quality_by_label": score_rows,
        "sampling_reason_counts": [
            {"sampling_reason": reason, **{f"label_{label}": counts[label] for label in DEFAULT_LABELS}}
            for reason, counts in sorted(reason_counts.items())
        ],
        "best_algorithm_counts": [
            {"best_algorithm": algorithm, **{f"label_{label}": counts[label] for label in DEFAULT_LABELS}}
            for algorithm, counts in sorted(best_algorithm_counts.items())
        ],
        "invalid_cases": [
            {
                "case_id": row.get("case_id", ""),
                "reviewer_label": row.get("reviewer_label", ""),
            }
            for row in invalid
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label-csv", required=True)
    parser.add_argument("--output-dir")
    parser.add_argument("--target-labels", nargs="+", default=DEFAULT_TARGET_LABELS)
    parser.add_argument("--false-positive-labels", nargs="+", default=DEFAULT_FALSE_POSITIVE_LABELS)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    label_csv = Path(args.label_csv)
    output_dir = Path(args.output_dir) if args.output_dir else label_csv.parent / "label_summary"
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = read_csv(label_csv)
    result = summarize(rows, set(args.target_labels), set(args.false_positive_labels))

    summary_json = output_dir / "label_summary.json"
    algorithm_csv = output_dir / "algorithm_label_summary.csv"
    quality_csv = output_dir / "quality_by_label.csv"
    reason_csv = output_dir / "sampling_reason_label_summary.csv"
    best_csv = output_dir / "best_algorithm_label_summary.csv"

    summary_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(algorithm_csv, result["algorithm_summary"])
    write_csv(quality_csv, result["quality_by_label"])
    write_csv(reason_csv, result["sampling_reason_counts"])
    write_csv(best_csv, result["best_algorithm_counts"])

    print("Label summary saved")
    print(f"  labeled: {result['labeled_rows']} / {result['total_rows']}")
    print(f"  unlabeled: {result['unlabeled_rows']}")
    print(f"  invalid labels: {result['invalid_label_rows']}")
    print(f"  summary: {summary_json}")
    print(f"  algorithm summary: {algorithm_csv}")
    print(f"  quality by label: {quality_csv}")
    print(f"  sampling reason summary: {reason_csv}")
    print(f"  best algorithm summary: {best_csv}")
    for row in result["algorithm_summary"]:
        print(
            f"  {row['algorithm']}: target={row['target_cases_present']}, "
            f"false_positive={row['false_positive_cases_present']}, "
            f"unknown={row['unknown_cases_present']}, "
            f"target_rate={row['target_rate_in_labeled_sample']:.3f}"
        )


if __name__ == "__main__":
    main()

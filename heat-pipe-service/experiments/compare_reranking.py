"""Compare experimental scores on the same exported E1 tracks."""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path
from statistics import median

import numpy as np
from scipy.stats import rankdata
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
from sklearn.preprocessing import RobustScaler
from threadpoolctl import threadpool_limits

from aggregate_14_pairs import PROJECT, directory
from compare_selection import describe

sys.path.insert(0, str(PROJECT / "src"))

from heatpipe_service.analysis.io import ATT_HEADER_SIZE, TEMP_SCALE, read_att_header
from heatpipe_service.analysis.rendering import colorize, draw_result_image, imwrite_unicode
from heatpipe_service.jobs.batch import _bbox_iou, _select_tracks_across_frames


BOX_KEYS = ("bbox_x0", "bbox_y0", "bbox_x1", "bbox_y1")
METHODS = ("e1", "e1_peak_delta", "e1_track_delta", "e1_track_delta_area_duration",
           "isolation_forest", "lof")


def local_delta(raw, point, roi):
    x0, y0, x1, y1 = (point[key] for key in BOX_KEYS)
    left, top, right, bottom = roi
    window = raw[max(top, y0 - 5):min(bottom, y1 + 6), max(left, x0 - 5):min(right, x1 + 6)]
    dy, dx = y0 - max(top, y0 - 5), x0 - max(left, x0 - 5)
    mask = np.ones(window.shape, dtype=bool)
    mask[dy:dy + y1 - y0 + 1, dx:dx + x1 - x0 + 1] = False
    background = window[mask]
    if not background.size:
        return None
    return point["peak_temp_c"] - float(np.median(background)) / TEMP_SCALE


def track_features(row, points, frames, roi):
    indices = np.linspace(0, len(points) - 1, min(5, len(points)), dtype=int)
    deltas = [local_delta(frames[points[i][0]], points[i][1], roi) for i in indices]
    deltas = [value for value in deltas if value is not None]
    summary = row["summary"]
    span = summary["end_frame"] - summary["start_frame"] + 1
    return {
        "peak_delta_c": local_delta(frames[row["representative_frame"]], row["representative"], roi),
        "median_delta_c": float(median(deltas)) if deltas else None,
        "delta_samples": len(deltas),
        "median_area_px": float(median(point["area_px"] for _, point in points)),
        "n_frames": summary["n_frames"],
        "density": summary["n_frames"] / span,
        "area_growth_ratio": summary["area_growth_ratio"],
        "mean_circularity": summary["mean_circularity"],
    }


def load_tracks(stem, overnight, att_path):
    path = directory(stem, "E1", overnight)
    result = json.loads((path / "analysis_result.json").read_text(encoding="utf-8"))
    processing = result["processing"]
    frame_range = processing["frame_range"]
    r = processing["analysis_roi"]
    roi = (r["x"], r["y"], r["x"] + r["width"], r["y"] + r["height"])
    header = read_att_header(att_path)
    frames = np.memmap(att_path, dtype="<u2", mode="r", offset=ATT_HEADER_SIZE,
                       shape=(frame_range["total_att_frames"], header.height, header.width))
    eligible_frame = np.zeros(len(frames), dtype=bool)
    tracks = []
    with gzip.open(path / "eligible_tracks.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            points = row.pop("points")
            eligible_frame[[frame for frame, _ in points]] = True
            row["features"] = track_features(row, points, frames, roi)
            tracks.append(row)
    return tracks, frame_range, roi, eligible_frame


def percentile(values):
    return (rankdata(values, method="average") - 0.5) / len(values)


def feature_matrix(tracks):
    columns = ("peak_delta_c", "median_delta_c", "median_area_px", "n_frames",
               "density", "area_growth_ratio", "mean_circularity")
    return np.array([[row["features"][column] for column in columns] for row in tracks],
                    dtype=float)


def model_scores(matrices):
    dates = sorted({stem[:8] for stem in matrices})
    result = {}
    for date in dates:
        by_date = [
            np.concatenate([x for stem, x in matrices.items() if stem.startswith(other)])
            for other in dates if other != date
        ]
        training = np.concatenate([
            x[np.random.default_rng(0).choice(len(x), min(len(x), 2000), replace=False)]
            for x in by_date
        ])
        fill = np.nanmedian(training, axis=0)
        fill = np.nan_to_num(fill)
        scaler = RobustScaler()
        training = scaler.fit_transform(np.where(np.isnan(training), fill, training))
        models = {
            "isolation_forest": IsolationForest(n_estimators=100, random_state=0),
            "lof": LocalOutlierFactor(n_neighbors=30, novelty=True),
        }
        for model in models.values():
            model.fit(training)
        for stem, matrix in matrices.items():
            if stem.startswith(date):
                features = scaler.transform(np.where(np.isnan(matrix), fill, matrix))
                result[stem] = {name: -model.score_samples(features)
                                for name, model in models.items()}
    return result


def ranked_methods(tracks, scores):
    base = percentile([row["persistence_score"] for row in tracks])
    feature = feature_matrix(tracks)
    # For a missing local background, give the temperature term a neutral percentile.
    temperature = []
    for column in feature[:, :2].T:
        valid = np.isfinite(column)
        rank = np.full(len(column), 0.5)
        if valid.any():
            rank[valid] = percentile(column[valid])
        temperature.append(rank)
    peak, sustained = temperature
    area, duration = (percentile(c) for c in feature[:, 2:4].T)
    values = {
        "e1": base,
        "e1_peak_delta": 0.75 * base + 0.25 * peak,
        "e1_track_delta": 0.75 * base + 0.25 * sustained,
        "e1_track_delta_area_duration": 0.55 * base + 0.25 * sustained + 0.10 * area + 0.10 * duration,
        **scores,
    }
    return {
        method: [tracks[i] for i in np.argsort(-values[method], kind="stable")]
        for method in METHODS
    }


def track_id(row):
    return row["summary"]["track_id"]


def suppress_nearby_boxes(ranked):
    """Suppress image-space duplicates only within two representative frames."""
    by_frame = {}
    kept = []
    for row in ranked:
        frame = row["representative_frame"]
        if any(_bbox_iou(row["representative"], previous) >= 0.5
               for nearby in range(frame - 2, frame + 3)
               for previous in by_frame.get(nearby, ())):
            continue
        by_frame.setdefault(frame, []).append(row["representative"])
        kept.append(row)
    return kept


def evaluate(ranks, frame_range):
    start, end = frame_range["start"], frame_range["end_exclusive"]
    deduplicated = {name: suppress_nearby_boxes(rows) for name, rows in ranks.items()}
    result = {}
    for limit in (25, 50, 100):
        selection = {}
        for name, ranked in ranks.items():
            selection[name] = {
                "global": ranked[:limit],
                "interval": _select_tracks_across_frames(ranked, start, end, limit),
                "interval_nearby_suppression": _select_tracks_across_frames(
                    deduplicated[name], start, end, limit
                ),
            }
        result[limit] = {}
        for name, variants in selection.items():
            result[limit][name] = {}
            for policy, rows in variants.items():
                ids = {track_id(row) for row in rows}
                baseline = {track_id(row) for row in selection["e1"][policy]}
                stats = describe(rows, start, end)
                stats["shared_with_e1"] = len(ids & baseline)
                stats["track_ids"] = [track_id(row) for row in rows]
                stats["nearby_box_redundancy"] = len(rows) - len(suppress_nearby_boxes(rows))
                stats["excluded_before_cap"] = len(ranks[name]) - len(deduplicated[name]) if (
                    policy == "interval_nearby_suppression"
                ) else 0
                result[limit][name][policy] = stats
    return result


def write_review(output, datasets, rankings, inputs):
    images = output / "review_images"
    images.mkdir()
    review_rows, mapping = [], []
    for stem, (tracks, _, roi, eligible) in datasets.items():
        ranks = rankings[stem]
        samples = {}
        for method, ranked in ranks.items():
            for i in (0, 24, 49):
                row = ranked[i]
                samples.setdefault(track_id(row), row)
        samples[track_id(tracks[len(tracks) // 2])] = tracks[len(tracks) // 2]
        for frame in np.flatnonzero(~eligible)[::max(1, int((~eligible).sum() / 2))][:2]:
            samples[f"frame-{frame}"] = {"representative_frame": int(frame)}
        header = read_att_header(inputs[stem])
        frames = np.memmap(inputs[stem], dtype="<u2", mode="r", offset=ATT_HEADER_SIZE,
                           shape=(len(eligible), header.height, header.width))
        order = np.random.default_rng(17).permutation(len(samples))
        sample_list = list(samples.values())
        for i in order:
            row = sample_list[i]
            sample_id = f"R{len(review_rows) + 1:04d}"
            frame = row["representative_frame"]
            frame_c = frames[frame].astype(np.float32) / TEMP_SCALE
            representative = row.get("representative")
            if representative:
                box = tuple(representative[key] for key in BOX_KEYS)
                rendered = draw_result_image(frame_c, box, sample_id, analysis_roi=roi)
            else:
                rendered = colorize(frame_c)
            image_path = images / f"{sample_id}.png"
            if not imwrite_unicode(image_path, rendered, ".png"):
                raise OSError(f"Could not write {image_path}")
            review_rows.append({"id": sample_id, "image": f"review_images/{sample_id}.png",
                                "label": "", "notes": ""})
            mapping.append({"id": sample_id, "stem": stem, "frame": frame,
                            "track_id": track_id(row) if representative else None})
    for name, data in (("review_labels", review_rows), ("review_index", mapping)):
        (output / f"{name}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    write_review_guide(output, review_rows)
    return len(review_rows)


def write_review_guide(output, review_rows):
    lines = [
        "# 열영상 후보 검토",
        "",
        "PNG를 열고 [라벨 파일](review_labels.json)에 같은 ID의 열원 유형과 근거를 기록합니다.",
        "분류는 관로 이상 의심, 차량/배기열, 기타 열원, 판단 불가입니다.",
        "프레임과 트랙 정보는 [검토 인덱스](review_index.json)에 있습니다.",
        "",
    ]
    lines.extend(f"- [{row['id']}]({row['image']})" for row in review_rows)
    (output / "REVIEW.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("overnight", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    rows = json.loads((args.overnight / "aggregate_14_pairs.json").read_text(encoding="utf-8"))
    inputs = {p.stem: p for p in (PROJECT / "data/experiments/raw").glob("*/*.att")}
    args.output.mkdir(parents=True)
    datasets = {}
    for row in rows:
        if row["skipped_frames"] == 0:
            stem = row["stem"]
            datasets[stem] = load_tracks(stem, args.overnight, inputs[stem])
            print(f"{stem}: {len(datasets[stem][0])} tracks", flush=True)
    with threadpool_limits(limits=2):
        scores = model_scores({stem: feature_matrix(data[0]) for stem, data in datasets.items()})
    rankings, results = {}, {}
    with gzip.open(args.output / "track_features.jsonl.gz", "wt", encoding="utf-8") as stream:
        for stem, (tracks, frame_range, _, _) in datasets.items():
            rankings[stem] = ranked_methods(tracks, scores[stem])
            results[stem] = evaluate(rankings[stem], frame_range)
            for i, row in enumerate(tracks):
                row["model_scores"] = {name: float(values[i]) for name, values in scores[stem].items()}
                stream.write(json.dumps({"stem": stem, **row}, allow_nan=False) + "\n")
    count = write_review(args.output, datasets, rankings, inputs)
    (args.output / "comparison.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"{len(results)} recordings, {sum(len(data[0]) for data in datasets.values())} tracks, "
          f"{count} review images")


if __name__ == "__main__":
    main()

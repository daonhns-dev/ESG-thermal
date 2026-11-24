"""Road-motion residual features and event grouping for exported E1 tracks.

A road-surface hot spot moves with the road image as the vehicle drives; another
vehicle moves on its own. For each consecutive track link we estimate the road
image motion from bottom-band features (camera_motion.estimate_motion), project
the previous box centre with it, and keep the distance to the observed centre.
"""

from __future__ import annotations

import gzip
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

from aggregate_14_pairs import PROJECT

import sys

sys.path.insert(0, str(PROJECT / "src"))

from camera_motion import estimate_motion  # noqa: E402
from heatpipe_service.analysis.io import ATT_HEADER_SIZE, TEMP_SCALE, read_att_header  # noqa: E402
from heatpipe_service.analysis.tracking import centroid  # noqa: E402


BOX_KEYS = ("bbox_x0", "bbox_y0", "bbox_x1", "bbox_y1")


def load_run(run_dir: Path):
    result = json.loads((run_dir / "analysis_result.json").read_text(encoding="utf-8"))
    processing = result["processing"]
    r = processing["analysis_roi"]
    roi = (r["x"], r["y"], r["x"] + r["width"], r["y"] + r["height"])
    with gzip.open(run_dir / "eligible_tracks.jsonl.gz", "rt", encoding="utf-8") as stream:
        tracks = [json.loads(line) for line in stream]
    return tracks, roi, processing["frame_range"]


def open_frames(att_path: Path, count: int, signed: bool = False):
    header = read_att_header(att_path)
    return np.memmap(att_path, dtype="<i2" if signed else "<u2", mode="r",
                     offset=ATT_HEADER_SIZE, shape=(count, header.height, header.width))


def frame_transforms(frames, tracks, roi, min_inlier_ratio=0.70):
    """Estimate road motion for every consecutive frame pair used by a track link.

    Returns {target_frame: (2x3 list | None, reason)}. A negligible motion is kept
    as the identity because the camera was effectively stationary.
    """
    boxes = defaultdict(list)
    pairs = set()
    for row in tracks:
        points = row["points"]
        for frame, point in points:
            boxes[frame].append(point)
        for (a, _), (b, _) in zip(points, points[1:]):
            if b == a + 1:
                pairs.add(b)
    result = {}
    for target in sorted(pairs):
        previous = frames[target - 1].astype(np.float32) / TEMP_SCALE
        current = frames[target].astype(np.float32) / TEMP_SCALE
        transform, stats = estimate_motion(previous, current, boxes[target - 1], boxes[target], roi,
                                           min_inlier_ratio=min_inlier_ratio)
        reason = stats["reason"]
        if transform is None and reason == "negligible_motion":
            transform = np.array(((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)))
        result[target] = (None if transform is None else np.asarray(transform).tolist(), reason)
    return result


def motion_features(row, transforms):
    residuals, flows, raws, scales = [], [], [], []
    links = 0
    for (a, p), (b, q) in zip(row["points"], row["points"][1:]):
        if b != a + 1:
            continue
        links += 1
        transform = transforms.get(b, (None, None))[0]
        if transform is None:
            continue
        start, end = np.array(centroid(p)), np.array(centroid(q))
        predicted = np.asarray(transform) @ np.append(start, 1.0)
        residuals.append(float(np.linalg.norm(end - predicted)))
        flows.append(float(np.linalg.norm(predicted - start)))
        raws.append(float(np.linalg.norm(end - start)))
        scales.append(math.sqrt(max(1.0, p["area_px"])))
    if not residuals:
        return {"motion_links": links, "motion_valid_links": 0, "residual_median_px": None,
                "residual_p90_px": None, "flow_median_px": None, "raw_median_px": None,
                "residual_relative": None, "residual_per_size": None}
    residual = float(np.median(residuals))
    flow = float(np.median(flows))
    return {
        "motion_links": links,
        "motion_valid_links": len(residuals),
        "residual_median_px": residual,
        "residual_p90_px": float(np.percentile(residuals, 90)),
        "flow_median_px": flow,
        "raw_median_px": float(np.median(raws)),
        # Near 0: moves with the road. Near 1 or above: stays put or moves on its own.
        "residual_relative": residual / (flow + 1.0),
        "residual_per_size": residual / float(np.median(scales)),
    }


def _box(point, pad=0):
    x0, y0, x1, y1 = (point[key] for key in BOX_KEYS)
    return x0 - pad, y0 - pad, x1 + pad, y1 + pad


def _touch(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def same_object(a, b, max_gap=3, pad=8):
    """True when two tracks show one hot object: padded boxes touch within max_gap frames.

    Covers simultaneous parts of one vehicle (lamp, exhaust, tyre) and a track
    broken and restarted on the same spot. Deliberately not transitive.
    """
    pa, pb = a["points"], b["points"]
    if pb[0][0] - pa[-1][0] > max_gap or pa[0][0] - pb[-1][0] > max_gap:
        return False
    boxes = {frame: _box(point, pad) for frame, point in pb}
    for frame, point in pa:
        box = _box(point, pad)
        for offset in range(-max_gap, max_gap + 1):
            other = boxes.get(frame + offset)
            if other is not None and _touch(box, other):
                return True
    return False


def select_distinct(ranked, frame_start, frame_end, limit, max_gap=3, pad=8, *,
                    match=None, on_skip=None):
    """Ten-interval round robin that skips a track touching an already selected one."""
    interval_count = min(10, limit)
    intervals = [[] for _ in range(interval_count)]
    span = frame_end - frame_start
    for item in ranked:
        offset = max(0, int(item["representative_frame"]) - frame_start)
        intervals[min(interval_count - 1, offset * interval_count // span)].append(item)
    selected, skipped = [], 0
    cursors = [0] * interval_count
    while len(selected) < limit:
        added = False
        for k, interval in enumerate(intervals):
            while cursors[k] < len(interval) and len(selected) < limit:
                item = interval[cursors[k]]
                cursors[k] += 1
                blocker = next((chosen for chosen in selected
                                if (match(item, chosen) if match is not None
                                    else same_object(item, chosen, max_gap, pad))), None)
                if blocker is not None:
                    skipped += 1
                    if on_skip is not None:
                        on_skip(item, blocker)
                    continue
                selected.append(item)
                added = True
                break
        if not added:
            break
    order = {id(item): i for i, item in enumerate(ranked)}
    return sorted(selected, key=lambda item: order[id(item)]), skipped

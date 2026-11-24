"""Estimate local road image motion and use it in experimental greedy tracking."""

from __future__ import annotations

import math
from collections import defaultdict

import cv2
import numpy as np

from heatpipe_service.analysis.rendering import normalize_u8
from heatpipe_service.analysis.tracking import centroid


def feature_mask(shape, candidates, road_band_start):
    height, width = shape
    mask = np.zeros(shape, dtype=np.uint8)
    mask[int(height * road_band_start) : int(height * 0.94), int(width * 0.12) : int(width * 0.88)] = 255
    for row in candidates:
        # Hot components include vehicle parts and are unreliable alignment anchors.
        x0, y0, x1, y1 = (row[f"bbox_{key}"] for key in ("x0", "y0", "x1", "y1"))
        cv2.rectangle(mask, (x0 - 4, y0 - 4), (x1 + 4, y1 + 4), 0, -1)
    return mask


def estimate_motion(previous, current, previous_candidates, current_candidates, roi, road_band_start=0.70,
                    min_inlier_ratio=0.70):
    lo, hi = np.percentile(np.stack((previous, current)), (2, 98))
    a, b = (normalize_u8(frame, float(lo), float(hi)) for frame in (previous, current))
    start_mask = feature_mask(a.shape, previous_candidates, road_band_start)
    end_mask = feature_mask(b.shape, current_candidates, road_band_start)
    source = cv2.goodFeaturesToTrack(a, 160, 0.01, 6, mask=start_mask)
    if source is None or len(source) < 12:
        return None, {"reason": "few_features"}
    target, forward, _ = cv2.calcOpticalFlowPyrLK(a, b, source, None, winSize=(21, 21), maxLevel=3)
    if target is None or forward is None:
        return None, {"reason": "few_reliable_pairs"}
    keep = forward.ravel() == 1
    source, target = source[keep], target[keep]
    if len(source) < 12:
        return None, {"reason": "few_reliable_pairs"}
    returned, backward, _ = cv2.calcOpticalFlowPyrLK(b, a, target, None, winSize=(21, 21), maxLevel=3)
    if returned is None or backward is None:
        return None, {"reason": "few_reliable_pairs"}
    source, target, returned = (array.reshape(-1, 2) for array in (source, target, returned))
    keep = (backward.ravel() == 1) & (np.linalg.norm(source - returned, axis=1) <= 1.0)
    source, target = source[keep], target[keep]
    height, width = a.shape
    xy = np.rint(target).astype(int)
    inside = (xy[:, 0] >= 0) & (xy[:, 0] < width) & (xy[:, 1] >= 0) & (xy[:, 1] < height)
    source, target, xy = source[inside], target[inside], xy[inside]
    keep = end_mask[xy[:, 1], xy[:, 0]] != 0
    source, target = source[keep], target[keep]
    if len(source) < 12:
        return None, {"reason": "few_reliable_pairs", "pairs": len(source)}
    transform, inliers = cv2.estimateAffinePartial2D(source, target, method=cv2.RANSAC,
                                                   ransacReprojThreshold=2.0)
    if transform is None or inliers is None:
        return None, {"reason": "no_transform", "pairs": len(source)}
    keep = inliers.ravel().astype(bool)
    src, dst = source[keep], target[keep]
    stats = {"pairs": len(source), "inliers": len(src)}
    if len(src) < 12 or len(src) / len(source) < min_inlier_ratio:
        return None, {"reason": "weak_consensus", **stats}
    span_x, span_y = np.ptp(src, axis=0)
    if span_x < width * 0.20 or span_y < height * 0.08:
        return None, {"reason": "localized_features", **stats}
    x0, y0, x1, y1 = roi
    probes = np.array([(x0, y0), (x1, y0), (x0, y1), (x1, y1),
                       ((x0 + x1) / 2, (y0 + y1) / 2)])
    projection = probes @ transform[:, :2].T + transform[:, 2]
    movement = np.linalg.norm(projection - probes, axis=1)
    scale = float(np.linalg.norm(transform[:, 0]))
    stats["center_shift_px"] = float(movement[-1])
    stats["max_roi_shift_px"] = float(movement.max())
    if not 0.85 <= scale <= 1.18 or movement.max() > 55:
        return None, {"reason": "implausible_motion", **stats}
    if movement.max() < 0.75:
        return None, {"reason": "negligible_motion", **stats}
    stats.update(reason="applied", source_points=src.tolist(), target_points=dst.tolist())
    return transform, stats


def build_tracks_with_motion(by_frame, max_distance, transformations):
    """Apply motion only to the previous center; keep observed boxes and frame indices."""
    active, finished = [], []
    for frame, candidates in sorted(by_frame.items()):
        recent = []
        for track in active:
            (recent if frame - track["last_frame"] == 1 else finished).append(track)
        active = recent
        used = set()
        transform = transformations.get(frame)
        for track in active:
            x, y = centroid(track["points"][-1][1])
            if transform is not None:
                x, y = transform @ np.array((x, y, 1.0))
            best, distance = None, max_distance
            for i, candidate in enumerate(candidates):
                if i not in used:
                    cx, cy = centroid(candidate)
                    d = math.hypot(cx - x, cy - y)
                    if d < distance:
                        best, distance = i, d
            if best is not None:
                track["points"].append((frame, candidates[best]))
                track["last_frame"] = frame
                used.add(best)
        active.extend({"points": [(frame, point)], "last_frame": frame}
                      for i, point in enumerate(candidates) if i not in used)
    return finished + active


def stitch_with_motion(tracks, transformations):
    """Keep established greedy links and try only unambiguous adjacent track ends."""
    starts, ends = defaultdict(list), defaultdict(list)
    for i, track in enumerate(tracks):
        starts[track["points"][0][0]].append(i)
        ends[track["points"][-1][0]].append(i)
    successors, predecessors = {}, set()
    for frame, transform in sorted(transformations.items()):
        options, reverse = defaultdict(list), defaultdict(list)
        for i in ends[frame - 1]:
            source = tracks[i]["points"][-1][1]
            center = np.array(centroid(source))
            predicted = transform @ np.append(center, 1.0)
            if np.linalg.norm(center - predicted) < 5.0:
                continue
            x0, y0, x1, y1 = (source[f"bbox_{key}"] for key in ("x0", "y0", "x1", "y1"))
            corners = np.array(((x0, y0, 1), (x0, y1, 1), (x1, y0, 1), (x1, y1, 1)))
            corners = corners @ transform.T
            left, top = corners.min(axis=0)
            right, bottom = corners.max(axis=0)
            area = (right - left + 1) * (bottom - top + 1)
            for j in starts[frame]:
                target = tracks[j]["points"][0][1]
                observed = np.asarray(centroid(target))
                error = np.linalg.norm(observed - predicted)
                if error > 8.0 or np.linalg.norm(observed - center) - error < 5.0:
                    continue
                if not 0.5 <= target["area_px"] / source["area_px"] <= 2.0:
                    continue
                a, b, c, d = (target[f"bbox_{key}"] for key in ("x0", "y0", "x1", "y1"))
                intersection = max(0.0, min(c, right) - max(a, left) + 1) * (
                    max(0.0, min(d, bottom) - max(b, top) + 1)
                )
                if intersection / (area + (c - a + 1) * (d - b + 1) - intersection) >= 0.25:
                    options[i].append(j)
                    reverse[j].append(i)
        for i, matches in options.items():
            if len(matches) == 1 and len(reverse[matches[0]]) == 1:
                successors[i] = matches[0]
                predecessors.add(matches[0])
    result = []
    for i, track in enumerate(tracks):
        if i in predecessors:
            continue
        if i not in successors:
            result.append(track)
            continue
        points = list(track["points"])
        while i in successors:
            i = successors[i]
            points.extend(tracks[i]["points"])
        result.append({"points": points, "last_frame": points[-1][0]})
    return result

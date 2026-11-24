"""Compose E1 vehicle reranking and shared frame reads for a local experiment."""

from __future__ import annotations

import json

import numpy as np
from scipy.ndimage import binary_erosion

from compare_reranking import track_features
from heatpipe_service.analysis.io import (
    ATT_HEADER_SIZE, TEMP_SCALE, att_frame_count, read_att_header,
)
from heatpipe_service.analysis.roi import grid_cell_roi
from vehicle_rerank import PENALTY, fit_model, score_tracks


class SharedFrameAccess:
    def __init__(self, att_path, signed=False):
        self.att_path = att_path
        self.header = read_att_header(att_path)
        count = att_frame_count(att_path, self.header)
        dtype = "<i2" if signed else "<u2"
        self.frames = np.memmap(
            att_path, dtype=dtype, mode="r", offset=ATT_HEADER_SIZE,
            shape=(count, self.header.height, self.header.width),
        ) if count else np.empty((0, self.header.height, self.header.width), dtype=dtype)

    def read_temperature(self, path, index, header=None):
        if path != self.att_path:
            raise ValueError("The optimized reader serves one ATT per run")
        if not 0 <= index < len(self.frames):
            raise IndexError(f"ATT frame {index} is outside the file")
        return self.frames[index].astype(np.float32) / TEMP_SCALE


class FrameGradient:
    def __init__(self):
        self.last_grid = None
        self.gradient = None

    def edge_sharpness(self, grid, mask):
        boundary = mask & ~binary_erosion(mask)
        if not boundary.any():
            return 0.0
        if grid is not self.last_grid:
            grad_y, grad_x = np.gradient(grid)
            self.gradient = np.sqrt(grad_y**2 + grad_x**2)
            self.last_grid = grid
        return float(self.gradient[boundary].mean())


class OptimizedVehicleRun(SharedFrameAccess, FrameGradient):
    def __init__(self, att_path, config, feature_export, observations, signed):
        self.model, self.training = fit_model(feature_export, observations)
        SharedFrameAccess.__init__(self, att_path, signed)
        FrameGradient.__init__(self)
        self.roi = grid_cell_roi(
            (self.header.height, self.header.width),
            config.display_grid_rows, config.display_grid_columns,
            config.display_grid_rows - 1, (config.display_grid_columns - 1) // 2,
        )

    def rank(self, ranked_tracks):
        for item in ranked_tracks:
            item["features"] = track_features(item, item["track"]["points"], self.frames, self.roi)
        scores, vehicles = score_tracks(self.model, ranked_tracks)
        for item, score, vehicle in zip(ranked_tracks, scores, vehicles, strict=True):
            item["vehicle_rerank"] = {"score": float(score), "vehicle_score": float(vehicle)}
        order = np.argsort(-scores, kind="stable")
        return [ranked_tracks[i] for i in order]

    def write_selection(self, path, baseline, selected):
        payload = {
            **self.training,
            "penalty": PENALTY,
            "e1_track_ids": [item["summary"]["track_id"] for item in baseline],
            "optimized_track_ids": [item["summary"]["track_id"] for item in selected],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

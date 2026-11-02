"""Lossless thermal rendering and Unicode-safe image output."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def imwrite_unicode(
    path: Path, image: np.ndarray, extension: str = ".png", params: list[int] | None = None
) -> bool:
    success, buffer = cv2.imencode(extension, image, params or [])
    if not success:
        return False
    Path(path).write_bytes(buffer.tobytes())
    return True


def normalize_u8(frame_c: np.ndarray, low: float | None = None, high: float | None = None) -> np.ndarray:
    if low is None:
        low = float(frame_c.min())
    if high is None:
        high = float(frame_c.max())
    normalized = np.clip((frame_c - low) / max(high - low, 1e-6), 0, 1)
    return (normalized * 255).astype(np.uint8)


def colorize(frame_c: np.ndarray, low: float | None = None, high: float | None = None) -> np.ndarray:
    return cv2.applyColorMap(normalize_u8(frame_c, low, high), cv2.COLORMAP_JET)


def to_grayscale(
    frame_c: np.ndarray, low: float | None = None, high: float | None = None
) -> np.ndarray:
    return cv2.cvtColor(normalize_u8(frame_c, low, high), cv2.COLOR_GRAY2BGR)


def draw_result_image(
    frame_c: np.ndarray,
    candidate_bbox: tuple[int, int, int, int],
    candidate_id: str,
    grid_rows: int = 2,
    grid_columns: int = 3,
    analysis_roi: tuple[int, int, int, int] | None = None,
) -> np.ndarray:
    """Show the display grid, analysis ROI and candidate on the full frame."""
    if grid_rows <= 0 or grid_columns <= 0:
        raise ValueError("display grid dimensions must be positive")

    image = colorize(frame_c)
    height, width = frame_c.shape
    grid_color = (255, 255, 255)
    for column in range(1, grid_columns):
        x = int(round(width * column / grid_columns))
        cv2.line(image, (x, 0), (x, height - 1), grid_color, 1, cv2.LINE_AA)
    for row in range(1, grid_rows):
        y = int(round(height * row / grid_rows))
        cv2.line(image, (0, y), (width - 1, y), grid_color, 1, cv2.LINE_AA)

    if analysis_roi is not None:
        rx0, ry0, rx1, ry1 = analysis_roi
        cv2.rectangle(image, (rx0, ry0), (rx1 - 1, ry1 - 1), (0, 0, 255), 2, cv2.LINE_AA)

    x0, y0, x1, y1 = candidate_bbox
    cv2.rectangle(image, (x0, y0), (x1, y1), (0, 255, 0), 2, cv2.LINE_AA)
    label_y = max(14, y0 - 5)
    cv2.putText(
        image,
        candidate_id,
        (x0, label_y),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (0, 255, 0),
        1,
        cv2.LINE_AA,
    )
    return image


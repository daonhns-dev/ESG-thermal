"""Export numbered RGB/ATT contact sheets for manual synchronization review."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.experiments.road_thermal._att_io import read_att_frame_raw, read_att_header

TILE_WIDTH = 420
TILE_HEIGHT = 315
LABEL_HEIGHT = 30
PADDING = 4


def review_indices(total: int, anchors: list[int] | None, radius: int, step: int) -> list[list[int]]:
    if total < 1 or radius < 0 or step < 1:
        raise ValueError("total and step must be positive; radius must be nonnegative")
    chosen = anchors if anchors is not None else [total // 6, total // 2, 5 * total // 6]
    if any(index < 0 or index >= total for index in chosen):
        raise ValueError(f"anchor index must be in 0..{total - 1}")
    return [list(range(max(0, anchor - radius), min(total, anchor + radius + 1), step))
            for anchor in chosen]


def _rgb_at(capture: cv2.VideoCapture, index: int) -> np.ndarray:
    if not capture.set(cv2.CAP_PROP_POS_FRAMES, index):
        raise ValueError(f"AVI seek failed at frame {index}")
    ok, frame = capture.read()
    if not ok:
        raise ValueError(f"AVI decode failed at frame {index}")
    position = capture.get(cv2.CAP_PROP_POS_FRAMES)
    if abs(position - (index + 1)) > 0.5:
        raise ValueError(f"AVI seek returned frame position {position}, expected {index + 1}")
    return frame


def _fit(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    scale = min(TILE_WIDTH / width, TILE_HEIGHT / height)
    new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
    resized = cv2.resize(frame, new_size, interpolation=cv2.INTER_AREA)
    tile = np.full((TILE_HEIGHT, TILE_WIDTH, 3), 20, dtype=np.uint8)
    x = (TILE_WIDTH - new_size[0]) // 2
    y = (TILE_HEIGHT - new_size[1]) // 2
    tile[y:y + new_size[1], x:x + new_size[0]] = resized
    return tile


def _thermal_color(frame: np.ndarray, lo: float, hi: float) -> np.ndarray:
    scaled = np.clip((frame.astype(np.float32) - lo) / max(hi - lo, 1), 0, 1)
    return cv2.applyColorMap((scaled * 255).astype(np.uint8), cv2.COLORMAP_JET)


def _tile(canvas: np.ndarray, row: int, col: int, frame: np.ndarray | None, label: str) -> None:
    x = col * (TILE_WIDTH + PADDING)
    y = row * (TILE_HEIGHT + LABEL_HEIGHT + PADDING)
    cv2.putText(canvas, label, (x + 6, y + 21), cv2.FONT_HERSHEY_SIMPLEX,
                0.62, (235, 235, 235), 1, cv2.LINE_AA)
    if frame is not None:
        canvas[y + LABEL_HEIGHT:y + LABEL_HEIGHT + TILE_HEIGHT, x:x + TILE_WIDTH] = _fit(frame)


def export_sheets(
    report: dict, output_dir: Path, anchors: list[int] | None,
    radius: int, step: int, offsets: list[int] | None,
) -> list[Path]:
    total = report["att_frames"]
    if report["avi_decoded_frames"] != total:
        raise ValueError("AVI/ATT counts in motion report do not match")
    chosen = anchors if anchors is not None else [total // 6, total // 2, 5 * total // 6]
    windows = review_indices(total, chosen, radius, step)
    best = report["windows"]["full"]["top_distinct_candidates"]
    if offsets is None:
        if not best:
            raise ValueError("no motion offset candidate; provide --offsets explicitly")
        candidate = best[0]["thermal_minus_rgb_frames"]
        offsets = [candidate - 1, candidate, candidate + 1]
    offsets = list(dict.fromkeys(offsets))
    if not offsets:
        raise ValueError("at least one offset is required")
    targets = [output_dir / f"{report['session']}_rgb_{anchor}.png" for anchor in chosen]
    if any(path.exists() for path in targets):
        raise FileExistsError("review PNG already exists; choose a new --output-dir")

    att_path = Path(report["att_path"])
    avi_path = Path(report["avi_path"])
    header = read_att_header(att_path)
    zero_indices = set(report["zero_att_indices"])
    thermal_indices = {rgb_idx + offset for row in windows for rgb_idx in row for offset in offsets
                       if 0 <= rgb_idx + offset < total}
    thermal = {index: read_att_frame_raw(att_path, index, header) for index in sorted(thermal_indices)}
    valid_frames = [frame.ravel()[::8] for index, frame in thermal.items() if index not in zero_indices]
    if not valid_frames:
        raise ValueError("all selected thermal frames are zero or out of range")
    lo, hi = np.percentile(np.concatenate(valid_frames), [1, 99])

    capture = cv2.VideoCapture(str(avi_path))
    if not capture.isOpened():
        raise ValueError(f"cannot open AVI: {avi_path}")
    try:
        rgb_indices = sorted({index for row in windows for index in row})
        rgb = {index: _rgb_at(capture, index) for index in rgb_indices}
    finally:
        capture.release()

    output_dir.mkdir(parents=True, exist_ok=True)
    for path, indices in zip(targets, windows):
        rows = 1 + len(offsets)
        width = len(indices) * (TILE_WIDTH + PADDING) - PADDING
        height = rows * (TILE_HEIGHT + LABEL_HEIGHT + PADDING) - PADDING
        canvas = np.full((height, width, 3), 35, dtype=np.uint8)
        for col, rgb_idx in enumerate(indices):
            _tile(canvas, 0, col, rgb[rgb_idx], f"RGB AVI  #{rgb_idx}")
            for row, offset in enumerate(offsets, 1):
                thermal_idx = rgb_idx + offset
                frame = thermal.get(thermal_idx)
                if frame is None or thermal_idx in zero_indices:
                    label = f"ATT #{thermal_idx}  unavailable"
                    colored = None
                else:
                    label = f"ATT #{thermal_idx}  offset {offset:+d}"
                    colored = _thermal_color(frame, lo, hi)
                _tile(canvas, row, col, colored, label)
        if not cv2.imwrite(str(path), canvas):
            raise OSError(f"could not write {path}")
    return targets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True, help="motion_sync.py analyze JSON")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--indices", type=int, nargs="+", help="RGB anchor indices; default: three segment centers")
    parser.add_argument("--radius", type=int, default=4, help="frames on either side of each anchor")
    parser.add_argument("--step", type=int, default=2, help="contact-sheet frame spacing")
    parser.add_argument("--offsets", type=int, nargs="+", help="ATT index minus RGB index; default: best +/- 1")
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    try:
        paths = export_sheets(report, args.output_dir, args.indices, args.radius, args.step, args.offsets)
    except (ValueError, FileExistsError, OSError) as error:
        parser.error(str(error))
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()

"""Export separate, pixel-preserving RGB and thermal frame sheets for event annotation."""

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


def validate_crop(crop: list[int] | None, width: int, height: int) -> tuple[int, int, int, int]:
    if crop is None:
        return (0, 0, width, height)
    x1, y1, x2, y2 = crop
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError(f"crop {crop} must fit within {width}x{height}")
    return (x1, y1, x2, y2)


def frame_groups(start: int, stop: int, count: int = 6) -> list[list[int]]:
    if start < 0 or stop < start or count < 1:
        raise ValueError("invalid frame range or page size")
    return [list(range(index, min(index + count, stop + 1)))
            for index in range(start, stop + 1, count)]


def _rgb_frames(avi: Path, start: int, stop: int) -> list[np.ndarray]:
    capture = cv2.VideoCapture(str(avi))
    if not capture.isOpened():
        raise ValueError(f"cannot open AVI: {avi}")
    frames = []
    try:
        if not capture.set(cv2.CAP_PROP_POS_FRAMES, start):
            raise ValueError(f"cannot seek to RGB frame {start}")
        for index in range(start, stop + 1):
            ok, frame = capture.read()
            if not ok or abs(capture.get(cv2.CAP_PROP_POS_FRAMES) - (index + 1)) > 0.5:
                raise ValueError(f"cannot decode RGB frame {index} at its expected index")
            frames.append(frame)
    finally:
        capture.release()
    return frames


def _thermal_frames(att: Path, start: int, stop: int) -> list[np.ndarray]:
    header = read_att_header(att)
    return [read_att_frame_raw(att, index, header) for index in range(start, stop + 1)]


def thermal_display_range(
    frames: list[np.ndarray], crop: tuple[int, int, int, int]
) -> tuple[float, float]:
    x1, y1, x2, y2 = crop
    samples = [frame[y1:y2, x1:x2].ravel()[::8] for frame in frames if frame.any()]
    if not samples:
        raise ValueError("selected thermal range contains only zero frames")
    lo, hi = np.percentile(np.concatenate(samples), [1, 99])
    return float(lo), float(hi)


def _sheet(frames: list[np.ndarray], first_index: int, name: str, output_dir: Path,
           crop: tuple[int, int, int, int], lo: float | None = None,
           hi: float | None = None) -> list[Path]:
    x1, y1, x2, y2 = crop
    tile_width = 600
    tile_height = round(tile_width * (y2 - y1) / (x2 - x1))
    if tile_height > 900:
        tile_height = 900
    label_height = 34
    paths = []
    for group in frame_groups(first_index, first_index + len(frames) - 1):
        rows = (len(group) + 2) // 3
        canvas = np.full((rows * (tile_height + label_height), 3 * tile_width, 3),
                         28, dtype=np.uint8)
        for slot, index in enumerate(group):
            frame = frames[index - first_index][y1:y2, x1:x2]
            if name == "thermal":
                if not frame.any():
                    frame = np.zeros((tile_height, tile_width, 3), dtype=np.uint8)
                    label = f"ATT #{index} EMPTY"
                else:
                    scaled = np.clip((frame.astype(np.float32) - lo) / max(hi - lo, 1), 0, 1)
                    frame = cv2.applyColorMap((scaled * 255).astype(np.uint8), cv2.COLORMAP_JET)
                    label = f"ATT #{index}"
            else:
                label = f"RGB AVI #{index}"
            col, row = slot % 3, slot // 3
            x, y = col * tile_width, row * (tile_height + label_height)
            cv2.putText(canvas, label, (x + 8, y + 24), cv2.FONT_HERSHEY_SIMPLEX,
                        0.72, (245, 245, 245), 1, cv2.LINE_AA)
            enlarged = cv2.resize(frame, (tile_width, tile_height), interpolation=cv2.INTER_NEAREST)
            canvas[y + label_height:y + label_height + tile_height, x:x + tile_width] = enlarged
        path = output_dir / f"{name}_{group[0]}_{group[-1]}.png"
        if not cv2.imwrite(str(path), canvas):
            raise OSError(f"could not write {path}")
        paths.append(path)
    return paths


def export_event_review(
    report: dict, output_dir: Path, rgb_range: tuple[int, int], thermal_range: tuple[int, int],
    rgb_crop: list[int] | None, thermal_crop: list[int] | None,
    thermal_scale_crop: list[int] | None = None,
) -> dict[str, list[Path]]:
    rgb_start, rgb_stop = rgb_range
    thermal_start, thermal_stop = thermal_range
    if not (0 <= rgb_start <= rgb_stop < report["avi_decoded_frames"]):
        raise ValueError("RGB range is outside decoded AVI")
    if not (0 <= thermal_start <= thermal_stop < report["att_frames"]):
        raise ValueError("thermal range is outside ATT")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError("output directory is not empty; choose a new --output-dir")
    avi = Path(report["avi_path"])
    att = Path(report["att_path"])
    capture = cv2.VideoCapture(str(avi))
    try:
        if not capture.isOpened():
            raise ValueError(f"cannot open AVI: {avi}")
        rgb_size = (int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                    int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    finally:
        capture.release()
    header = read_att_header(att)
    rgb_bounds = validate_crop(rgb_crop, *rgb_size)
    thermal_bounds = validate_crop(thermal_crop, header.width, header.height)
    scale_bounds = validate_crop(thermal_scale_crop, header.width, header.height)
    rgb_frames = _rgb_frames(avi, rgb_start, rgb_stop)
    thermal_frames = _thermal_frames(att, thermal_start, thermal_stop)
    lo, hi = thermal_display_range(thermal_frames, scale_bounds)
    output_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "rgb": _sheet(rgb_frames, rgb_start, "rgb", output_dir, rgb_bounds),
        "thermal": _sheet(thermal_frames, thermal_start, "thermal", output_dir,
                          thermal_bounds, lo, hi),
    }
    metadata = {
        "rgb_range": list(rgb_range),
        "thermal_range": list(thermal_range),
        "rgb_crop": list(rgb_bounds),
        "thermal_crop": list(thermal_bounds),
        "thermal_scale_crop": list(scale_bounds),
        "thermal_scale_percentiles": [1, 99],
        "thermal_scale_raw": [lo, hi],
        "thermal_scale_celsius": [lo / 100, hi / 100],
    }
    (output_dir / "thermal_scale.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True, help="motion_sync.py report")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--rgb-range", type=int, nargs=2, metavar=("START", "STOP"), required=True)
    parser.add_argument("--thermal-range", type=int, nargs=2, metavar=("START", "STOP"), required=True)
    parser.add_argument("--rgb-crop", type=int, nargs=4, metavar=("X1", "Y1", "X2", "Y2"))
    parser.add_argument("--thermal-crop", type=int, nargs=4, metavar=("X1", "Y1", "X2", "Y2"))
    parser.add_argument("--thermal-scale-crop", type=int, nargs=4,
                        metavar=("X1", "Y1", "X2", "Y2"),
                        help="source region for one fixed thermal display range; default: full frames")
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    try:
        paths = export_event_review(report, args.output_dir, tuple(args.rgb_range),
                                    tuple(args.thermal_range), args.rgb_crop, args.thermal_crop,
                                    args.thermal_scale_crop)
    except (ValueError, FileExistsError, OSError) as error:
        parser.error(str(error))
    for name, files in paths.items():
        print(f"{name}: {len(files)} sheets in {args.output_dir}")


if __name__ == "__main__":
    main()

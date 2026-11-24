"""Count raw ATT high-bit samples and inspect baseline candidate boxes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from heatpipe_service.analysis.io import ATT_HEADER_SIZE, TEMP_SCALE, att_frame_count, read_att_header
from heatpipe_service.analysis.rendering import draw_result_image, imwrite_unicode


def audit(path, result_path):
    header = read_att_header(path)
    count = att_frame_count(path, header)
    raw = np.memmap(path, dtype="<u2", mode="r", offset=ATT_HEADER_SIZE,
                    shape=(count, header.height, header.width))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    roi = result["processing"]["analysis_roi"]
    x, y = roi["x"], roi["y"]
    w, h = roi["width"], roi["height"]
    histogram = np.zeros(65536, dtype=np.int64)
    affected_frames = affected_roi_frames = affected_roi_pixels = 0
    for start in range(0, count, 64):
        chunk = raw[start:start + 64]
        mask = chunk >= 32768
        cropped = mask[:, y:y+h, x:x+w]
        affected_frames += int(mask.any(axis=(1, 2)).sum())
        affected_roi_frames += int(cropped.any(axis=(1, 2)).sum())
        affected_roi_pixels += int(cropped.sum())
        histogram += np.bincount(chunk[mask], minlength=65536)
    candidates = []
    for candidate in result["candidates"]:
        frame = candidate["representative_frame"]
        b = candidate["candidate_bbox"]
        region = raw[frame, b["y"]:b["y"]+b["height"], b["x"]:b["x"]+b["width"]]
        high = region[region >= 32768]
        if high.size:
            candidates.append({
                "rank": candidate["rank"], "frame": frame,
                "high_bit_pixels": int(high.size),
                "unsigned_range": [int(high.min()), int(high.max())],
                "signed_range": [int(high.view("<i2").min()), int(high.view("<i2").max())],
            })
    nonzero = np.flatnonzero(histogram)
    return {
        "file": path.name, "frames": count, "high_bit_frames": affected_frames,
        "high_bit_roi_frames": affected_roi_frames, "high_bit_roi_pixels": affected_roi_pixels,
        "high_bit_value_range": [int(nonzero.min()), int(nonzero.max())] if nonzero.size else None,
        "raw_ffff_pixels": int(histogram[-1]), "distinct_high_bit_values": int(nonzero.size),
        "high_bit_samples_common": [
            {"raw": int(i), "signed": int(i) - 65536, "count": int(histogram[i])}
            for i in sorted(nonzero, key=lambda i: -histogram[i])[:10]
        ],
        "affected_candidate_boxes": candidates,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("att", type=Path)
    parser.add_argument("result", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    stats = audit(args.att, args.result)
    args.output.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    if stats["affected_candidate_boxes"]:
        candidate = stats["affected_candidate_boxes"][0]
        result = json.loads(args.result.read_text(encoding="utf-8"))
        box = result["candidates"][candidate["rank"] - 1]["candidate_bbox"]
        header = read_att_header(args.att)
        raw = np.memmap(args.att, dtype="<u2", mode="r",
                        offset=ATT_HEADER_SIZE + candidate["frame"] * header.frame_bytes,
                        shape=(header.height, header.width))
        bbox = (box["x"], box["y"], box["x"] + box["width"] - 1, box["y"] + box["height"] - 1)
        images = []
        for tag, data in (("uint16", raw), ("int16 test", raw.view("<i2"))):
            frame = data.astype(np.float32) / TEMP_SCALE
            images.append(draw_result_image(frame, bbox, tag))
        comparison = args.output.with_name("decode_comparison.png")
        if not imwrite_unicode(comparison, np.hstack(images)):
            raise OSError(f"Could not write {comparison}")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

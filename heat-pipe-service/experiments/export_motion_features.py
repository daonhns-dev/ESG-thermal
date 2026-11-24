"""Export road-motion residual features for every eligible E1 track."""

from __future__ import annotations

import argparse
import gzip
import json
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2

from aggregate_14_pairs import PROJECT, directory
from ego_motion import frame_transforms, load_run, motion_features, open_frames

DEV_STEMS = (
    "20260812_012856", "20260813_132634", "20260813_231950", "20260814_122052",
    "20260814_233331", "20260815_232817", "20260816_120017", "20260816_230532",
    "20260817_225314", "20260818_000446", "20260818_003507",
)


def export_one(stem, run_dir, att_path, output, signed, min_inlier_ratio):
    cv2.setNumThreads(1)
    started = time.perf_counter()
    tracks, roi, frame_range = load_run(run_dir)
    frames = open_frames(att_path, frame_range["total_att_frames"], signed)
    transforms = frame_transforms(frames, tracks, roi, min_inlier_ratio)
    reasons = {}
    for _, reason in transforms.values():
        reasons[reason] = reasons.get(reason, 0) + 1
    with gzip.open(output / f"{stem}.jsonl.gz", "wt", encoding="utf-8") as stream:
        for rank, row in enumerate(tracks):
            stream.write(json.dumps({
                "stem": stem, "track_id": row["summary"]["track_id"], "e1_rank": rank,
                **motion_features(row, transforms),
            }) + "\n")
    summary = {"stem": stem, "tracks": len(tracks), "min_inlier_ratio": min_inlier_ratio,
               "transform_pairs": len(transforms), "reasons": reasons,
               "seconds": round(time.perf_counter() - started, 1)}
    (output / f"{stem}.summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--overnight", type=Path,
                        default=PROJECT / "output/experiments/overnight-20261116-v2")
    parser.add_argument("--run", nargs=3, action="append", metavar=("STEM", "RUN_DIR", "ATT"),
                        help="Explicit run instead of the development set")
    parser.add_argument("--signed", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--min-inlier-ratio", type=float, default=0.70)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.run:
        jobs = [(stem, Path(run), Path(att)) for stem, run, att in args.run]
    else:
        atts = {p.stem: p for p in (PROJECT / "data/experiments/raw").glob("*/*.att")}
        jobs = [(stem, directory(stem, "E1", args.overnight), atts[stem]) for stem in DEV_STEMS]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(export_one, *job, args.output, args.signed, args.min_inlier_ratio) for job in jobs]
        for future in futures:
            print(json.dumps(future.result()), flush=True)


if __name__ == "__main__":
    main()

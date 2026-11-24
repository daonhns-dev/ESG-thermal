"""Build a date-separated review set for experimental vehicle downranking."""

from __future__ import annotations

import argparse
import gzip
import json
import random
from pathlib import Path

import numpy as np

from compare_reranking import track_id
from prepare_visual_review import (
    COLORS, PROJECT, att_frame_count, colorize, coordinates, label_image, read_att_frame_celsius,
    read_att_header, write_json, write_png,
)
from heatpipe_service.jobs.batch import _select_tracks_across_frames
from vehicle_rerank import EVALUATION_FILES, fit_model, score_tracks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reranking", type=Path)
    parser.add_argument("observations", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--penalty", type=float, default=0.30,
                        help="Vehicle score weight in the ranking penalty")
    parser.add_argument("--omit-delta", action="store_true",
                        help="Compare the same classifier without the two local temperature features")
    args = parser.parse_args()
    if not 0 <= args.penalty <= 1:
        parser.error("--penalty must be between 0 and 1")
    feature_export = args.reranking / "track_features.jsonl.gz"
    model, training = fit_model(feature_export, args.observations, args.omit_delta)
    targets = {stem: [] for stem in EVALUATION_FILES}
    with gzip.open(feature_export, "rt", encoding="utf-8") as stream:
        for row in map(json.loads, stream):
            stem = row["stem"]
            if stem in targets:
                targets[stem].append(row)
    original = json.loads((args.reranking / "comparison.json").read_text(encoding="utf-8"))
    inputs = {p.stem: p for p in (PROJECT / "data/experiments/raw").glob("*/*.att")}
    args.output.mkdir(parents=True)
    sheets = args.output / "sheets"
    sheets.mkdir()
    snapshots, manifest, sets = {}, [], {}
    for stem, tracks in targets.items():
        att = inputs[stem]
        header = read_att_header(att)
        frames = att_frame_count(att, header)
        by_id = {track_id(row): row for row in tracks}
        ranking_score, _ = score_tracks(model, tracks, args.penalty, args.omit_delta)
        order = np.argsort(-ranking_score, kind="stable")
        selected = _select_tracks_across_frames([tracks[i] for i in order], 0, frames, 50)
        comparison = original[stem]["50"]
        sets[stem] = {
            "e1": comparison["e1"]["interval"]["track_ids"],
            "e1_track_delta": comparison["e1_track_delta"]["interval"]["track_ids"],
            "e1_vehicle_penalty": [track_id(row) for row in selected],
        }
        union = sorted({id_ for ids in sets[stem].values() for id_ in ids})
        for id_ in union:
            row = by_id[id_]
            frame = row["representative_frame"]
            manifest.append({"stem": stem, "track_id": id_, "frame": frame, "label": "pending",
                             "ranks": {method: ids.index(id_) + 1 for method, ids in sets[stem].items()
                                       if id_ in ids}})
            raw = read_att_frame_celsius(att, frame, header)
            low, high = np.percentile(raw, (2, 99.9))
            snapshots[stem, id_] = colorize(raw, float(low), float(high)), coordinates(row["representative"])
    random.Random(1811).shuffle(manifest)
    panels = []
    for i, row in enumerate(manifest, 1):
        tag = f"V{i:03d}"
        row["review_id"] = tag
        image, box = snapshots[row["stem"], row["track_id"]]
        panels.append(label_image(image, tag, [("", box, COLORS[2])]))
    for start in range(0, len(panels), 6):
        group = panels[start:start + 6]
        group.extend(np.zeros_like(group[0]) for _ in range(6 - len(group)))
        write_png(sheets / f"sheet-{start // 6 + 1:02d}.png",
                  np.vstack([np.hstack(group[:3]), np.hstack(group[3:])]))
    write_json(args.output / "review_manifest.json", manifest)
    write_json(args.output / "review_labels.json", [{"review_id": r["review_id"], "label": "", "note": ""}
                                                    for r in manifest])
    write_json(args.output / "selection.json", {**training, "penalty": args.penalty, "sets": sets})
    lines = ["# 상위 50건 열원 판독", "",
             "차량 관련, 기타 열원, 검토할 노면 열점, 판단 불가로 기록합니다.",
             "[판독 입력 파일](review_labels.json) · [방법별 후보 대응표](review_manifest.json)", ""]
    lines.extend(f"- [{path.stem}](sheets/{path.name})" for path in sorted(sheets.glob("*.png")))
    (args.output / "REVIEW.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(manifest)} distinct candidates, {training['training_samples']} training labels.")


if __name__ == "__main__":
    main()

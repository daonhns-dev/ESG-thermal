"""Prepare or score a small blinded review of E0 and E1+E3's different outputs."""

from __future__ import annotations

import argparse
import gzip
import json
import random
from collections import Counter
from pathlib import Path

from aggregate_14_pairs import OUTPUT, PROJECT, directory
from compare_e0_e3 import track_key
from e3_review import CLASSES, context_strip, open_frames, read, write_json, write_png


FILES = ("20260814_122052", "20260817_225314")
GROUPS = (("e0_only_vs_e1_e3", "E0", "e0"),
          ("e1_e3_only_vs_e0", "E1", "e1_e3"))


def prepare(args):
    comparisons = read(args.audit)["files"]
    overnight = OUTPUT / "overnight-20261116-v2"
    pending = []
    rng = random.Random(61117)
    for stem in FILES:
        att = PROJECT / "data/experiments/raw" / stem[:8] / f"{stem}.att"
        for difference, export, method in GROUPS:
            run = directory(stem, export, overnight)
            keys = rng.sample(comparisons[stem][difference], 3)
            targets = set(map(tuple, keys))
            tracks = {}
            with gzip.open(run / "eligible_tracks.jsonl.gz", "rt", encoding="utf-8") as stream:
                for line in stream:
                    row = json.loads(line)
                    key = track_key(row)
                    if key in targets:
                        if key in tracks:
                            raise ValueError(f"Ambiguous representative in {stem}/{method}: {key}")
                        tracks[key] = row
            frame_range = read(run / "analysis_result.json")["processing"]["frame_range"]
            frames = open_frames(att, frame_range["total_att_frames"])
            for key in map(tuple, keys):
                pending.append((stem, method, key, context_strip(frames, tracks[key])))
    rng.shuffle(pending)
    reviewer = args.output / "reviewer"
    reviewer.mkdir(parents=True, exist_ok=False)
    manifest = []
    for i, (stem, method, key, image) in enumerate(pending, 1):
        tag = f"Q{i:02d}"
        manifest.append({"id": tag, "stem": stem, "method": method, "representative": key})
        write_png(reviewer / f"{tag}.png", image)
    write_json(args.output / "manifest.json", manifest)
    write_json(reviewer / "labels.json", {
        "reviewer_type": "", "labels": [{"id": r["id"], "label": "", "note": ""} for r in manifest],
    })
    lines = [
        "# 상위 후보 비교 판독", "",
        "가운데 +0의 분홍색 사각형이 가리키는 열점을 분류합니다. 앞뒤 프레임을 참고합니다.", "",
        "- `road` · 주변보다 분명히 따뜻한 국소 열점이 노면 위에 보임",
        "- `uncertain` · 표시된 대상을 특정하기 어려움",
        "- `cover` · 맨홀·덮개",
        "- `vehicle` · 차량, 오토바이, 배기구 등 차량 관련 열원",
        "- `other` · 난간, 건물 등 별도로 식별되는 기타 열원",
        "- `artifact` · 센서 또는 영상 처리에서 생긴 영상 이상", "",
        "[입력 양식](labels.json)에 기록하고 완료 후 reviewer_type을 human으로 지정합니다.", "",
    ]
    lines.extend(f"- [{r['id']}]({r['id']}.png)" for r in manifest)
    (reviewer / "REVIEW.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(manifest)} blinded comparison images")


def score(args):
    manifest = read(args.output / "manifest.json")
    form = read(args.output / "reviewer/labels.json")
    labels = {r["id"]: r["label"] for r in form["labels"]}
    if (form["reviewer_type"] != "human" or len(form["labels"]) != len(manifest)
            or set(labels) != {r["id"] for r in manifest} or set(labels.values()) - CLASSES):
        raise ValueError("Expected all human comparison judgments")
    classified = [{**r, "label": labels[r["id"]]} for r in manifest]
    by_file = {stem: {
        method: dict(Counter(r["label"] for r in classified if r["stem"] == stem and r["method"] == method))
        for _, _, method in GROUPS
    } for stem in FILES}
    report = {"by_file": by_file, "classified": classified}
    membership_path = args.output / "factorial-membership.json"
    if membership_path.is_file():
        membership = {r["id"]: r["selected"] for r in read(membership_path)}
        if set(membership) != set(labels):
            raise ValueError("Factorial membership does not cover the review cards")
        report["classified_pilot_cards_by_selection"] = {
            method: dict(Counter(label for tag, label in labels.items() if membership[tag][method]))
            for method in ("e0", "e0_e3", "e1", "e1_e3")
        }
    write_json(args.output / "scored.json", report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True)
    for action in (prepare, score):
        command = commands.add_parser(action.__name__)
        command.add_argument("output", type=Path)
        command.set_defaults(run=action)
        if action == prepare:
            command.add_argument("audit", type=Path)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()

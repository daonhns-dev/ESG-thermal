"""Export two sets of meeting images using recorded review judgments."""

from __future__ import annotations

import argparse
import html
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from aggregate_14_pairs import OUTPUT, PROJECT
from dev_labels import load_labels
from e3_review import OCTOBER, open_runs, read, track_id, write_json, write_png
from ego_motion import open_frames
from heatpipe_service.analysis.io import TEMP_SCALE
from heatpipe_service.analysis.rendering import colorize
from prepare_visual_review import coordinates


def pool():
    root = OUTPUT / "e3-human-review-20261117"
    cards = {r["id"]: r for r in read(root / "internal/manifest.json")}
    entries = {}
    for name in ("candidate_labels_R001-R020_round2.json", "rank-sample/labels.json"):
        path = root / "reviewer" / name
        for answer in read(path)["labels"]:
            card = cards[answer["id"]]
            if answer["label"]:
                entries[card["stem"], card["track_id"]] = {
                    "label": answer["label"], "note": answer.get("note", ""),
                    "source": str(path.relative_to(PROJECT)), "review_id": answer["id"],
                    "provenance": "user_entered" if name.startswith("candidate") else "user_entered_partial_form",
                }
    latest = read(root / "reviewer/zoom-pilot/zoom_labels.json")
    for answer in latest["labels"]:
        if answer["id"] == "R019" and answer["zoom_label"]:
            card = cards[answer["id"]]
            entries[card["stem"], card["track_id"]].update(
                label=answer["zoom_label"], provenance="user_zoom_review", note="난간 하단으로 사용자 판독")
    directory = OUTPUT / "heldout-20261118/review"
    historic_cards = {r["review_id"]: r for r in read(directory / "review_manifest.json")}
    for answer in read(directory / "review_labels.json"):
        card = historic_cards[answer["review_id"]]
        entries.setdefault((card["stem"], card["track_id"]), {
            "label": answer["label"], "note": answer.get("note", ""),
            "source": "output/experiments/heldout-20261118/review/review_labels.json",
            "review_id": answer["review_id"], "provenance": "historical_ai_review",
        })
    labels, _ = load_labels()
    for key, label in labels.items():
        entries.setdefault(key, {"label": label, "note": "", "review_id": "",
                                "source": "dev_labels.load_labels", "provenance": "historical_ai_review"})
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--october-dir", type=Path, default=Path("E:/heatpipe/datasets_TB"))
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    runs, labels = open_runs(), pool()
    candidates = []
    for stem, (tracks, _, meta) in runs.items():
        by_id = {track_id(row): row for row in tracks}
        for (source, tid), annotation in labels.items():
            if source == stem and tid in by_id:
                candidates.append({**annotation, "stem": stem, "track_id": tid,
                                   "track": by_id[tid], "meta": meta})
    road = sorted((r for r in candidates if r["label"] == "road"),
                  key=lambda r: (r["provenance"] == "historical_ai_review", r["stem"], r["track_id"]))
    other = sorted((r for r in candidates if r["label"] in ("vehicle", "other")),
                   key=lambda r: (r["review_id"] not in ("H058", "H027"),
                                  r["provenance"] == "historical_ai_review", r["stem"], r["track_id"]))
    chosen = {}
    for group, items in (("01_road_candidates", road), ("02_other_heat_sources", other)):
        selected, used = [], set()
        # Thin candidates close in representative time to avoid repeated meeting examples.
        for row in items:
            frame = row["track"]["representative_frame"]
            if any(stem == row["stem"] and abs(frame - previous) < 40 for stem, previous in used):
                continue
            if group.startswith("02"):
                counts = Counter(r["label"] for r in selected)
                if row["label"] == "vehicle" and counts["vehicle"] >= 12:
                    continue
                if row["label"] == "other" and counts["other"] >= 8:
                    continue
            selected.append(row)
            used.add((row["stem"], frame))
            if len(selected) == 20:
                break
        if len(selected) != 20:
            raise ValueError(f"Only {len(selected)} examples available in {group}")
        chosen[group] = selected
    args.output.mkdir(parents=True)
    pages, records, cache = [], [], {}
    for group, rows in chosen.items():
        folder = args.output / group
        folder.mkdir()
        tiles = []
        for number, row in enumerate(rows, 1):
            stem, track = row["stem"], row["track"]
            if stem not in cache:
                directory = args.october_dir if stem in OCTOBER else PROJECT / "data/experiments/raw" / stem[:8]
                cache[stem] = open_frames(directory / f"{stem}.att", row["meta"]["total_att_frames"])
            frame = track["representative_frame"]
            raw = cache[stem][frame].astype(np.float32) / TEMP_SCALE
            plausible = raw[raw < 200]
            low, high = np.percentile(plausible, (2, 99.9))
            image = colorize(raw, float(low), float(high))
            x0, y0, x1, y1 = coordinates(track["representative"])
            cv2.rectangle(image, (x0, y0), (x1, y1), (255, 35, 255), 1)
            image = cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
            image = cv2.copyMakeBorder(image, 38, 38, 0, 0, cv2.BORDER_CONSTANT)
            tag = f"{'A' if group.startswith('01') else 'B'}{number:02d}"
            title = f"{tag}  {stem}  frame {frame}"
            cv2.putText(image, title, (12, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255,255,255), 1, cv2.LINE_AA)
            cv2.putText(image, f"Display {low:.2f}..{high:.2f} C | {row['provenance']}",
                        (12, image.shape[0]-12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,255,255), 1, cv2.LINE_AA)
            file = folder / f"{tag}_{stem}_f{frame:06d}.png"
            write_png(file, image)
            record = {k: v for k, v in row.items() if k not in ("track", "meta")}
            record.update(id=tag, frame=frame, image=file.relative_to(args.output).as_posix(),
                          bbox=[x0,y0,x1,y1], display_low_c=float(low), display_high_c=float(high))
            records.append(record)
            tiles.append(cv2.resize(image, (384,326), interpolation=cv2.INTER_AREA))
        for start in (0,10):
            sheet = np.vstack([np.hstack(tiles[i:i+2]) for i in range(start,start+10,2)])
            name = f"{group}_sheet{start//10+1}.png"
            write_png(args.output / name, sheet)
        title = "관로 관련 여부를 확인할 노면 열점 후보" if group.startswith("01") else "차량·구조물 등 다른 열원으로 판독된 후보"
        section = f"<h2>{title} 20장</h2><div class='grid'>"
        for r in records[-20:]:
            note = r["note"] or r["label"]
            source = "사용자 입력" if r["provenance"].startswith("user") else "과거 AI 개발 판독 · 회의 전 확인 필요"
            section += f"<article><a href='{r['image']}'><img src='{r['image']}'></a><p>{r['id']} · {html.escape(note)}<br>{source}</p></article>"
        pages.append(section + "</div>")
    write_json(args.output / "manifest.json", records)
    guide = """# 금요일 회의용 열영상 사례

8월 연구 자료와 10월 실증 자료에서 각 20장을 선정했다. 노면 열점은 관로 관련 여부를 확인할 사례다. 지하 열수송관 위치와 매칭된 현장 정답은 아직 확보되지 않았다.

이미지의 분홍색 사각형이 후보 위치다. 대표 프레임의 온도 분포 2~99.9백분위를 표시 범위로 사용하고 범위를 이미지 하단에 기록했다. 이미지 간 색을 비교할 때 표시 온도 범위를 함께 확인한다.

사용자가 현재 기준으로 입력한 라벨을 우선 사용했다. 부분 입력 양식의 답도 사용자 입력으로 표시했으며, 부족한 사례는 과거 AI 개발 판독에서 보완했다. 과거 AI 판독 사례는 회의 전 사람 확인이 필요하다. 기존 판독 양식에는 새 답을 쓰지 않았다.

[이미지 목록 열기](index.html)

- [노면 후보 20장](01_road_candidates)
- [다른 열원 20장](02_other_heat_sources)
- [이미지별 출처와 판독 기록](manifest.json)
"""
    (args.output / "README.md").write_text(guide, encoding="utf-8")
    head = "<!doctype html><meta charset='utf-8'><title>회의용 열영상 사례</title><style>body{font-family:Arial,sans-serif;margin:24px;background:#f3f4f5;color:#202124}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}img{width:100%}article{background:white;padding:12px}p{line-height:1.5}</style><h1>회의용 열영상 사례</h1><p>8월·10월 자료. 노면 열점의 관로 관련성은 현장 확인이 필요합니다. 분홍색 사각형을 확인하세요.</p>"
    (args.output / "index.html").write_text(head + "".join(pages), encoding="utf-8")
    print(Counter((r["label"], r["provenance"]) for r in records))


if __name__ == "__main__":
    main()

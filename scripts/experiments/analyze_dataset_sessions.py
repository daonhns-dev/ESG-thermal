"""
AIR_thermal train/val/test 데이터셋의 세션 구성을 파일명 기반으로 정밀 분석.

파일명 패턴: AIR_{NOM|AON}_{date}_{location}_{Dx}_{frame_idx}.png
  예) AIR_NOM_20.12.31_CIC_P1345-P1348_D3_000588.png
      AIR_AON_21.01.06_CIC_P1638-P1640_D3_000002.png

목적 (Task A의 채널 선정처럼, 데이터 자체를 뜯어봐서 구조를 파악):
  1) 각 split(train/val/test) x class(normal/anomaly)가 몇 개의 "세션"
     (date+location+Dx 조합)으로 구성되는지 — 세션 수가 적으면 그 자체로
     대표성/과적합 위험 신호.
  2) split 간 세션(날짜) 중복 여부 — 데이터 누수 가능성 재확인.
  3) 세션별 이미지 장수 분포 — 특정 세션 하나가 클래스를 사실상 지배하는지.
  4) location/Dx 필드가 normal/anomaly 간 체계적으로 다른지 — 있다면 그 자체가
     구조적 복잡도 confound의 근본 원인일 수 있음(장면 자체가 다른 곳).

사용법:
  python scripts/experiments/analyze_dataset_sessions.py --data_dir data/AIR_thermal
"""

from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from pathlib import Path

FNAME_RE = re.compile(r"^AIR_(?P<cls>NOM|AON)_(?P<date>\d{2}\.\d{2}\.\d{2})_(?P<location>.+)_(?P<dx>D\d+)_(?P<frame>\d+)$")


def parse(path: Path):
    m = FNAME_RE.match(path.stem)
    if not m:
        return None
    return m.groupdict()


def session_key(meta: dict) -> str:
    return f"{meta['date']}_{meta['location']}_{meta['dx']}"


def scan(root: Path):
    exts = {".png", ".jpg", ".jpeg"}
    records = []
    unparsed = 0
    for p in root.rglob("*"):
        if p.suffix.lower() not in exts:
            continue
        meta = parse(p)
        if meta is None:
            unparsed += 1
            continue
        meta["path"] = p
        records.append(meta)
    return records, unparsed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data_dir", default="data/AIR_thermal")
    args = ap.parse_args()
    root = Path(args.data_dir)

    splits = ["train", "val", "test"]
    classes = ["normal", "anomaly"]

    all_data = {}  # (split, cls) -> records
    for split in splits:
        for cls in classes:
            d = root / split / cls
            if not d.exists():
                continue
            records, unparsed = scan(d)
            all_data[(split, cls)] = records
            if unparsed:
                print(f"[경고] {split}/{cls}: 파일명 패턴 안 맞는 파일 {unparsed}개(제외)")

    print("=" * 78)
    print("  1) split x class 별 세션(날짜+location+Dx) 수 / 이미지 수")
    print("=" * 78)
    session_sets = {}
    for (split, cls), records in all_data.items():
        sessions = Counter(session_key(r) for r in records)
        session_sets[(split, cls)] = set(sessions.keys())
        n_img = len(records)
        n_sess = len(sessions)
        top = sessions.most_common(3)
        top_str = ", ".join(f"{k}:{v}" for k, v in top)
        max_share = (top[0][1] / n_img * 100) if n_img and top else 0
        print(f"  {split:5s}/{cls:8s}  이미지 {n_img:6d}장  세션 {n_sess:3d}개  "
              f"최대세션점유율 {max_share:5.1f}%  top3=[{top_str}]")

    print("\n" + "=" * 78)
    print("  2) split 간 날짜(date) 중복 여부 (data leakage 재확인)")
    print("=" * 78)
    dates_by_split_cls = {}
    for (split, cls), records in all_data.items():
        dates_by_split_cls[(split, cls)] = set(r["date"] for r in records)

    for cls in classes:
        splits_present = [s for s in splits if (s, cls) in dates_by_split_cls]
        for i in range(len(splits_present)):
            for j in range(i + 1, len(splits_present)):
                s1, s2 = splits_present[i], splits_present[j]
                d1, d2 = dates_by_split_cls[(s1, cls)], dates_by_split_cls[(s2, cls)]
                overlap = d1 & d2
                flag = " ← 날짜 겹침!" if overlap else ""
                print(f"  [{cls}] {s1} vs {s2}: {s1}={sorted(d1)}  {s2}={sorted(d2)}"
                      f"  overlap={sorted(overlap)}{flag}")

    print("\n" + "=" * 78)
    print("  3) location 필드 분포 — normal vs anomaly 간 장면(장소) 자체가 다른가?")
    print("=" * 78)
    for split in splits:
        locs = {}
        for cls in classes:
            if (split, cls) not in all_data:
                continue
            locs[cls] = Counter(r["location"] for r in all_data[(split, cls)])
        if not locs:
            continue
        all_locs = set()
        for c in locs.values():
            all_locs |= set(c.keys())
        print(f"  -- {split} --")
        for loc in sorted(all_locs):
            row = "  ".join(f"{cls}={locs.get(cls, {}).get(loc, 0)}" for cls in classes)
            print(f"    {loc:30s} {row}")

    print("\n" + "=" * 78)
    print("  4) Dx(추정: 거리/구역 코드) 분포 — normal vs anomaly")
    print("=" * 78)
    for split in splits:
        dxs = {}
        for cls in classes:
            if (split, cls) not in all_data:
                continue
            dxs[cls] = Counter(r["dx"] for r in all_data[(split, cls)])
        if not dxs:
            continue
        all_dx = set()
        for c in dxs.values():
            all_dx |= set(c.keys())
        print(f"  -- {split} --")
        for dx in sorted(all_dx):
            row = "  ".join(f"{cls}={dxs.get(cls, {}).get(dx, 0)}" for cls in classes)
            print(f"    {dx:6s} {row}")

    print("\n" + "=" * 78)
    print("  5) 요약 세션 카탈로그 (전체 split 통틀어 고유 세션 나열)")
    print("=" * 78)
    all_sessions = defaultdict(lambda: defaultdict(int))  # session -> (split,cls) -> count
    for (split, cls), records in all_data.items():
        for r in records:
            all_sessions[session_key(r)][(split, cls)] += 1
    for sess in sorted(all_sessions.keys()):
        parts = ", ".join(f"{s}/{c}:{n}" for (s, c), n in sorted(all_sessions[sess].items()))
        print(f"  {sess:45s} {parts}")


if __name__ == "__main__":
    main()

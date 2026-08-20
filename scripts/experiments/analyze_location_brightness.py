"""
location(촬영 지점) 필드가 normal/anomaly 라벨과 완전히 분리되어 있음을
`analyze_dataset_sessions.py`로 확인한 뒤, brightness_mean 신호가 실제로는
"이상 여부"가 아니라 "어느 location에서 찍었는가"에 의해 결정되는 것은
아닌지 검증.

방법: normal 라벨 내부(29개 location, train 기준)만 놓고 location별 평균
밝기를 계산 — normal끼리도 location 간 밝기 편차가 normal-vs-anomaly 갭
만큼 크다면, brightness가 location(촬영 조건)의 부산물이라는 강한 증거.

사용법:
  python scripts/experiments/analyze_location_brightness.py --data_dir data/AIR_thermal
"""

from __future__ import annotations

import argparse
import random
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

FNAME_RE = re.compile(r"^AIR_(?P<cls>NOM|AON)_(?P<date>\d{2}\.\d{2}\.\d{2})_(?P<location>.+)_(?P<dx>D\d+)_(?P<frame>\d+)$")


def parse(path: Path):
    m = FNAME_RE.match(path.stem)
    return m.groupdict() if m else None


def load_gray_mean(path: Path, size: int) -> float:
    g = np.array(Image.open(path).convert("L").resize((size, size), Image.BILINEAR))
    return float(g.mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data_dir", default="data/AIR_thermal")
    ap.add_argument("--size", type=int, default=256)
    ap.add_argument("--sample_per_location", type=int, default=150, help="location당 밝기 계산에 쓸 최대 샘플 수 (속도용)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    random.seed(args.seed)

    root = Path(args.data_dir)
    exts = {".png", ".jpg", ".jpeg"}

    # location -> {"cls": set(), "split": set(), "paths": [...]}
    by_location = defaultdict(lambda: {"cls": set(), "split": set(), "paths": []})

    for split in ["train", "val", "test"]:
        for cls_dir in ["normal", "anomaly"]:
            d = root / split / cls_dir
            if not d.exists():
                continue
            for p in d.rglob("*"):
                if p.suffix.lower() not in exts:
                    continue
                meta = parse(p)
                if meta is None:
                    continue
                key = meta["location"]
                by_location[key]["cls"].add(cls_dir)
                by_location[key]["split"].add(split)
                by_location[key]["paths"].append(p)

    print(f"고유 location 수: {len(by_location)}")

    rows = []
    for loc, info in by_location.items():
        paths = info["paths"]
        if len(paths) > args.sample_per_location:
            paths = random.sample(paths, args.sample_per_location)
        means = [load_gray_mean(p, args.size) for p in paths]
        rows.append({
            "location": loc,
            "cls": ",".join(sorted(info["cls"])),
            "split": ",".join(sorted(info["split"])),
            "n_total": len(info["paths"]),
            "n_sampled": len(paths),
            "brightness_mean": float(np.mean(means)),
            "brightness_std": float(np.std(means)),
        })

    normal_rows = [r for r in rows if r["cls"] == "normal"]
    anomaly_rows = [r for r in rows if r["cls"] == "anomaly"]

    print("\n" + "=" * 88)
    print("  location별 평균 밝기 (전체, 밝기 오름차순)")
    print("=" * 88)
    print(f"  {'location':30s} {'class':8s} {'split':12s} {'n':>6s} {'brightness_mean':>16s}")
    for r in sorted(rows, key=lambda x: x["brightness_mean"]):
        print(f"  {r['location']:30s} {r['cls']:8s} {r['split']:12s} "
              f"{r['n_total']:6d} {r['brightness_mean']:16.2f}")

    def stats(rs, label):
        vals = np.array([r["brightness_mean"] for r in rs])
        print(f"\n[{label}] location {len(rs)}개 — location-평균 밝기의 location간 분포:")
        print(f"  mean={vals.mean():.2f}  std={vals.std():.2f}  "
              f"min={vals.min():.2f}  max={vals.max():.2f}  range={vals.max()-vals.min():.2f}")
        return vals

    print("\n" + "=" * 88)
    print("  normal location들 사이의 밝기 편차 vs normal-vs-anomaly 갭")
    print("=" * 88)
    nv = stats(normal_rows, "normal locations")
    av = stats(anomaly_rows, "anomaly locations")

    gap = nv.mean() - av.mean()
    print(f"\nnormal-location 평균끼리의 산포(std) = {nv.std():.2f}, range = {nv.max()-nv.min():.2f}")
    print(f"normal 전체평균 vs anomaly 전체평균 갭 = {gap:.2f}")
    if nv.std() > gap * 0.3 or (nv.max() - nv.min()) > gap * 0.6:
        print("\n→ normal location들 사이에서도 밝기 편차가 상당히 큼(정상-이상 갭의 상당 비율).")
        print("  즉 brightness가 '이상 여부'만이 아니라 'location(촬영 지점) 정체성' 자체에")
        print("  크게 좌우된다는 뜻 — location-confound 가설을 강하게 지지.")
    else:
        print("\n→ normal location들끼리는 밝기가 비교적 균일하고, anomaly와의 갭이 훨씬 큼.")
        print("  location 정체성만으로는 밝기 차이가 다 설명되지 않음 — 이상 자체와 연관된")
        print("  신호가 남아있을 가능성.")
    print("=" * 88)


if __name__ == "__main__":
    main()

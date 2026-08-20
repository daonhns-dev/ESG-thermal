"""
hv_motor 전체 프레임 밝기/엣지 baseline이 거의 무작위(AUC 0.39~0.61)로 나온 뒤,
"신호가 국소적(bounding box 내부)이라 전체 평균에 묻히는 것 아닌가"를 검증.

JSON 라벨의 bounding_box 좌표로 원본 CSV(픽셀별 실측 온도)에서 박스 내부
mean/max 온도를 직접 계산해 normal/danger AUC를 비교. 전체 프레임 통계보다
훨씬 높은 AUC가 나오면 "국소 위치를 알아야 신호가 보인다"는 뜻 — 지도학습
(객체검출+상태분류, §8-8 원래 권장 경로)이 정공법이라는 근거가 됨.

사용법:
  python scripts/experiments/hv_motor_bbox_temp_auc.py \
      --csv_dir data/hv_motor_raw/csv --labels_dir data/hv_motor_raw/labels
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.metrics import roc_auc_score

N_COLS = 640


def load_thermal_csv(csv_path: Path) -> np.ndarray:
    return np.loadtxt(csv_path, delimiter=";", skiprows=5, usecols=range(N_COLS))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv_dir", default="data/hv_motor_raw/csv")
    ap.add_argument("--labels_dir", default="data/hv_motor_raw/labels")
    ap.add_argument("--max_per_class", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    csv_dir, labels_dir = Path(args.csv_dir), Path(args.labels_dir)
    label_files = sorted(labels_dir.glob("*.json"))

    normal_recs, danger_recs = [], []
    for jf in label_files:
        with open(jf, encoding="utf-8") as f:
            d = json.load(f)
        status = d.get("metadata", {}).get("status", "")
        status = "danger" if "danger" in status else ("normal" if "normal" in status else None)
        anns = d.get("annotations", [])
        if status is None or not anns:
            continue
        cf = csv_dir / f"{jf.stem}.csv"
        if not cf.exists():
            continue
        rec = (jf.stem, cf, anns)
        (danger_recs if status == "danger" else normal_recs).append(rec)

    rng = np.random.default_rng(args.seed)
    if args.max_per_class and len(normal_recs) > args.max_per_class:
        idx = rng.choice(len(normal_recs), args.max_per_class, replace=False)
        normal_recs = [normal_recs[i] for i in idx]
    if args.max_per_class and len(danger_recs) > args.max_per_class:
        idx = rng.choice(len(danger_recs), args.max_per_class, replace=False)
        danger_recs = [danger_recs[i] for i in idx]

    print(f"normal {len(normal_recs)}개, danger {len(danger_recs)}개 (bbox 라벨 보유분)")

    def extract_stats(recs, tag):
        bbox_mean, bbox_max, frame_mean, bg_mean = [], [], [], []
        for i, (stem, cf, anns) in enumerate(recs):
            arr = load_thermal_csv(cf)
            h, w = arr.shape
            mask = np.zeros((h, w), dtype=bool)
            for ann in anns:
                data = ann.get("data", {})
                x, y = int(data.get("x", 0)), int(data.get("y", 0))
                bw, bh = int(data.get("width", 0)), int(data.get("height", 0))
                x0, y0 = max(0, x), max(0, y)
                x1, y1 = min(w, x + bw), min(h, y + bh)
                if x1 > x0 and y1 > y0:
                    mask[y0:y1, x0:x1] = True
            if not mask.any():
                continue
            bbox_vals = arr[mask]
            bg_vals = arr[~mask]
            bbox_mean.append(float(bbox_vals.mean()))
            bbox_max.append(float(bbox_vals.max()))
            frame_mean.append(float(arr.mean()))
            bg_mean.append(float(bg_vals.mean()) if bg_vals.size else float("nan"))
            if (i + 1) % 2000 == 0:
                print(f"  [{tag}] {i+1}/{len(recs)} 처리 중...")
        return map(np.array, (bbox_mean, bbox_max, frame_mean, bg_mean))

    n_bbox_mean, n_bbox_max, n_frame_mean, n_bg_mean = extract_stats(normal_recs, "normal")
    d_bbox_mean, d_bbox_max, d_frame_mean, d_bg_mean = extract_stats(danger_recs, "danger")

    labels = np.array([0] * len(n_bbox_mean) + [1] * len(d_bbox_mean))

    def auc_report(name, n_vals, d_vals):
        vals = np.concatenate([n_vals, d_vals])
        auc = roc_auc_score(labels, vals)
        print(f"  {name:35s} AUC = {auc:.4f}   normal mean={n_vals.mean():.2f}  danger mean={d_vals.mean():.2f}")

    print("\n" + "=" * 78)
    print("  bbox(설비 위치) 내부 온도 vs 전체 프레임 온도 — 판별력 비교")
    print("=" * 78)
    auc_report("bbox 내부 평균온도(bbox_mean)", n_bbox_mean, d_bbox_mean)
    auc_report("bbox 내부 최고온(bbox_max)", n_bbox_max, d_bbox_max)
    auc_report("bbox - 배경 온도차(bbox_mean - bg_mean)", n_bbox_mean - n_bg_mean, d_bbox_mean - d_bg_mean)
    auc_report("전체 프레임 평균(frame_mean, 참고용)", n_frame_mean, d_frame_mean)
    print("=" * 78)


if __name__ == "__main__":
    main()

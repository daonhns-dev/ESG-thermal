"""
hv_motor_fixed_scale(CSV 고정 스케일 재렌더링, §8-17/§8-18) 데이터에서
brightness/edge 단독 baseline AUC 확인 — aircon(brightness_baseline_auc.py)과
동일한 방법으로, "구조/엣지 confound로 다시 빠지는지" 정량 확인.

사용법:
  python scripts/experiments/hv_motor_baseline_auc.py --data_dir data/hv_motor_fixed_scale
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image
from sklearn.metrics import roc_auc_score


def edge_magnitude(gray: np.ndarray) -> np.ndarray:
    gx, gy = np.gradient(gray.astype(np.float64))
    return np.sqrt(gx ** 2 + gy ** 2)


def load_gray(path: Path, size: int) -> np.ndarray:
    return np.array(Image.open(path).convert("L").resize((size, size), Image.BILINEAR))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data_dir", default="data/hv_motor_fixed_scale")
    ap.add_argument("--size", type=int, default=256)
    ap.add_argument("--max_per_class", type=int, default=4000, help="속도를 위한 클래스당 최대 샘플 수 (0이면 전체)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    root = Path(args.data_dir)
    exts = {".png", ".jpg", ".jpeg"}
    normal = [p for p in (root / "normal").rglob("*") if p.suffix.lower() in exts]
    anomaly = [p for p in (root / "danger").rglob("*") if p.suffix.lower() in exts]

    rng = np.random.default_rng(args.seed)
    if args.max_per_class and len(normal) > args.max_per_class:
        normal = list(rng.choice(normal, args.max_per_class, replace=False))
    if args.max_per_class and len(anomaly) > args.max_per_class:
        anomaly = list(rng.choice(anomaly, args.max_per_class, replace=False))

    print(f"정상(normal) {len(normal)}장, 이상(danger) {len(anomaly)}장")

    paths = normal + anomaly
    labels = np.array([0] * len(normal) + [1] * len(anomaly))

    bmean, bstd, emean = [], [], []
    for i, p in enumerate(paths):
        g = load_gray(p, args.size)
        bmean.append(float(g.mean()))
        bstd.append(float(g.std()))
        emean.append(float(edge_magnitude(g).mean()))
        if (i + 1) % 3000 == 0:
            print(f"  {i+1}/{len(paths)} 처리 중...")
    bmean, bstd, emean = np.array(bmean), np.array(bstd), np.array(emean)

    print("\n" + "=" * 60)
    print("  단순 픽셀 통계 단독 AUC (학습 없음)")
    print("=" * 60)
    for name, score in [
        ("brightness_mean (score=+brightness, 위험이 더 뜨거울 것으로 가정)", bmean),
        ("brightness_mean (score=-brightness, aircon과 동일 방향)", -bmean),
        ("brightness_std (score=+std)", bstd),
        ("edge_mean (score=+edge)", emean),
        ("edge_mean (score=-edge)", -emean),
    ]:
        auc = roc_auc_score(labels, score)
        print(f"  {name:55s} AUC = {auc:.4f}")
    print("=" * 60)


if __name__ == "__main__":
    main()

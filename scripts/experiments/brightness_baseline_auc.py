"""
'밝기/구조 복잡도 단독'만으로 이상탐지가 되는지 빠른 baseline 확인.

§8-11에서 확인한 confound(정상이 이상보다 밝고 구조가 복잡)가 얼마나 강한 판별
신호인지, 학습 없이 픽셀 통계(brightness mean/std, edge magnitude) 단독 AUC로 확인.
GPU/모델 불필요.

사용법:
  python scripts/experiments/brightness_baseline_auc.py --data_dir data/AIR_thermal/test
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
    ap.add_argument("--data_dir", default="data/AIR_thermal/test")
    ap.add_argument("--size", type=int, default=256)
    args = ap.parse_args()

    root = Path(args.data_dir)
    exts = {".png", ".jpg", ".jpeg"}
    normal = [p for p in (root / "normal").rglob("*") if p.suffix.lower() in exts]
    anomaly = [p for p in (root / "anomaly").rglob("*") if p.suffix.lower() in exts]
    print(f"정상 {len(normal)}장, 이상 {len(anomaly)}장")

    paths = normal + anomaly
    labels = np.array([0] * len(normal) + [1] * len(anomaly))

    bmean, bstd, emean = [], [], []
    for p in paths:
        g = load_gray(p, args.size)
        bmean.append(float(g.mean()))
        bstd.append(float(g.std()))
        emean.append(float(edge_magnitude(g).mean()))
    bmean, bstd, emean = np.array(bmean), np.array(bstd), np.array(emean)

    features = {
        "brightness_mean (score=-brightness)": -bmean,
        "brightness_std (score=-std)": -bstd,
        "edge_mean (score=-edge)": -emean,
        "brightness_mean + edge_mean 결합(표준화 평균)": (
            -(bmean - bmean.mean()) / bmean.std() - (emean - emean.mean()) / emean.std()
        ),
    }

    print("\n" + "=" * 60)
    print("  단순 픽셀 통계 단독 AUC (학습 없음)")
    print("=" * 60)
    for name, score in features.items():
        auc = roc_auc_score(labels, score)
        print(f"  {name:45s} AUC = {auc:.4f}")
    print("=" * 60)
    print("  참고: AE(재구성 기반) AUC = 0.0586 (역전)")


if __name__ == "__main__":
    main()

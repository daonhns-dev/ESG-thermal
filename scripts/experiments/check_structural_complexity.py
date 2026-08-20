"""
정상/이상 이미지의 '구조적 복잡도(edge magnitude)'를 비교 — AE AUC 역전(0.06) 원인 진단.

가설: 재구성 오차가 온도가 아니라 구조(edge)를 따라간다는 §8-7 결론이 AE에서도 재발했고,
      이 데이터셋에서는 우연히 '정상'이 '이상'보다 구조적으로 더 복잡해서(배경 파이프/케이블/
      벽 vs 단순한 화재 덩어리) 재구성 오차 순위가 통째로 뒤집혔다는 것.

GPU/모델 불필요 — 순수 이미지 전처리만 사용, 수 초~수십 초면 끝남.

사용법:
  python scripts/experiments/check_structural_complexity.py --data_dir data/AIR_thermal/test
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image


def edge_magnitude(gray: np.ndarray) -> np.ndarray:
    gx, gy = np.gradient(gray.astype(np.float64))
    return np.sqrt(gx ** 2 + gy ** 2)


def load_gray(path: Path, size: int = 256) -> np.ndarray:
    return np.array(Image.open(path).convert("L").resize((size, size), Image.BILINEAR))


def stats_for(paths: list[Path], size: int) -> dict:
    edge_means, brightness_means, brightness_stds = [], [], []
    for p in paths:
        g = load_gray(p, size)
        e = edge_magnitude(g)
        edge_means.append(float(e.mean()))
        brightness_means.append(float(g.mean()))
        brightness_stds.append(float(g.std()))
    return {
        "edge_mean": np.array(edge_means),
        "brightness_mean": np.array(brightness_means),
        "brightness_std": np.array(brightness_stds),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data_dir", default="data/AIR_thermal/test", help="normal/, anomaly/ 하위폴더 포함 경로")
    ap.add_argument("--size", type=int, default=256)
    args = ap.parse_args()

    root = Path(args.data_dir)
    exts = {".png", ".jpg", ".jpeg"}
    normal = [p for p in (root / "normal").rglob("*") if p.suffix.lower() in exts]
    anomaly = [p for p in (root / "anomaly").rglob("*") if p.suffix.lower() in exts]
    print(f"정상 {len(normal)}장, 이상 {len(anomaly)}장 분석 중...")

    sn = stats_for(normal, args.size)
    sa = stats_for(anomaly, args.size)

    def report(name, sn_v, sa_v):
        print(f"\n  {name}")
        print(f"    정상: mean={sn_v.mean():.4f}  median={np.median(sn_v):.4f}  std={sn_v.std():.4f}")
        print(f"    이상: mean={sa_v.mean():.4f}  median={np.median(sa_v):.4f}  std={sa_v.std():.4f}")
        diff = sn_v.mean() - sa_v.mean()
        print(f"    차이(정상-이상): {diff:+.4f}  ({'정상이 더 큼' if diff > 0 else '이상이 더 큼'})")

    print("\n" + "=" * 60)
    print("  구조 복잡도(edge) / 밝기 비교 — 정상 vs 이상")
    print("=" * 60)
    report("edge magnitude (구조 복잡도, 클수록 복잡)", sn["edge_mean"], sa["edge_mean"])
    report("brightness mean (전체 밝기)", sn["brightness_mean"], sa["brightness_mean"])
    report("brightness std (명암 대비/hotspot 존재감)", sn["brightness_std"], sa["brightness_std"])

    print("\n" + "=" * 60)
    print("판정:")
    if sn["edge_mean"].mean() > sa["edge_mean"].mean():
        ratio = sn["edge_mean"].mean() / max(sa["edge_mean"].mean(), 1e-8)
        print(f"  → 정상이 이상보다 구조적으로 {ratio:.2f}배 더 복잡함.")
        print("    AE/EfficientAD 재구성 오차가 '온도'가 아니라 '구조 복잡도'를 따라간다면,")
        print("    이게 AUC 역전(0.06)의 원인일 가능성이 높음 (§8-7 edge-following 재확인).")
    else:
        print("  → 이상이 정상보다 구조적으로 복잡함. 구조 복잡도 역전 가설은 기각 — 다른 원인 조사 필요")
        print("    (예: 정규화/threshold 방향 버그, score_mode 설정 등 코드 레벨 확인).")
    print("=" * 60)


if __name__ == "__main__":
    main()

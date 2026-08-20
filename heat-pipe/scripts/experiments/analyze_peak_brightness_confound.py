"""
brightness_mean baseline(§8-12, AUC 1.0/0.96)의 정체 판별:
  physical auto-scale artifact(전이 가능) vs. 촬영조건(조명/노출) artifact(전이 불가)

핵심 아이디어:
  - auto-scale(카메라가 프레임마다 자체 min~max를 팔레트 전체로 재매핑) 때문이라면,
    이상 이미지도 "가장 밝은 픽셀(hotspot)"은 정상 수준으로 밝아야 한다.
    (전체는 어두워져도 hotspot 자체는 팔레트 최댓값 근처로 눌려 올라감)
  - 촬영조건(조명/노출) 문제라면, 이상 이미지는 가장 밝은 픽셀조차 전체적으로 어두울 것이다.

따라서 mean brightness뿐 아니라 peak(top-percentile) brightness를 그룹별로 비교하고,
"peak/mean 비율"(hotspot이 나머지 대비 얼마나 튀는지)도 함께 본다.

사용법:
  python scripts/experiments/analyze_peak_brightness_confound.py --data_dir data/AIR_thermal/test
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image
from sklearn.metrics import roc_auc_score


def load_gray(path: Path, size: int) -> np.ndarray:
    return np.array(Image.open(path).convert("L").resize((size, size), Image.BILINEAR))


def summarize(name: str, values: np.ndarray):
    print(f"  {name:28s} mean={values.mean():7.2f}  std={values.std():6.2f}  "
          f"min={values.min():6.2f}  max={values.max():6.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data_dir", default="data/AIR_thermal/test")
    ap.add_argument("--size", type=int, default=256)
    ap.add_argument("--top_pct", type=float, default=1.0, help="peak brightness로 볼 상위 percentile (기본 top 1%%)")
    args = ap.parse_args()

    root = Path(args.data_dir)
    exts = {".png", ".jpg", ".jpeg"}
    normal = [p for p in (root / "normal").rglob("*") if p.suffix.lower() in exts]
    anomaly = [p for p in (root / "anomaly").rglob("*") if p.suffix.lower() in exts]
    print(f"정상 {len(normal)}장, 이상 {len(anomaly)}장")

    paths = normal + anomaly
    labels = np.array([0] * len(normal) + [1] * len(anomaly))

    bmean, bmax, bp99, ratio = [], [], [], []
    pct = 100.0 - args.top_pct
    for p in paths:
        g = load_gray(p, args.size).astype(np.float64)
        m = float(g.mean())
        mx = float(g.max())
        p99 = float(np.percentile(g, pct))
        bmean.append(m)
        bmax.append(mx)
        bp99.append(p99)
        ratio.append(p99 / max(m, 1e-6))
    bmean, bmax, bp99, ratio = map(np.array, (bmean, bmax, bp99, ratio))

    is_normal = labels == 0
    is_anomaly = labels == 1

    print("\n" + "=" * 70)
    print("  그룹별 밝기 통계 (정상 vs 이상)")
    print("=" * 70)
    print("[정상]")
    summarize("brightness_mean", bmean[is_normal])
    summarize("brightness_max", bmax[is_normal])
    summarize(f"brightness_top{args.top_pct}pct", bp99[is_normal])
    summarize("peak/mean ratio", ratio[is_normal])
    print("[이상]")
    summarize("brightness_mean", bmean[is_anomaly])
    summarize("brightness_max", bmax[is_anomaly])
    summarize(f"brightness_top{args.top_pct}pct", bp99[is_anomaly])
    summarize("peak/mean ratio", ratio[is_anomaly])

    print("\n" + "=" * 70)
    print("  판별 신호로서의 AUC (참고용, 0=정상/1=이상, score=-feature)")
    print("=" * 70)
    for name, feat in [("brightness_mean", bmean), ("brightness_max", bmax), (f"brightness_top{args.top_pct}pct", bp99), ("peak/mean ratio", -ratio)]:
        auc = roc_auc_score(labels, -feat)
        print(f"  {name:28s} AUC = {auc:.4f}")

    print("\n" + "=" * 70)
    print("  해석")
    print("=" * 70)
    mean_drop = 1 - bmean[is_anomaly].mean() / bmean[is_normal].mean()
    peak_drop = 1 - bp99[is_anomaly].mean() / bp99[is_normal].mean()
    print(f"  mean brightness 하락률   : {mean_drop*100:5.1f}%  (정상→이상)")
    print(f"  peak(top{args.top_pct}%) brightness 하락률: {peak_drop*100:5.1f}%  (정상→이상)")
    print(f"  ratio(peak/mean) 변화    : 정상 {ratio[is_normal].mean():.3f} → 이상 {ratio[is_anomaly].mean():.3f}")
    print()
    if peak_drop < mean_drop * 0.5:
        print("  → peak brightness는 mean 대비 훨씬 덜 떨어짐 = hotspot은 정상 수준으로 밝음을 유지.")
        print("    auto-scale 가설 지지: 전체가 어두워져도 hotspot 자체는 압축되어 위로 눌려 올라감.")
        print("    (물리적 신호일 가능성 → NCC 실제 데이터로 전이 가능성 있음)")
    elif peak_drop > mean_drop * 0.8:
        print("  → peak brightness도 mean과 비슷한 비율로 함께 떨어짐 = 가장 밝은 픽셀조차 전체적으로 어두움.")
        print("    촬영조건(조명/노출) artifact 가설 지지: 프레임 전체가 균일하게 눌린 것에 가까움.")
        print("    (비물리적 shortcut일 가능성 → NCC 전이 어려움)")
    else:
        print("  → 중간 정도: 두 가설이 부분적으로 혼재되어 있을 가능성. 추가 정성 확인(이미지 육안) 필요.")
    print("=" * 70)


if __name__ == "__main__":
    main()

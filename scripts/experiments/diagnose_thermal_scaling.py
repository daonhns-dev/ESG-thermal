"""
열화상 렌더 이미지의 '절대 온도 보존 여부' 진단.

질문: grayscale 이미지의 밝기가 절대 온도를 인코딩하는가(고정 레인지),
      아니면 프레임마다 팔레트가 auto-scaling 되어 상대 온도만 남았는가?

방법 (이미지-CSV 쌍 사용, 픽셀 정렬 불필요한 지표 중심):
  각 이미지에 대해
    - 밝기 범위 (1/99 퍼센타일):  int_lo, int_hi
    - 실측 온도 범위 (CSV, 1/99): temp_lo, temp_hi
    - 밝기 1단계당 온도:  degC_per_level = (temp_hi - temp_lo) / (int_hi - int_lo)
  이 값들이 이미지마다
    - 거의 일정 → 고정 레인지 (절대 온도가 이미지에 보존됨)
    - 크게 변동 → auto-scale (절대 온도 소실, 상대 온도만)
  보조 지표: 픽셀 단위 밝기↔온도 상관(밝기가 (상대)온도를 인코딩하는지 확인).

사용법:
  python scripts/diagnose_thermal_scaling.py --config configs/config_efficientad.yaml --n 60
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datasets.dataset import ThermalImageDataset, load_thermal_csv  # noqa: E402
from scripts.validate_efficientad_csv import _resize_temp, resolve_csv_path  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/config_efficientad.yaml")
    ap.add_argument("--n", type=int, default=60, help="분석할 이미지-CSV 쌍 수")
    ap.add_argument("--size", type=int, default=256)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    dcfg = cfg["data"]
    test_dir = dcfg.get("test_dir", "data/test")
    data_root = Path(test_dir).parent
    csv_root = data_root / "csv"

    ds = ThermalImageDataset(root_dir=test_dir, transform=None, is_train=False)
    rng = np.random.default_rng(args.seed)
    idxs = rng.choice(len(ds.image_paths), size=min(args.n, len(ds.image_paths)), replace=False)

    rows = []
    for i in idxs:
        img_path = ds.image_paths[i]
        csv_path = resolve_csv_path(img_path, data_root, csv_root)
        if csv_path is None:
            continue
        temp = load_thermal_csv(csv_path)
        if temp.size == 0:
            continue
        temp_256 = _resize_temp(temp, size=args.size)                      # °C
        gray = np.array(Image.open(img_path).convert("L").resize((args.size, args.size), Image.BILINEAR)).astype(np.float32)

        int_lo, int_hi = np.percentile(gray, [1, 99])
        temp_lo, temp_hi = np.percentile(temp_256, [1, 99])
        if int_hi - int_lo < 1e-3:
            continue
        degc_per_level = (temp_hi - temp_lo) / (int_hi - int_lo)
        corr = float(np.corrcoef(gray.ravel(), temp_256.ravel())[0, 1])
        rows.append({
            "int_lo": int_lo, "int_hi": int_hi,
            "temp_lo": temp_lo, "temp_hi": temp_hi,
            "degc_per_level": degc_per_level, "corr": corr,
        })

    n = len(rows)
    if n == 0:
        print("매칭된 이미지-CSV 쌍이 없습니다.")
        return

    def col(k):
        return np.array([r[k] for r in rows])

    dpl = col("degc_per_level")
    cv = float(dpl.std() / (dpl.mean() + 1e-8))  
    print(f"\n분석 쌍: {n}")
    print("=" * 62)
    print("  [밝기 1단계당 온도]  이미지 간 일정 여부")
    print(f"    degC/level  평균 {dpl.mean():.4f}  std {dpl.std():.4f}  변동계수(CV) {cv:.3f}")
    print(f"    범위        min {dpl.min():.4f}  max {dpl.max():.4f}  (max/min = {dpl.max()/max(dpl.min(),1e-8):.1f}배)")
    print("-" * 62)
    print("  [온도 범위]  이미지마다 다른가")
    print(f"    temp_lo  평균 {col('temp_lo').mean():.1f}°C  std {col('temp_lo').std():.1f}  (범위 {col('temp_lo').min():.1f}~{col('temp_lo').max():.1f})")
    print(f"    temp_hi  평균 {col('temp_hi').mean():.1f}°C  std {col('temp_hi').std():.1f}  (범위 {col('temp_hi').min():.1f}~{col('temp_hi').max():.1f})")
    print("-" * 62)
    print("  [밝기 범위]  auto-scale이면 매 이미지가 거의 full-range 사용")
    print(f"    int_lo   평균 {col('int_lo').mean():.1f}  std {col('int_lo').std():.1f}")
    print(f"    int_hi   평균 {col('int_hi').mean():.1f}  std {col('int_hi').std():.1f}")
    print("-" * 62)
    print(f"  [픽셀 밝기↔온도 상관]  평균 {col('corr').mean():.3f}  (높을수록 밝기가 (상대)온도 인코딩)")
    print("=" * 62)

    # 판정 가이드
    print("\n판정:")
    if cv > 0.25:
        print(f"  → degC/level 변동계수 {cv:.2f} 로 큼 + 온도범위가 이미지마다 상이")
        print("     ⇒ AUTO-SCALE 가능성 높음: 밝기는 '프레임 내 상대 온도'만 인코딩,")
        print("        절대 °C는 렌더 이미지에서 소실됨. (이미지 모델은 절대온도 사용 불가)")
    else:
        print(f"  → degC/level 변동계수 {cv:.2f} 로 작음")
        print("     ⇒ FIXED-RANGE 가능성: 밝기가 절대 온도를 어느정도 일관되게 인코딩.")
    print("  (보조: 픽셀 상관이 높으면 밝기가 온도를 인코딩은 하되, 절대/상대 여부는 위 CV로 판단)")


if __name__ == "__main__":
    main()

"""
모델 이상맵이 '온도'가 아니라 'edge/밝기'에 반응하는지 확진.

각 test 이미지에 대해 EfficientAD combined 이상맵과 다음 3가지의 픽셀 단위
Pearson 상관을 계산해 평균을 비교한다:
  - temp_hotspot : CSV 실측 온도의 중앙값 대비 양의 편차 (온도 신호)
  - edge         : 입력 grayscale의 gradient magnitude (구조적 엣지)
  - luminance    : 입력 grayscale 밝기

edge/luminance 상관 >> temp 상관 이면, 모델이 온도가 아니라 구조/밝기에 반응한다는
어제의 가설이 확진된다.

사용법:
  python scripts/analyze_edge_luminance_corr.py --config configs/config_efficientad.yaml \
    --checkpoint results/checkpoints/efficientad/efficientad.pth \
    --max_per_class 2000 --agg topk_mean --topk_ratio 0.15 --alpha 0.3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import torch
import yaml
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datasets.dataset import load_thermal_csv  # noqa: E402
from scripts.inference_efficientad import _build_test_loader, _load_model, run_inference  # noqa: E402
from scripts.validate_efficientad_csv import _resize_temp, _spatial_correlation, _temp_hotspot_map, resolve_csv_path  # noqa: E402


def edge_magnitude(gray: np.ndarray) -> np.ndarray:
    gx, gy = np.gradient(gray.astype(np.float64))
    return np.sqrt(gx ** 2 + gy ** 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/config_efficientad.yaml")
    ap.add_argument("--checkpoint", default=None)
    ap.add_argument("--max_per_class", type=int, default=2000)
    ap.add_argument("--alpha", type=float, default=0.3)
    ap.add_argument("--agg", type=str, default="topk_mean")
    ap.add_argument("--topk_ratio", type=float, default=0.15)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault("inference", {})["max_per_class"] = args.max_per_class
    if args.checkpoint:
        cfg["inference"]["checkpoint"] = args.checkpoint
    device = torch.device("cuda" if cfg.get("device") == "cuda" and torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    model = _load_model(cfg, device)
    model.set_score_params(alpha=args.alpha, agg=args.agg, topk_ratio=args.topk_ratio)

    data_root = Path(cfg["data"]["test_dir"]).parent
    csv_root = data_root / "csv"

    loader = _build_test_loader(cfg)
    scores, _, _, labels, paths, map_list = run_inference(model, loader, device, store_maps=True)
    labels = np.array(labels)

    c_temp, c_edge, c_lum = [], [], []
    lab_keep = []
    for i, path in enumerate(paths):
        amap = map_list[i]["combined_map"]           
        H, W = amap.shape
        gray = np.array(Image.open(path).convert("L").resize((W, H), Image.BILINEAR)).astype(np.float32)
        edge = edge_magnitude(gray)

        csv_path = resolve_csv_path(path, data_root, csv_root)
        temp_hot = None
        if csv_path is not None:
            temp = load_thermal_csv(csv_path)
            if temp.size:
                temp_hot = _temp_hotspot_map(_resize_temp(temp, size=H))
        if temp_hot is None:
            continue

        c_temp.append(_spatial_correlation(amap, temp_hot))
        c_edge.append(_spatial_correlation(amap, edge))
        c_lum.append(_spatial_correlation(amap, gray))
        lab_keep.append(int(labels[i]))

    c_temp = np.array(c_temp); c_edge = np.array(c_edge); c_lum = np.array(c_lum)
    lab_keep = np.array(lab_keep)

    def stat(a):
        return f"mean={np.nanmean(a):+.3f}  median={np.nanmedian(a):+.3f}  |mean|={np.nanmean(np.abs(a)):.3f}"

    print(f"\n분석 표본: {len(c_temp)}")
    print("=" * 64)
    print("  이상맵 vs 각 신호의 픽셀 상관 (전체 이미지 평균)")
    print("  " + "-" * 60)
    print(f"  temp_hotspot : {stat(c_temp)}")
    print(f"  edge (grad)  : {stat(c_edge)}")
    print(f"  luminance    : {stat(c_lum)}")
    print("=" * 64)

    # anomaly만
    for name, sel in [("anomaly", lab_keep == 1), ("normal", lab_keep == 0)]:
        if sel.any():
            print(f"  [{name}] temp={np.nanmean(c_temp[sel]):+.3f}  "
                  f"edge={np.nanmean(c_edge[sel]):+.3f}  lum={np.nanmean(c_lum[sel]):+.3f}")

    print("\n판정:")
    mt, me, ml = np.nanmean(np.abs(c_temp)), np.nanmean(np.abs(c_edge)), np.nanmean(np.abs(c_lum))
    driver = max([("temp", mt), ("edge", me), ("luminance", ml)], key=lambda x: x[1])
    print(f"  |상관| 최대 신호 = '{driver[0]}' ({driver[1]:.3f})")
    if me > mt or ml > mt:
        print("  → edge/luminance 상관이 temp보다 큼: 모델이 온도보다 구조/밝기에 반응 (가설 확진)")
    else:
        print("  → temp 상관이 가장 큼: 온도 주도")


if __name__ == "__main__":
    main()

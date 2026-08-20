"""
ROI 내부의 고-이상 픽셀이 '고온'을 잡나 '엣지(온도 전이)'를 잡나 정량 확인.

가설: ROI를 온도(밝기) 기반 hot 영역으로 잡아도, EfficientAD 이상점수는 feature
      재구성 오차라서 ROI '내부'에서도 균일한 고온부가 아니라 엣지/전이 지점을 짚는다.
      → 이상맵이 온도(hot)가 아니라 edge를 따라간다면, "구조 기반 모델로는 열 이상
        국소화 불가"가 확정됨.

방법 (이상 이미지, ROI 내부 픽셀만):
  - corr(이상맵, 온도)  vs  corr(이상맵, edge)  — ROI 내부 픽셀에 한정한 Pearson
  - 상위 topk% 이상 픽셀(=모델이 '이상'이라 표시한 지점)의:
      · 온도 percentile (ROI 온도분포 기준): 50이면 평범한 온도, >70이면 고온
      · edge percentile (ROI edge분포 기준): >70이면 엣지 위

사용법:
  python scripts/analyze_roi_hot_vs_edge.py --config configs/config_efficientad.yaml \
    --checkpoint results/checkpoints/efficientad/efficientad_roi.pth --recalib \
    --roi-k 0.3 --max_per_class 2000
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import torch
import yaml
from PIL import Image
from torch.utils.data import DataLoader, Subset

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datasets.dataset import load_thermal_csv  # noqa: E402
from scripts.inference_efficientad import EfficientADTestDataset, _build_test_loader, _get_test_transform, _load_model, run_inference  # noqa: E402
from scripts.experiments.eval_efficientad_roi import roi_recalibrate  # noqa: E402
from scripts.train_efficientad import _brightness_roi  # noqa: E402
from scripts.validate_efficientad_csv import _resize_temp, resolve_csv_path  # noqa: E402


def edge_magnitude(gray):
    gx, gy = np.gradient(gray.astype(np.float64))
    return np.sqrt(gx ** 2 + gy ** 2)


def corr(a, b):
    a = a.astype(np.float64); b = b.astype(np.float64)
    if a.std() < 1e-8 or b.std() < 1e-8:
        return np.nan
    return float(np.corrcoef(a, b)[0, 1])


def pctile_of(vals, x):
    return float((vals < x).mean() * 100.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/config_efficientad.yaml")
    ap.add_argument("--checkpoint", default=None)
    ap.add_argument("--recalib", action="store_true", help="ROI 픽셀로 분위수 재캘리브레이션")
    ap.add_argument("--roi-k", type=float, default=0.3)
    ap.add_argument("--topk", type=float, default=0.15, help="상위 이상 픽셀 비율")
    ap.add_argument("--alpha", type=float, default=0.3)
    ap.add_argument("--max_per_class", type=int, default=2000)
    ap.add_argument("--calib_n", type=int, default=500)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault("inference", {})["max_per_class"] = args.max_per_class
    if args.checkpoint:
        cfg["inference"]["checkpoint"] = args.checkpoint
    device = torch.device("cuda" if cfg.get("device") == "cuda" and torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    model = _load_model(cfg, device)
    model.set_score_params(alpha=args.alpha, agg="topk_mean", topk_ratio=args.topk)

    if args.recalib:
        dcfg = cfg["data"]
        calib_ds = EfficientADTestDataset(root_dir=dcfg.get("train_dir", "data/train"), transform=_get_test_transform(int(dcfg.get("image_size", 256))), is_train=True)
        rng = np.random.default_rng(args.seed)
        cidx = rng.choice(len(calib_ds), size=min(args.calib_n, len(calib_ds)), replace=False)
        calib_loader = DataLoader(Subset(calib_ds, cidx.tolist()), batch_size=8, shuffle=False)
        ncfg = cfg.get("normalization", {})
        roi_recalibrate(model, calib_loader, device, args.roi_k, float(ncfg.get("q_a", 0.9)), float(ncfg.get("q_b", 0.995)))

    data_root = Path(cfg["data"]["test_dir"]).parent
    csv_root = data_root / "csv"

    loader = _build_test_loader(cfg)
    scores, _, _, labels, paths, map_list = run_inference(model, loader, device, store_maps=True)
    labels = np.array(labels)

    cAT, cAE = [], []          # ROI 내부 corr(anomaly,temp), corr(anomaly,edge)
    tp_temp, tp_edge = [], []  # 상위 이상픽셀의 온도/엣지 percentile
    for i, path in enumerate(paths):
        if int(labels[i]) != 1:      # 이상 이미지만
            continue
        csv_path = resolve_csv_path(path, data_root, csv_root)
        if csv_path is None:
            continue
        temp = load_thermal_csv(csv_path)
        if temp.size == 0:
            continue
        A = map_list[i]["combined_map"]
        H, W = A.shape
        gray = np.array(Image.open(path).convert("L").resize((W, H), Image.BILINEAR)).astype(np.float32)
        E = edge_magnitude(gray)
        T = _resize_temp(temp, size=H)
        roi = _brightness_roi(torch.tensor(gray)[None, None], args.roi_k, (H, W))[0, 0].numpy() > 0.5
        if roi.sum() < 50:
            continue

        A_r, T_r, E_r = A[roi], T[roi], E[roi]
        cAT.append(corr(A_r, T_r))
        cAE.append(corr(A_r, E_r))

        # 상위 topk% 이상 픽셀
        k = max(1, int(A_r.size * args.topk))
        top = np.argsort(A_r)[-k:]
        tp_temp.append(pctile_of(T_r, T_r[top].mean()))
        tp_edge.append(pctile_of(E_r, E_r[top].mean()))

    cAT = np.array(cAT); cAE = np.array(cAE)
    tp_temp = np.array(tp_temp); tp_edge = np.array(tp_edge)

    print(f"\n분석(이상 이미지, ROI 내부): {len(cAT)}장")
    print("=" * 60)
    print("  ROI 내부 픽셀 상관 (이상맵 vs)")
    print(f"    온도  : mean={np.nanmean(cAT):+.3f}  |mean|={np.nanmean(np.abs(cAT)):.3f}")
    print(f"    edge  : mean={np.nanmean(cAE):+.3f}  |mean|={np.nanmean(np.abs(cAE)):.3f}")
    print("  " + "-" * 56)
    print("  상위 이상픽셀(모델이 '이상'이라 짚은 곳)의 ROI 내 위치")
    print(f"    온도 percentile : {np.nanmean(tp_temp):.1f}  (50=평범, >70=고온)")
    print(f"    edge percentile : {np.nanmean(tp_edge):.1f}  (>70=엣지 위)")
    print("=" * 60)
    print("판정:")
    if np.nanmean(tp_edge) > np.nanmean(tp_temp) + 10:
        print("  → 고-이상 픽셀이 고온보다 '엣지'에 위치. ROI 내부에서도 edge 추종 확정.")
        print("     구조 기반(재구성 오차) 모델로는 열 이상 국소화 불가 → 온도 기반 정의 필요.")
    elif np.nanmean(tp_temp) > 70:
        print("  → 고-이상 픽셀이 실제 고온에 위치. 온도를 짚고 있음.")
    else:
        print("  → 온도/엣지 어느 쪽도 강하게 주도하지 않음(둘 다 애매).")


if __name__ == "__main__":
    main()

"""
상대 hotspot 이상 정의 (이미지 밝기 기반, 재학습·CSV 불필요).

아이디어: 코킹 = 국소 과열 = "주변 국소 baseline보다 비정상적으로 뜨거운 지점".
  이미지 밝기 ≈ 상대 온도(r≈0.98)이므로, 밝기의 local high-pass로 직접 계산:
      relhot(p) = max(0, gray(p) - GaussianBlur(gray, sigma)(p))
  균일하게 뜨거운 정상 장비는 relhot 낮음, 국소 hotspot만 높음.

EfficientAD(구조/엣지 추종)와 대조:
  - 이미지 레벨 탐지 AUC
  - 상위 이상픽셀의 '온도 percentile' (EfficientAD는 58=평범 → 엣지 추종.
    relhot이 온도를 짚으면 이 값이 높아야 함)와 'edge percentile'

사용법:
  python scripts/relative_hotspot_anomaly.py --config configs/config_efficientad.yaml \
    --sigma 18 --roi-k 0.3 --topk 0.15 --max_per_class 2000 --save_viz 6
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.cm as cm
import matplotlib.pyplot as plt
import numpy as np
import yaml
from PIL import Image
from sklearn.metrics import roc_auc_score

try:
    from scipy.ndimage import gaussian_filter
except Exception:  # scipy 없으면 uniform 근사
    gaussian_filter = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datasets.dataset import ThermalImageDataset, load_thermal_csv  # noqa: E402
from scripts.validate_efficientad_csv import _resize_temp, resolve_csv_path  # noqa: E402


def _blur(x, sigma):
    if gaussian_filter is not None:
        return gaussian_filter(x, sigma=sigma)
    k = int(sigma * 3) | 1
    pad = k // 2
    xp = np.pad(x, pad, mode="reflect")
    out = np.zeros_like(x)
    for i in range(x.shape[0]):
        for j in range(x.shape[1]):
            out[i, j] = xp[i:i + k, j:j + k].mean()
    return out


def relhot_map(gray, sigma):
    """밝기 local high-pass → 양의 국소 hotspot."""
    base = _blur(gray.astype(np.float64), sigma)
    return np.clip(gray - base, 0, None)


def edge_magnitude(gray):
    gx, gy = np.gradient(gray.astype(np.float64))
    return np.sqrt(gx ** 2 + gy ** 2)


def brightness_roi(gray, k):
    m, s = gray.mean(), gray.std()
    return gray > (m + k * s)


def pctile_of(vals, x):
    return float((vals < x).mean() * 100.0)


def topk_mean(vals, ratio):
    n = max(1, int(vals.size * ratio))
    return float(np.sort(vals.ravel())[-n:].mean())


def overlay(gray, amap, a=0.55):
    g = np.stack([gray] * 3, -1); g = (g - g.min()) / (g.max() - g.min() + 1e-8)
    lo, hi = np.percentile(amap, [50, 99.5])
    heat = cm.jet(np.clip((amap - lo) / (hi - lo + 1e-8), 0, 1))[..., :3]
    return (1 - a) * g + a * heat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/config_efficientad.yaml")
    ap.add_argument("--sigma", type=float, default=18.0, help="local baseline 스케일(px@256)")
    ap.add_argument("--roi-k", type=float, default=0.3)
    ap.add_argument("--topk", type=float, default=0.15)
    ap.add_argument("--size", type=int, default=256)
    ap.add_argument("--max_per_class", type=int, default=2000)
    ap.add_argument("--save_viz", type=int, default=6)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    test_dir = cfg["data"].get("test_dir", "data/test")
    data_root = Path(test_dir).parent
    csv_root = data_root / "csv"

    ds = ThermalImageDataset(root_dir=test_dir, transform=None, is_train=False)
    by = {0: [], 1: []}
    for i, lb in enumerate(ds.labels):
        by[lb].append(i)
    rng = np.random.default_rng(args.seed)
    sel = []
    for lb in (0, 1):
        idx = by[lb]
        if len(idx) > args.max_per_class:
            idx = rng.choice(idx, size=args.max_per_class, replace=False).tolist()
        sel.extend(idx)

    scores, labels = [], []
    tp_temp, tp_edge = [], []      
    viz = []
    for i in sel:
        path = ds.image_paths[i]; lab = int(ds.labels[i])
        gray = np.array(Image.open(path).convert("L").resize((args.size, args.size), Image.BILINEAR)).astype(np.float32)
        rh = relhot_map(gray, args.sigma)
        roi = brightness_roi(gray, args.roi_k)
        if roi.sum() < 50:
            roi = np.ones_like(roi)
        score = topk_mean(rh[roi], args.topk)
        scores.append(score); labels.append(lab)

        if lab == 1:
            csv_path = resolve_csv_path(path, data_root, csv_root)
            if csv_path is not None:
                temp = load_thermal_csv(csv_path)
                if temp.size:
                    T = _resize_temp(temp, size=args.size)
                    E = edge_magnitude(gray)
                    rh_r, T_r, E_r = rh[roi], T[roi], E[roi]
                    k = max(1, int(rh_r.size * args.topk))
                    top = np.argsort(rh_r)[-k:]
                    tp_temp.append(pctile_of(T_r, T_r[top].mean()))
                    tp_edge.append(pctile_of(E_r, E_r[top].mean()))
                    if len(viz) < args.save_viz:
                        viz.append((gray, T, rh))

    scores = np.array(scores); labels = np.array(labels)
    auc = roc_auc_score(labels, scores)

    print(f"\n상대 hotspot 이상 정의 (sigma={args.sigma}, roi_k={args.roi_k}, topk={args.topk})")
    print("=" * 60)
    print(f"  표본: {len(labels)} (정상 {int((labels==0).sum())}/이상 {int((labels==1).sum())})")
    print(f"  이미지 레벨 탐지 AUC : {auc:.4f}   (참고 EfficientAD 0.9596)")
    print("  " + "-" * 56)
    print(f"  상위 이상픽셀 온도 percentile : {np.nanmean(tp_temp):.1f}   (EfficientAD 58.7)")
    print(f"  상위 이상픽셀 edge percentile : {np.nanmean(tp_edge):.1f}   (EfficientAD 76.2)")
    print("=" * 60)
    if np.nanmean(tp_temp) > 70:
        print("  → 상위 이상픽셀이 실제 고온에 위치 = 온도 기반 국소화 성공 (엣지 아님)")

    if viz:
        n = len(viz)
        fig, axes = plt.subplots(n, 3, figsize=(12, 3.8 * n))
        if n == 1:
            axes = axes[None, :]
        for r, (gray, T, rh) in enumerate(viz):
            axes[r, 0].imshow(gray, cmap="gray"); axes[r, 0].set_title("Input"); axes[r, 0].axis("off")
            im = axes[r, 1].imshow(T, cmap="inferno"); plt.colorbar(im, ax=axes[r, 1], fraction=0.046)
            axes[r, 1].set_title("CSV Temp (degC)"); axes[r, 1].axis("off")
            axes[r, 2].imshow(overlay(gray, rh)); axes[r, 2].set_title("Relative-hotspot overlay"); axes[r, 2].axis("off")
        fig.tight_layout()
        out_dir = Path(cfg.get("inference", {}).get("output_dir", "results/predictions"))
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "relative_hotspot_overlay.png"
        fig.savefig(out_path, dpi=130); plt.close(fig)
        print(f"오버레이 저장 → {out_path}")


if __name__ == "__main__":
    main()

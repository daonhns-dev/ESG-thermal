"""
이상맵(base / ROI) vs 실측 CSV 온도 비교.

목적: 모델이 반응하는 영역이 실제 이상 영역(및 CSV 온도 구조)과 겹치는지 판단.
      base/ROI 모두 '실제 이상 위치'를 짚는지, 아니면 엉뚱한 곳(밝은 전경 등)에
      반응하는지를 CSV 온도맵과 나란히 놓고 확인.

레이아웃 (이상 샘플별 4열):
  Input(gray) | CSV Temp(°C) | base 이상맵 오버레이 | ROI 이상맵 오버레이

사용법:
  python scripts/viz_roi_vs_base_csv.py --config configs/config_efficientad.yaml \
    --base results/checkpoints/efficientad/efficientad.pth \
    --roi  results/checkpoints/efficientad/efficientad_roi.pth \
    --n 6 --roi-k 0.3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.cm as cm
import matplotlib.pyplot as plt
import numpy as np
import torch
import torch.nn.functional as F
import yaml
from PIL import Image
from torch.utils.data import DataLoader, Subset

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datasets.dataset import load_thermal_csv  # noqa: E402
from scripts.inference_efficientad import EfficientADTestDataset, _build_test_loader, _get_test_transform, _load_model  # noqa: E402
from scripts.experiments.eval_efficientad_roi import roi_recalibrate  # noqa: E402
from scripts.validate_efficientad_csv import _resize_temp, resolve_csv_path  # noqa: E402


def _load(cfg, ckpt, device):
    cfg = dict(cfg); cfg.setdefault("inference", {})
    cfg["inference"] = dict(cfg["inference"]); cfg["inference"]["checkpoint"] = ckpt
    return _load_model(cfg, device)


@torch.no_grad()
def anomaly_map_256(model, images, alpha):
    out = model(images)
    ln = model._quantile_normalize(out["local_map_raw"], model.q_a_st, model.q_b_st)
    gn = model._quantile_normalize(out["global_map_raw"], model.q_a_ae, model.q_b_ae)
    combined = alpha * ln + (1.0 - alpha) * gn
    return F.interpolate(combined, size=(256, 256), mode="bilinear", align_corners=False)[:, 0]


def norm01(m):
    lo, hi = np.percentile(m, [50, 99.5])
    return np.clip((m - lo) / (hi - lo + 1e-8), 0, 1)


def overlay(gray, amap, a=0.55):
    g = np.stack([gray] * 3, -1)
    g = (g - g.min()) / (g.max() - g.min() + 1e-8)
    heat = cm.jet(norm01(amap))[..., :3]
    return (1 - a) * g + a * heat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/config_efficientad.yaml")
    ap.add_argument("--base", required=True)
    ap.add_argument("--roi", required=True)
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--roi-k", type=float, default=0.3)
    ap.add_argument("--alpha", type=float, default=0.3)
    ap.add_argument("--calib_n", type=int, default=500)
    ap.add_argument("--max_per_class", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault("inference", {})["max_per_class"] = args.max_per_class
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    data_root = Path(cfg["data"].get("test_dir", "data/test")).parent
    csv_root = data_root / "csv"

    base_model = _load(cfg, args.base, device)
    roi_model = _load(cfg, args.roi, device)

    dcfg = cfg["data"]
    img_size = int(dcfg.get("image_size", 256))
    calib_ds = EfficientADTestDataset(root_dir=dcfg.get("train_dir", "data/train"), transform=_get_test_transform(img_size), is_train=True)
    rng = np.random.default_rng(args.seed)
    cidx = rng.choice(len(calib_ds), size=min(args.calib_n, len(calib_ds)), replace=False)
    calib_loader = DataLoader(Subset(calib_ds, cidx.tolist()), batch_size=8, shuffle=False)
    ncfg = cfg.get("normalization", {})
    q_a, q_b = float(ncfg.get("q_a", 0.9)), float(ncfg.get("q_b", 0.995))
    print("base 재캘리:"); roi_recalibrate(base_model, calib_loader, device, args.roi_k, q_a, q_b)
    print("roi  재캘리:"); roi_recalibrate(roi_model, calib_loader, device, args.roi_k, q_a, q_b)

    test_loader = _build_test_loader(cfg)
    rows = []
    for images, y, paths in test_loader:
        images = images.to(device)
        bmap = anomaly_map_256(base_model, images, args.alpha).cpu().numpy()
        rmap = anomaly_map_256(roi_model, images, args.alpha).cpu().numpy()
        g = images.mean(1).cpu().numpy()
        for i in range(images.shape[0]):
            if int(y[i]) != 1 or len(rows) >= args.n:
                continue
            csv_path = resolve_csv_path(paths[i], data_root, csv_root)
            temp256 = None
            if csv_path is not None:
                temp = load_thermal_csv(csv_path)
                if temp.size:
                    temp256 = _resize_temp(temp, size=256)
            rows.append((g[i], temp256, bmap[i], rmap[i]))
        if len(rows) >= args.n:
            break

    n = len(rows)
    fig, axes = plt.subplots(n, 4, figsize=(15, 3.7 * n))
    if n == 1:
        axes = axes[None, :]
    for r, (g, temp, bm, rm) in enumerate(rows):
        axes[r, 0].imshow(g, cmap="gray"); axes[r, 0].set_title("Input (anomaly)")
        if temp is not None:
            im = axes[r, 1].imshow(temp, cmap="inferno")
            plt.colorbar(im, ax=axes[r, 1], fraction=0.046)
            axes[r, 1].set_title("CSV Temp (degC)")
        else:
            axes[r, 1].text(0.5, 0.5, "CSV 없음", ha="center", va="center"); axes[r, 1].set_title("CSV Temp")
        axes[r, 2].imshow(overlay(g, bm)); axes[r, 2].set_title("base overlay")
        axes[r, 3].imshow(overlay(g, rm)); axes[r, 3].set_title("ROI-trained overlay")
        for c in range(4):
            axes[r, c].axis("off")
    fig.tight_layout()
    out_dir = Path(cfg.get("inference", {}).get("output_dir", "results/predictions"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "roi_vs_base_vs_csv.png"
    fig.savefig(out_path, dpi=130); plt.close(fig)
    print(f"\n비교 저장 → {out_path}")


if __name__ == "__main__":
    main()

"""
ROI 학습 모델 vs base 모델의 이상맵을 '같은 입력 위 오버레이'로 비교.

목적: 모델이 반응하는 영역이 실제 이미지의 어느 부분인지, 그리고 ROI 학습이
      base 대비 반응 위치를 바꾸는지를 착시 없이 눈으로 판단.

이전 히트맵의 문제:
  - combined 맵은 음수~양수 → ROI 마스크(0/1) 곱하면 배경(0)이 중간색으로 떠서 오해 유발.
  개선:
  - 두 모델을 ROI로 동일 재캘리브레이션 후, 같은 test 샘플에 대해 이상맵을 계산.
  - 맵을 퍼센타일로 [0,1] 정규화(상위 영역 강조) → jet 컬러 → 입력 grayscale 위 오버레이.
  - base와 ROI를 나란히 → 반응 위치 차이 직접 비교.

사용법:
  python scripts/viz_roi_vs_base.py --config configs/config_efficientad.yaml \
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
from torch.utils.data import DataLoader, Subset

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datasets.dataset import ThermalImageDataset  # noqa: E402
from scripts.inference_efficientad import (  # noqa: E402
    EfficientADTestDataset, _build_test_loader, _get_test_transform, _load_model,
)
from scripts.experiments.eval_efficientad_roi import roi_recalibrate  # noqa: E402
from scripts.train_efficientad import _brightness_roi  # noqa: E402

FEAT_HW = (64, 64)


def _load(cfg, ckpt, device):
    cfg = dict(cfg); cfg.setdefault("inference", {})
    cfg["inference"] = dict(cfg["inference"]); cfg["inference"]["checkpoint"] = ckpt
    return _load_model(cfg, device)


@torch.no_grad()
def anomaly_map_256(model, images, alpha):
    """ROI-재캘리된 분위수로 combined 맵(256) 산출."""
    out = model(images)
    ln = model._quantile_normalize(out["local_map_raw"], model.q_a_st, model.q_b_st)
    gn = model._quantile_normalize(out["global_map_raw"], model.q_a_ae, model.q_b_ae)
    combined = alpha * ln + (1.0 - alpha) * gn
    return F.interpolate(combined, size=(256, 256), mode="bilinear", align_corners=False)[:, 0]


def norm01(m):
    """상위 영역 강조 정규화: [p50, p99.5] → [0,1]."""
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
    ap.add_argument("--n", type=int, default=6, help="비교할 이상 샘플 수")
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

    base_model = _load(cfg, args.base, device)
    roi_model = _load(cfg, args.roi, device)

    # 두 모델 모두 ROI로 동일 재캘리브레이션
    dcfg = cfg["data"]
    img_size = int(dcfg.get("image_size", 256))
    calib_ds = EfficientADTestDataset(root_dir=dcfg.get("train_dir", "data/train"),
                                      transform=_get_test_transform(img_size), is_train=True)
    rng = np.random.default_rng(args.seed)
    cidx = rng.choice(len(calib_ds), size=min(args.calib_n, len(calib_ds)), replace=False)
    calib_loader = DataLoader(Subset(calib_ds, cidx.tolist()), batch_size=8, shuffle=False)
    ncfg = cfg.get("normalization", {})
    q_a, q_b = float(ncfg.get("q_a", 0.9)), float(ncfg.get("q_b", 0.995))
    print("base 재캘리:"); roi_recalibrate(base_model, calib_loader, device, args.roi_k, q_a, q_b)
    print("roi  재캘리:"); roi_recalibrate(roi_model, calib_loader, device, args.roi_k, q_a, q_b)

    # 이상 샘플 수집
    test_loader = _build_test_loader(cfg)
    rows = []
    for images, y, _ in test_loader:
        images = images.to(device)
        bmap = anomaly_map_256(base_model, images, args.alpha).cpu().numpy()
        rmap = anomaly_map_256(roi_model, images, args.alpha).cpu().numpy()
        g = images.mean(1).cpu().numpy()
        for i in range(images.shape[0]):
            if int(y[i]) == 1 and len(rows) < args.n:
                rows.append((g[i], bmap[i], rmap[i]))
        if len(rows) >= args.n:
            break

    n = len(rows)
    fig, axes = plt.subplots(n, 3, figsize=(11, 3.6 * n))
    if n == 1:
        axes = axes[None, :]
    for r, (g, bm, rm) in enumerate(rows):
        axes[r, 0].imshow(g, cmap="gray"); axes[r, 0].set_title("Input (anomaly)")
        axes[r, 1].imshow(overlay(g, bm)); axes[r, 1].set_title("base — anomaly overlay")
        axes[r, 2].imshow(overlay(g, rm)); axes[r, 2].set_title("ROI-trained — anomaly overlay")
        for c in range(3):
            axes[r, c].axis("off")
    fig.tight_layout()
    out_dir = Path(cfg.get("inference", {}).get("output_dir", "results/predictions"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "roi_vs_base_overlay.png"
    fig.savefig(out_path, dpi=130); plt.close(fig)
    print(f"\n오버레이 비교 저장 → {out_path}")


if __name__ == "__main__":
    main()

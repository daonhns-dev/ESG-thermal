"""
학습 시점 ROI로 학습한 EfficientAD의 'ROI-일치 평가'.

배경:
  train_efficientad.py --roi 로 학습한 모델은 ROI(고온 전경) 내부만 학습한다.
  그런데 기본 추론/캘리브레이션은 전체 프레임을 쓰므로, '학습 안 한 배경'의 큰 오차가
  스코어·정규화를 지배해 AUC가 랜덤(~0.5)으로 붕괴한다.
  → 학습을 ROI로 했으면 (1) 분위수 캘리브레이션 (2) 추론 스코어 도 ROI로 맞춰야 공정.

이 스크립트는 재학습 없이, 학습된 체크포인트에 대해:
  1) ROI 픽셀만으로 분위수(q_a/q_b) 재캘리브레이션
  2) ROI 내부에서만 topk_mean 스코어링
  하여 base(전체 프레임)와 비교한다. ROI 정의는 학습과 동일(밝기 > 평균+k·std).

사용법:
  python scripts/eval_efficientad_roi.py --config configs/config_efficientad.yaml \
    --checkpoint results/checkpoints/efficientad/efficientad_roi.pth \
    --roi-k 0.3 --alpha 0.3 --topk_ratio 0.15 --max_per_class 5000
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import torch
import torch.nn.functional as F
import yaml
from sklearn.metrics import roc_auc_score
from torch.utils.data import DataLoader, Subset

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datasets.dataset import ThermalImageDataset  # noqa: E402
from scripts.inference_efficientad import EfficientADTestDataset, _build_test_loader, _get_test_transform, _load_model  # noqa: E402
from scripts.train_efficientad import _brightness_roi  # noqa: E402


FEAT_HW = (64, 64)


@torch.no_grad()
def roi_recalibrate(model, loader, device, roi_k, q_a, q_b):
    """ROI(고온 전경) 픽셀만으로 local/global raw 맵 분위수 재계산."""
    model.eval()
    loc, glo = [], []
    for batch in loader:
        images = batch[0].to(device)
        out = model(images)
        roi = _brightness_roi(images, roi_k, FEAT_HW)          # (B,1,64,64)
        sel = roi.reshape(-1) > 0.5
        loc.append(out["local_map_raw"].reshape(-1)[sel].cpu())
        glo.append(out["global_map_raw"].reshape(-1)[sel].cpu())
    local_flat = torch.cat(loc).to(device)
    global_flat = torch.cat(glo).to(device)
    model.set_quantiles_from_maps(local_flat, global_flat, q_a, q_b)
    print(f"  [ROI 재캘리브레이션] q_a_st={model.q_a_st.item():.4f} q_b_st={model.q_b_st.item():.4f} "
          f"q_a_ae={model.q_a_ae.item():.4f} q_b_ae={model.q_b_ae.item():.4f} "
          f"(ROI 픽셀 {local_flat.numel():,}개)")


@torch.no_grad()
def score_test(model, loader, device, roi_k, alpha, topk_ratio, max_viz=0):
    """ROI 내부 topk_mean 스코어 + 전체프레임 스코어(참고) 동시 산출.
    max_viz>0 이면 이상 샘플 일부의 시각화 데이터도 수집."""
    model.eval()
    roi_scores, full_scores, labels = [], [], []
    viz = []  
    for images, y, _ in loader:
        images = images.to(device)
        out = model(images)
        full_scores.extend(out["image_score"].cpu().numpy())    
        lm, gm = out["local_map_raw"], out["global_map_raw"]
        ln = model._quantile_normalize(lm, model.q_a_st, model.q_b_st)
        gn = model._quantile_normalize(gm, model.q_a_ae, model.q_b_ae)
        combined = alpha * ln + (1.0 - alpha) * gn              
        roi = _brightness_roi(images, roi_k, FEAT_HW)
        B = combined.shape[0]
        for i in range(B):
            vals = combined[i][roi[i] > 0.5]
            if vals.numel() == 0:
                roi_scores.append(float(combined[i].max()))
            else:
                k = max(1, int(vals.numel() * topk_ratio))
                roi_scores.append(float(vals.reshape(-1).topk(k).values.mean()))
        labels.extend(y.numpy())

        if max_viz and len(viz) < max_viz:
            cmap256 = F.interpolate(combined, size=(256, 256), mode="bilinear", align_corners=False)
            roi256 = _brightness_roi(images, roi_k, (256, 256))
            for i in range(B):
                if len(viz) >= max_viz:
                    break
                if int(y[i]) == 1:  
                    viz.append((images[i].mean(0).cpu().numpy(), cmap256[i, 0].cpu().numpy(),
                                roi256[i, 0].cpu().numpy(), int(y[i]),))
    return np.array(roi_scores), np.array(full_scores), np.array(labels), viz


def auc_fp_fn(scores, labels):
    auc = float(roc_auc_score(labels, scores)) if len(np.unique(labels)) > 1 else float("nan")
    lo, hi = scores.min(), scores.max()
    best = {"f1": -1, "fp": 0, "fn": 0, "acc": 0.0}
    for thr in np.linspace(lo, hi, 200):
        pred = (scores >= thr).astype(int)
        tp = int(((pred == 1) & (labels == 1)).sum()); fp = int(((pred == 1) & (labels == 0)).sum())
        fn = int(((pred == 0) & (labels == 1)).sum()); tn = int(((pred == 0) & (labels == 0)).sum())
        prec = tp / max(tp + fp, 1); rec = tp / max(tp + fn, 1)
        f1 = 2 * prec * rec / max(prec + rec, 1e-8)
        if f1 > best["f1"]:
            best = {"f1": f1, "fp": fp, "fn": fn, "acc": (tp + tn) / max(len(labels), 1)}
    return auc, best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/config_efficientad.yaml")
    ap.add_argument("--checkpoint", required=True, help="ROI 학습 체크포인트 (efficientad_roi.pth)")
    ap.add_argument("--roi-k", type=float, default=0.3)
    ap.add_argument("--alpha", type=float, default=0.3)
    ap.add_argument("--topk_ratio", type=float, default=0.15)
    ap.add_argument("--max_per_class", type=int, default=5000)
    ap.add_argument("--calib_n", type=int, default=500, help="재캘리브레이션용 정상 이미지 수")
    ap.add_argument("--save_viz", type=int, default=6, help="저장할 이상 샘플 히트맵 수 (0=안함)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault("inference", {})["checkpoint"] = args.checkpoint
    cfg["inference"]["max_per_class"] = args.max_per_class
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}\nCheckpoint: {args.checkpoint}")

    model = _load_model(cfg, device)
    ncfg = cfg.get("normalization", {})
    q_a, q_b = float(ncfg.get("q_a", 0.9)), float(ncfg.get("q_b", 0.995))

    dcfg = cfg["data"]
    img_size = int(dcfg.get("image_size", 256))
    train_dir = dcfg.get("train_dir", "data/train")
    calib_ds = EfficientADTestDataset(root_dir=train_dir, transform=_get_test_transform(img_size), is_train=True)
    rng = np.random.default_rng(args.seed)
    idx = rng.choice(len(calib_ds), size=min(args.calib_n, len(calib_ds)), replace=False)
    calib_loader = DataLoader(Subset(calib_ds, idx.tolist()), batch_size=8, shuffle=False)

    print("\n--- 1) ROI 픽셀로 분위수 재캘리브레이션 ---")
    roi_recalibrate(model, calib_loader, device, args.roi_k, q_a, q_b)

    print("\n--- 2) Test 스코어링 (ROI vs 전체프레임) ---")
    test_loader = _build_test_loader(cfg)
    roi_s, full_s, labels, viz = score_test(model, test_loader, device, args.roi_k, args.alpha, args.topk_ratio, max_viz=args.save_viz)

    auc_roi, b_roi = auc_fp_fn(roi_s, labels)
    auc_full, b_full = auc_fp_fn(full_s, labels)

    print("\n" + "=" * 60)
    print(f"  ROI 학습 모델 평가 (test {len(labels)}장, roi_k={args.roi_k})")
    print("=" * 60)
    print(f"  {'스코어링 방식':22}{'AUC':>9}{'FP':>7}{'FN':>7}{'Acc':>8}")
    print("  " + "-" * 54)
    print(f"  {'전체프레임(기본,붕괴)':22}{auc_full:>9.4f}{b_full['fp']:>7d}{b_full['fn']:>7d}{b_full['acc']:>8.3f}")
    print(f"  {'ROI-일치(재캘리+ROI)':22}{auc_roi:>9.4f}{b_roi['fp']:>7d}{b_roi['fn']:>7d}{b_roi['acc']:>8.3f}")
    print("=" * 60)
    print("  참고: base(풀프레임 학습) 모델 AUC = 0.9596")

    # --- 히트맵 저장 ---
    if viz:
        out_dir = Path(cfg.get("inference", {}).get("output_dir", "results/predictions"))
        out_dir.mkdir(parents=True, exist_ok=True)
        tag = Path(args.checkpoint).stem  # efficientad_roi / efficientad
        n = len(viz)
        fig, axes = plt.subplots(n, 4, figsize=(14, 3.2 * n))
        if n == 1:
            axes = axes[None, :]
        for r, (gray, cmap, roi, lab) in enumerate(viz):
            axes[r, 0].imshow(gray, cmap="gray"); axes[r, 0].set_title("Input (anomaly)")
            axes[r, 1].imshow(cmap, cmap="jet"); axes[r, 1].set_title("Anomaly map (full)")
            axes[r, 2].imshow(cmap * roi, cmap="jet"); axes[r, 2].set_title("Anomaly map x ROI")
            axes[r, 3].imshow(roi, cmap="gray"); axes[r, 3].set_title("ROI mask")
            for c in range(4):
                axes[r, c].axis("off")
        fig.tight_layout()
        out_path = out_dir / f"roi_eval_heatmaps_{tag}_k{args.roi_k}.png"
        fig.savefig(out_path, dpi=130); plt.close(fig)
        print(f"  히트맵 저장 → {out_path}")


if __name__ == "__main__":
    main()

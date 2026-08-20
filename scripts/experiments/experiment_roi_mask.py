"""
온도 기반 ROI 마스킹 실험 (post-hoc, 재학습 불필요).

가설(§8):
  EfficientAD의 오탐(FP)은 장비가 아니라 배경/엣지/반사 등 물리적으로 무의미한
  영역에서 이상 반응이 튀어서 발생한다. CSV 온도맵에서 "고온 전경 = 장비 객체"
  ROI를 뽑아, 그 바깥의 이상 반응을 억제하면 판별력(AUC)을 깨지 않으면서
  FP를 줄일 수 있다. (데이터 제공처의 YOLO(객체검출)→FCDD(이상탐지) 2-stage를
  온도 마스크로 근사한 것.)

이 스크립트는 저장된 combined_map(256)에 온도 ROI 마스크를 곱해 스코어를 재집계하고,
마스크 없음/소프트/바이너리 세 방식의 AUC·FP·FN을 비교한다.

사용법:
  python scripts/experiment_roi_mask.py --config configs/config_efficientad.yaml \
      --max_per_class 500 --agg topk_mean --topk_ratio 0.15 --alpha 0.3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
import torch
import yaml
from PIL import Image
from sklearn.metrics import roc_auc_score

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datasets.dataset import load_thermal_csv  # noqa: E402
from scripts.inference_efficientad import _build_test_loader, _load_model, run_inference

from scripts.validate_efficientad_csv import (  # noqa: E402
    _resize_temp,
    _temp_hotspot_map,
    resolve_csv_path,
)


def _topk_mean(values: np.ndarray, topk_ratio: float, mask: Optional[np.ndarray] = None) -> float:
    """상위 topk_ratio 비율 픽셀 평균. mask 주어지면 mask>0 영역에서만."""
    if mask is not None:
        vals = values[mask > 0]
        if vals.size == 0:
            return float(values.min())  # ROI 없으면 최저값 (이상 아님)
    else:
        vals = values.reshape(-1)
    k = max(1, int(vals.size * topk_ratio))
    return float(np.sort(vals)[-k:].mean())


def _best_f1_threshold(scores: np.ndarray, labels: np.ndarray, n_steps: int = 200) -> tuple[float, dict]:
    """F1 최대화 threshold와 그 지점의 FP/FN 반환."""
    lo, hi = float(scores.min()), float(scores.max())
    best = {"f1": -1.0, "threshold": lo, "fp": 0, "fn": 0, "acc": 0.0}
    for thr in np.linspace(lo, hi, n_steps):
        pred = (scores >= thr).astype(int)
        tp = int(((pred == 1) & (labels == 1)).sum())
        fp = int(((pred == 1) & (labels == 0)).sum())
        fn = int(((pred == 0) & (labels == 1)).sum())
        tn = int(((pred == 0) & (labels == 0)).sum())
        prec = tp / max(tp + fp, 1)
        rec = tp / max(tp + fn, 1)
        f1 = 2 * prec * rec / max(prec + rec, 1e-8)
        if f1 > best["f1"]:
            best = {"f1": f1, "threshold": float(thr), "fp": fp, "fn": fn,
                    "acc": (tp + tn) / max(len(labels), 1)}
    return best["threshold"], best


def _binary_roi(temp_256: np.ndarray) -> np.ndarray:
    """고온 전경(장비) 바이너리 마스크: median + 0.5·std 초과 영역."""
    med = float(np.median(temp_256))
    std = float(np.std(temp_256)) + 1e-6
    return (temp_256 > med + 0.5 * std).astype(np.float32)


def _border_mask(size: int, margin_frac: float) -> np.ndarray:
    """바깥 margin_frac 비율 테두리를 0으로, 안쪽을 1로 하는 기하학적 마스크."""
    m = max(1, int(size * margin_frac))
    mask = np.zeros((size, size), dtype=np.float32)
    mask[m:size - m, m:size - m] = 1.0
    return mask


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(PROJECT_ROOT / "configs" / "config_efficientad.yaml"))
    parser.add_argument("--max_per_class", type=int, default=500)
    parser.add_argument("--alpha", type=float, default=0.3)
    parser.add_argument("--agg", type=str, default="topk_mean")
    parser.add_argument("--topk_ratio", type=float, default=0.15)
    parser.add_argument("--border_frac", type=float, default=0.10, help="테두리 크롭 비율 (0.10=바깥 10%%)")
    parser.add_argument("--csv_dir", type=str, default=None)
    parser.add_argument("--data_dir", type=str, default=None)
    parser.add_argument("--num_viz", type=int, default=8)
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault("inference", {})["max_per_class"] = int(args.max_per_class)

    device = torch.device("cuda" if cfg.get("device") == "cuda" and torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    model = _load_model(cfg, device)
    model.set_score_params(alpha=args.alpha, agg=args.agg, topk_ratio=args.topk_ratio)

    loader = _build_test_loader(cfg)
    print(f"Test images: {len(loader.dataset)}")
    scores, _, _, labels, paths, map_list = run_inference(model, loader, device, store_maps=True)
    labels = np.array(labels)

    data_root = Path(args.data_dir or cfg["data"]["test_dir"]).parent
    csv_root = Path(args.csv_dir or data_root / "csv")

    model_s, repro_s, soft_s, bin_s, border_s = [], [], [], [], []
    keep_labels = []
    viz_rows = []
    missing = 0

    for i, path in enumerate(paths):
        csv_path = resolve_csv_path(path, data_root, csv_root)
        if csv_path is None:
            missing += 1
            continue
        temp = load_thermal_csv(csv_path)
        if temp.size == 0:
            missing += 1
            continue

        cmap = map_list[i]["combined_map"]  
        temp_256 = _resize_temp(temp, size=cmap.shape[0])
        soft_mask = _temp_hotspot_map(temp_256)          
        bin_mask = _binary_roi(temp_256)                 
        border_m = _border_mask(cmap.shape[0], args.border_frac)  # 테두리 제거 (온도 무관)

        model_s.append(float(scores[i]))
        repro_s.append(_topk_mean(cmap, args.topk_ratio))                    # 마스크 없음(재현)
        soft_s.append(_topk_mean(cmap * soft_mask, args.topk_ratio))         # 소프트 가중
        bin_s.append(_topk_mean(cmap, args.topk_ratio, mask=bin_mask))       # ROI 내부만
        border_s.append(_topk_mean(cmap, args.topk_ratio, mask=border_m))    # 테두리 크롭
        keep_labels.append(int(labels[i]))

        if len(viz_rows) < args.num_viz:
            viz_rows.append((path, cmap, temp_256, bin_mask, cmap * bin_mask, int(labels[i])))

    keep_labels = np.array(keep_labels)
    model_s = np.array(model_s); repro_s = np.array(repro_s)
    soft_s = np.array(soft_s); bin_s = np.array(bin_s); border_s = np.array(border_s)

    print(f"\nCSV matched: {len(keep_labels)}  (missing {missing})")
    print("=" * 68)
    print(f"  {'variant':<22}{'AUC':>8}{'FP':>7}{'FN':>7}{'Acc':>8}")
    print("  " + "-" * 62)

    results = {}
    for name, s in [("model (실제 forward)", model_s),
                    ("unmasked (재현)", repro_s),
                    ("soft mask (온도가중)", soft_s),
                    ("binary ROI (내부만)", bin_s),
                    (f"border crop ({int(args.border_frac*100)}%)", border_s)]:
        if len(np.unique(keep_labels)) < 2:
            continue
        auc = float(roc_auc_score(keep_labels, s))
        thr, best = _best_f1_threshold(s, keep_labels)
        results[name] = {"auc": auc, **best}
        print(f"  {name:<22}{auc:>8.4f}{best['fp']:>7d}{best['fn']:>7d}{best['acc']:>8.3f}")
    print("=" * 68)

    out_dir = Path(cfg.get("inference", {}).get("output_dir", "results/predictions")).parent / "csv_validation"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 시각화: 입력 combined / 온도 / ROI 마스크 / 마스킹된 combined
    if viz_rows:
        n = len(viz_rows)
        fig, axes = plt.subplots(n, 4, figsize=(14, 3.2 * n))
        if n == 1:
            axes = axes[None, :]
        for r, (path, cmap, temp_256, bin_mask, masked, lab) in enumerate(viz_rows):
            axes[r, 0].imshow(cmap, cmap="jet"); axes[r, 0].set_title(f"Combined ({'anom' if lab else 'norm'})")
            axes[r, 1].imshow(temp_256, cmap="inferno"); axes[r, 1].set_title("CSV Temp")
            axes[r, 2].imshow(bin_mask, cmap="gray"); axes[r, 2].set_title("Binary ROI")
            axes[r, 3].imshow(masked, cmap="jet"); axes[r, 3].set_title("Combined × ROI")
            for c in range(4):
                axes[r, c].axis("off")
        fig.tight_layout()
        viz_path = out_dir / "roi_mask_examples.png"
        fig.savefig(viz_path, dpi=130)
        plt.close(fig)
        print(f"\n예시 시각화: {viz_path}")

    # AUC/FP 비교 막대
    if results:
        fig, axes = plt.subplots(1, 2, figsize=(13, 5))
        names = list(results.keys())
        aucs = [results[n]["auc"] for n in names]
        fps = [results[n]["fp"] for n in names]
        colors = ["gray", "steelblue", "orange", "seagreen"][:len(names)]
        axes[0].bar(range(len(names)), aucs, color=colors)
        axes[0].set_xticks(range(len(names))); axes[0].set_xticklabels(names, rotation=20, ha="right")
        axes[0].set_ylim(min(aucs) - 0.02, 1.0); axes[0].set_title("AUC by masking variant")
        for i, v in enumerate(aucs): axes[0].text(i, v, f"{v:.4f}", ha="center", va="bottom")
        axes[1].bar(range(len(names)), fps, color=colors)
        axes[1].set_xticks(range(len(names))); axes[1].set_xticklabels(names, rotation=20, ha="right")
        axes[1].set_title("FP at F1-optimal threshold")
        for i, v in enumerate(fps): axes[1].text(i, v, str(v), ha="center", va="bottom")
        fig.tight_layout()
        cmp_path = out_dir / "roi_mask_comparison.png"
        fig.savefig(cmp_path, dpi=130)
        plt.close(fig)
        print(f"비교 차트:   {cmp_path}")


if __name__ == "__main__":
    main()

"""
학습 시점 ROI(loss 마스킹) 실험 — AE 대상 (POC).

배경:
  §8-5에서 '사후(post-hoc) 마스킹'은 성능을 못 올린다는 것을 확인했다. 그러나 사후
  마스킹은 이미 풀프레임으로 학습된 모델의 출력만 가리는 것이라, "학습 자체를 ROI로
  제한하면(배경/테두리를 아예 학습하지 않으면) 다른가?"라는 질문은 남는다.
  이 스크립트는 그 가설을 통제된 A/B로 검증한다.

방법:
  동일한 축소 예산으로 AE 2개를 학습해 비교한다.
    - base : 재구성 손실을 전체 픽셀에서 계산 (기존 방식)
    - roi  : 재구성 손실을 ROI(고온 전경=장비) 내부에서만 계산
  ROI 마스크는 입력 열화상의 밝기(=온도에 비례)에서 즉석 계산: 픽셀 > 평균 + k·표준편차.
  이상 스코어도 동일 기준(base=전체 / roi=ROI 내부)으로 계산해 공정 비교.

  ※ CPU만 있는 환경에서는 전체(22k장·300ep) 재학습이 불가하므로, subset·epochs를
    줄인 POC로 '상대 비교'를 본다. 두 모델을 완전히 동일한 예산·초기화로 학습하므로
    절대 성능이 낮아도 ROI vs 풀프레임의 차이는 공정하게 관찰된다.

사용법 (로컬에서 직접 실행):
  python scripts/experiment_roi_train_ae.py --config configs/config_ae.yaml \
      --train_subset 2000 --test_per_class 300 --epochs 15
  # GPU 있으면 자동 사용. subset/epochs를 늘리면 더 신뢰도 높은 비교 가능.
"""

from __future__ import annotations

import argparse
import random
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

from datasets.dataset import ThermalImageDataset, get_transforms  # noqa: E402
from models.ae import ConvAutoEncoder  # noqa: E402

IMAGE_SIZE = 256  


def set_seed(seed: int):
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def roi_mask(x: torch.Tensor, k: float) -> torch.Tensor:
    """입력 밝기(=온도) 기반 고온 전경 마스크. 픽셀 > 이미지별 평균 + k·std → 1."""
    B = x.shape[0]
    flat = x.view(B, -1)
    mean = flat.mean(dim=1).view(B, 1, 1, 1)
    std = flat.std(dim=1).view(B, 1, 1, 1)
    return (x > (mean + k * std)).float()


def recon_error(model, x, use_roi, k):
    """(anomaly_map, per-image score). roi면 ROI 내부에서만 오차 집계."""
    recon, _ = model(x)
    recon = recon.clamp(0.0, 1.0)
    diff2 = (recon - x) ** 2                      # (B,1,H,W)
    if use_roi:
        m = roi_mask(x, k)
        amap = diff2 * m
        denom = m.sum(dim=[1, 2, 3]).clamp(min=1.0)
        score = amap.sum(dim=[1, 2, 3]) / denom
    else:
        amap = diff2
        score = diff2.mean(dim=[1, 2, 3])
    return amap, score


def train_ae(cfg, train_loader, device, use_roi, k, epochs, lr, seed):
    """AE 학습. use_roi면 재구성 손실을 ROI 내부에서만 계산."""
    set_seed(seed)  
    mcfg = cfg["model"]
    model = ConvAutoEncoder(
        input_channels=int(mcfg.get("input_channels", 1)),
        latent_dim=int(mcfg.get("latent_dim", 128)),
        base_channels=int(mcfg.get("base_channels", 32)),
        depth=int(mcfg.get("depth", 5)),
        vae=False,
        use_attention=bool(mcfg.get("use_attention", True)),
    ).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=lr)

    model.train()
    for epoch in range(1, epochs + 1):
        losses = []
        for x, _, _ in train_loader:
            x = x.to(device)
            recon, _ = model(x)
            recon = recon.clamp(0.0, 1.0)
            diff2 = (recon - x) ** 2
            if use_roi:
                m = roi_mask(x, k)
                loss = (diff2 * m).sum() / m.sum().clamp(min=1.0)
            else:
                loss = diff2.mean()
            opt.zero_grad(); loss.backward(); opt.step()
            losses.append(loss.item())
        print(f"    [{'roi ' if use_roi else 'base'}] epoch {epoch:3d}/{epochs}  loss={np.mean(losses):.6f}")
    return model


@torch.no_grad()
def evaluate(model, test_loader, device, use_roi, k):
    model.eval()
    scores, labels, maps, xs = [], [], [], []
    for x, y, _ in test_loader:
        x = x.to(device)
        amap, s = recon_error(model, x, use_roi, k)
        scores.append(s.cpu().numpy()); labels.append(y.numpy())
        maps.append(amap.cpu().numpy()); xs.append(x.cpu().numpy())
    scores = np.concatenate(scores); labels = np.concatenate(labels)
    maps = np.concatenate(maps); xs = np.concatenate(xs)
    auc = float(roc_auc_score(labels, scores)) if len(np.unique(labels)) > 1 else float("nan")
    lo, hi = scores.min(), scores.max()
    best = {"f1": -1, "fp": 0, "fn": 0}
    for thr in np.linspace(lo, hi, 200):
        pred = (scores >= thr).astype(int)
        tp = int(((pred == 1) & (labels == 1)).sum()); fp = int(((pred == 1) & (labels == 0)).sum())
        fn = int(((pred == 0) & (labels == 1)).sum())
        prec = tp / max(tp + fp, 1); rec = tp / max(tp + fn, 1)
        f1 = 2 * prec * rec / max(prec + rec, 1e-8)
        if f1 > best["f1"]:
            best = {"f1": f1, "fp": fp, "fn": fn}
    return {"auc": auc, **best, "scores": scores, "labels": labels, "maps": maps, "xs": xs}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="configs/config_ae.yaml")
    p.add_argument("--train_subset", type=int, default=2000, help="학습에 쓸 정상 이미지 수")
    p.add_argument("--test_per_class", type=int, default=300)
    p.add_argument("--epochs", type=int, default=15)
    p.add_argument("--batch_size", type=int, default=32)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--roi_k", type=float, default=0.3, help="ROI 임계: 평균 + k·std")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    dcfg = cfg["data"]
    train_dir = dcfg.get("train_dir", "data/train")
    test_dir = dcfg.get("test_dir", "data/test")
    tf = get_transforms(IMAGE_SIZE, is_train=False, normalize_thermal=False)

    full_train = ThermalImageDataset(root_dir=train_dir, transform=tf, is_train=True)
    rng = np.random.default_rng(args.seed)
    tr_idx = rng.choice(len(full_train), size=min(args.train_subset, len(full_train)), replace=False)
    train_ds = Subset(full_train, tr_idx.tolist())

    full_test = ThermalImageDataset(root_dir=test_dir, transform=tf, is_train=False)
    by = {0: [], 1: []}
    for i, lb in enumerate(full_test.labels):
        by[lb].append(i)
    te_idx = []
    for lb in (0, 1):
        sel = rng.choice(by[lb], size=min(args.test_per_class, len(by[lb])), replace=False)
        te_idx.extend(sel.tolist())
    test_ds = Subset(full_test, te_idx)
    print(f"Train subset: {len(train_ds)} (정상) | Test: {len(test_ds)} (정상 {min(args.test_per_class,len(by[0]))} / 이상 {min(args.test_per_class,len(by[1]))})")

    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True, num_workers=0)
    test_loader = DataLoader(test_ds, batch_size=args.batch_size, shuffle=False, num_workers=0)

    results = {}
    for use_roi in (False, True):
        name = "roi" if use_roi else "base"
        print(f"\n=== 학습: {name} ===")
        model = train_ae(cfg, train_loader, device, use_roi, args.roi_k, args.epochs, args.lr, args.seed)
        results[name] = evaluate(model, test_loader, device, use_roi, args.roi_k)

    print("\n" + "=" * 56)
    print(f"  학습 시점 ROI A/B (train {len(train_ds)}장, {args.epochs}ep, 동일 예산)")
    print("=" * 56)
    print(f"  {'변형':10}{'AUC':>10}{'FP':>7}{'FN':>7}")
    print("  " + "-" * 40)
    for name in ("base", "roi"):
        r = results[name]
        print(f"  {name:10}{r['auc']:>10.4f}{r['fp']:>7d}{r['fn']:>7d}")
    print("=" * 56)

    # 결과 이미지: 입력 / base map / roi map / roi mask (테스트 이상 샘플 몇 개)
    out_dir = Path(cfg.get("inference", {}).get("output_dir", "results/predictions"))
    out_dir.mkdir(parents=True, exist_ok=True)

    rb, rr = results["base"], results["roi"]
    anom_idx = np.where(rb["labels"] == 1)[0][:4]
    n = len(anom_idx)
    if n > 0:
        fig, axes = plt.subplots(n, 4, figsize=(14, 3.3 * n))
        if n == 1:
            axes = axes[None, :]
        for r_i, idx in enumerate(anom_idx):
            x_img = rb["xs"][idx, 0]
            m = (x_img > (x_img.mean() + args.roi_k * x_img.std())).astype(float)
            axes[r_i, 0].imshow(x_img, cmap="gray"); axes[r_i, 0].set_title("Input (anomaly)")
            axes[r_i, 1].imshow(rb["maps"][idx, 0], cmap="jet"); axes[r_i, 1].set_title("base anomaly map")
            axes[r_i, 2].imshow(rr["maps"][idx, 0], cmap="jet"); axes[r_i, 2].set_title("ROI-trained map")
            axes[r_i, 3].imshow(m, cmap="gray"); axes[r_i, 3].set_title("ROI mask")
            for c in range(4):
                axes[r_i, c].axis("off")
        fig.tight_layout()
        out_path = out_dir / "roi_train_ae_comparison.png"
        fig.savefig(out_path, dpi=130); plt.close(fig)
        print(f"결과 이미지 → {out_path}")


if __name__ == "__main__":
    main()

"""
'렌더 이미지 밝기 ≈ 상대 온도'를 시각적으로 확인.

각 샘플에 대해:
  Input(gray, 밝기) | CSV Temp(°C) | 밝기 vs 온도 산점도(픽셀 단위, 상관 r)
를 나란히 보여준다. 밝기와 온도의 공간 구조가 거의 같고 산점도가 직선에 가까우면
(r≈0.9+), "이미지가 이미 상대 온도를 담고 있다"가 눈으로 확인된다.

(모델 불필요 — 이미지와 CSV만 사용, 빠름)

사용법:
  python scripts/viz_brightness_temp_corr.py --config configs/config_efficientad.yaml --n 4
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
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
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--size", type=int, default=256)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    test_dir = cfg["data"].get("test_dir", "data/test")
    data_root = Path(test_dir).parent
    csv_root = data_root / "csv"

    ds = ThermalImageDataset(root_dir=test_dir, transform=None, is_train=False)
    rng = np.random.default_rng(args.seed)
    idxs = rng.permutation(len(ds.image_paths))

    rows, corrs = [], []
    for i in idxs:
        path = ds.image_paths[i]
        csv_path = resolve_csv_path(path, data_root, csv_root)
        if csv_path is None:
            continue
        temp = load_thermal_csv(csv_path)
        if temp.size == 0:
            continue
        gray = np.array(Image.open(path).convert("L").resize((args.size, args.size), Image.BILINEAR)).astype(np.float32)
        temp256 = _resize_temp(temp, size=args.size)
        g, t = gray.ravel(), temp256.ravel()
        if g.std() < 1e-6 or t.std() < 1e-6:
            continue
        r = float(np.corrcoef(g, t)[0, 1])
        rows.append((gray, temp256, r))
        corrs.append(r)
        if len(rows) >= args.n:
            break

    n = len(rows)
    fig, axes = plt.subplots(n, 3, figsize=(13, 4.0 * n))
    if n == 1:
        axes = axes[None, :]
    for r_i, (gray, temp, r) in enumerate(rows):
        axes[r_i, 0].imshow(gray, cmap="gray"); axes[r_i, 0].set_title("Input (brightness)")
        axes[r_i, 0].axis("off")
        im = axes[r_i, 1].imshow(temp, cmap="inferno")
        plt.colorbar(im, ax=axes[r_i, 1], fraction=0.046)
        axes[r_i, 1].set_title("CSV Temp (degC)"); axes[r_i, 1].axis("off")
        axes[r_i, 2].hexbin(gray.ravel(), temp.ravel(), gridsize=45, cmap="viridis", mincnt=1)
        axes[r_i, 2].set_title(f"brightness vs temp   r = {r:.3f}")
        axes[r_i, 2].set_xlabel("brightness (0-255)"); axes[r_i, 2].set_ylabel("temp (degC)")

    mean_r = float(np.mean(corrs))
    fig.suptitle(f"Brightness ~ relative temperature  (mean pixel corr r = {mean_r:.3f})", fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.98])
    out_dir = Path(cfg.get("inference", {}).get("output_dir", "results/predictions"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "brightness_vs_temp.png"
    fig.savefig(out_path, dpi=130); plt.close(fig)
    print(f"평균 픽셀상관 r = {mean_r:.3f}  (표본 {n})")
    print(f"저장 → {out_path}")


if __name__ == "__main__":
    main()

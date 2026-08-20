"""
같은 설비의 정상 vs 이상 이미지를 나란히 → "이 데이터의 이상이 무엇인가" 시각 파악.

파일명 규약: 앞부분 = 설비 코드, 끝 4자리 = 상태(0101=정상 / 0102=이상).
같은 설비 코드가 normal/anomaly 양쪽에 있으므로, 짝지어 비교하면 무엇이 달라지는지 보인다.

사용법:
  python scripts/viz_normal_vs_anomaly_pairs.py --config configs/config_efficientad.yaml --n 6
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import yaml
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datasets.dataset import load_thermal_csv  # noqa: E402
from scripts.validate_efficientad_csv import _resize_temp, resolve_csv_path  # noqa: E402


def prefix(name: str) -> str:
    return name.split("(")[0]  # 설비 코드


def load_gray(path, size):
    return np.array(Image.open(path).convert("L").resize((size, size), Image.BILINEAR))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/config_efficientad.yaml")
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--size", type=int, default=256)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    test_dir = Path(cfg["data"].get("test_dir", "data/test"))
    data_root = test_dir.parent
    csv_root = data_root / "csv"

    norm_dir, anom_dir = test_dir / "normal", test_dir / "anomaly"
    norm = defaultdict(list); anom = defaultdict(list)
    for p in norm_dir.glob("*.jpg"):
        norm[prefix(p.name)].append(p)
    for p in anom_dir.glob("*.jpg"):
        anom[prefix(p.name)].append(p)

    common = sorted(set(norm) & set(anom))
    rng = np.random.default_rng(args.seed)
    rng.shuffle(common)
    common = common[:args.n]
    print(f"공통 설비 코드: {len(set(norm) & set(anom))}개, {len(common)}개 시각화")

    def temp_of(path):
        cp = resolve_csv_path(path, data_root, csv_root)
        if cp is None:
            return None
        t = load_thermal_csv(cp)
        return _resize_temp(t, size=args.size) if t.size else None

    n = len(common)
    fig, axes = plt.subplots(n, 4, figsize=(15, 3.7 * n))
    if n == 1:
        axes = axes[None, :]
    for r, pfx in enumerate(common):
        npath = sorted(norm[pfx])[0]; apath = sorted(anom[pfx])[0]
        ng, ag = load_gray(npath, args.size), load_gray(apath, args.size)
        nt, at = temp_of(npath), temp_of(apath)
        axes[r, 0].imshow(ng, cmap="gray"); axes[r, 0].set_title(f"NORMAL  {pfx}"); axes[r, 0].axis("off")
        if nt is not None:
            im = axes[r, 1].imshow(nt, cmap="inferno"); plt.colorbar(im, ax=axes[r, 1], fraction=0.046)
        axes[r, 1].set_title("normal Temp"); axes[r, 1].axis("off")
        axes[r, 2].imshow(ag, cmap="gray"); axes[r, 2].set_title("ANOMALY"); axes[r, 2].axis("off")
        if at is not None:
            im = axes[r, 3].imshow(at, cmap="inferno"); plt.colorbar(im, ax=axes[r, 3], fraction=0.046)
        axes[r, 3].set_title("anomaly Temp"); axes[r, 3].axis("off")

    fig.tight_layout()
    out_dir = Path(cfg.get("inference", {}).get("output_dir", "results/predictions"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "normal_vs_anomaly_pairs.png"
    fig.savefig(out_path, dpi=120); plt.close(fig)
    print(f"저장 → {out_path}")


if __name__ == "__main__":
    main()

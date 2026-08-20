"""
EfficientAD: q_a/q_b 재보정(학습과 동일 train→val 분할) × topk_ratio 그리드 서치.
재학습 없이 체크포인트 가중치만 사용.

사용:
  python scripts/grid_search_efficientad_params.py --config configs/config_efficientad.yaml
  python scripts/grid_search_efficientad_params.py --quick   # 소형 그리드 (1a+1b)
  python scripts/grid_search_efficientad_params.py --topk_only  # 1b만, 체크포인트 q 유지
  python scripts/grid_search_efficientad_params.py --calib_max_batches 50  # 캘리브 일부만(탐색용)
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import numpy as np
import torch
import yaml
from sklearn.metrics import roc_auc_score
from torch.utils.data import DataLoader, Subset

SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from datasets.dataset import create_efficientad_train_loader  # noqa: E402
import inference_efficientad as _inf  # noqa: E402

_load_model = _inf._load_model
_build_test_loader = _inf._build_test_loader
run_inference = _inf.run_inference
from utils.metrics import compute_metrics  # noqa: E402


def _split_train_val(full_dataset, val_ratio: float, seed: int):
    n = len(full_dataset)
    rng = np.random.default_rng(seed)
    perm = rng.permutation(n)
    n_val = max(1, int(n * val_ratio))
    return perm[n_val:].tolist(), perm[:n_val].tolist()


@torch.no_grad()
def collect_calibration_flats(model, val_loader: DataLoader, device: torch.device, max_batches: int | None = None,):
    """train_efficientad.compute_map_normalization 과 동일: raw 64×64 맵 픽셀 수집."""
    model.eval()
    chunks_l, chunks_g = [], []
    n_batches = len(val_loader) if max_batches is None else min(max_batches, len(val_loader))
    for i, batch in enumerate(val_loader):
        if max_batches is not None and i >= max_batches:
            break
        images = batch[0].to(device)
        out = model(images)
        chunks_l.append(out["local_map_raw"].flatten().cpu())
        chunks_g.append(out["global_map_raw"].flatten().cpu())
        if (i + 1) % 50 == 0 or (i + 1) == n_batches:
            print(f"  calib forward {i + 1}/{n_batches} batches", flush=True)
    local_flat = torch.cat(chunks_l).to(device)
    global_flat = torch.cat(chunks_g).to(device)
    return local_flat, global_flat


def _build_val_loader(cfg: dict) -> DataLoader:
    data_cfg = cfg["data"]
    tr = cfg["training"]
    norm_cfg = cfg["normalization"]
    seed = int(cfg.get("seed", 42))
    image_size = int(data_cfg.get("image_size", 256))
    nw = int(cfg.get("inference", {}).get("num_workers", tr.get("num_workers", 4)))
    pin = torch.cuda.is_available() and cfg.get("device") == "cuda"

    stats_loader = create_efficientad_train_loader(
        train_dir=data_cfg["train_dir"],
        batch_size=int(tr.get("teacher_stats_batch_size", 8)),
        image_size=image_size,
        num_workers=nw,
        shuffle=False,
        pin_memory=pin,
        train_fraction=float(data_cfg.get("train_fraction", 1.0)),
        subset_seed=seed,
    )
    full_ds = stats_loader.dataset
    val_ratio = float(norm_cfg.get("val_ratio", 0.1))
    val_seed = int(norm_cfg.get("quantile_split_seed", seed))
    _train_idx, val_idx = _split_train_val(full_ds, val_ratio, val_seed)
    val_subset = Subset(full_ds, val_idx)
    return DataLoader(
        val_subset,
        batch_size=8,
        shuffle=False,
        num_workers=nw,
        pin_memory=pin,
    )


def run_grid(cfg: dict, grids: dict, alpha: float, max_per_class: int | None, calib_max_batches: int | None = None, topk_only: bool = False,) -> list[dict]:
    device = torch.device("cuda" if cfg.get("device") == "cuda" and torch.cuda.is_available() else "cpu")
    cfg.setdefault("inference", {})
    if max_per_class is not None:
        cfg["inference"]["max_per_class"] = int(max_per_class)
    cfg["inference"]["num_workers"] = int(cfg["inference"].get("num_workers", 0))

    print(f"Device: {device}", flush=True)
    model = _load_model(cfg, device)

    local_flat = global_flat = None
    if not topk_only:
        print("\n--- 캘리브레이션(val) 로드 — train_efficientad 와 동일 분할 ---", flush=True)
        val_loader = _build_val_loader(cfg)
        print(f"  Val images: {len(val_loader.dataset)}", flush=True)
        t0 = time.time()
        local_flat, global_flat = collect_calibration_flats(
            model, val_loader, device, max_batches=calib_max_batches
        )
        if calib_max_batches is not None:
            print(
                "  [주의] calib_max_batches 로 val 일부만 사용 — 학습 시 전체 val과 분위수가 다를 수 있음.",
                flush=True,
            )
        print(
            f"  픽셀 수 local={local_flat.numel():,}, global={global_flat.numel():,} ({time.time()-t0:.1f}s)",
            flush=True,
        )
    else:
        print("\n--- topk_only: 체크포인트 분위수 유지 (재보정 생략) ---", flush=True)

    test_loader = _build_test_loader(cfg)
    print(f"\n--- Test: {len(test_loader.dataset)} images ---")
    use_fp16 = bool(cfg.get("inference", {}).get("use_fp16", False))

    q_as = grids["q_a"]
    q_bs = grids["q_b"]
    topks = grids["topk_ratio"]
    rows: list[dict] = []

    if topk_only:
        qa_qb_pairs = [(None, None)]
        total = len(topks)
    else:
        qa_qb_pairs = [(qa, qb) for qa in q_as for qb in q_bs if qa < qb]
        total = len(qa_qb_pairs) * len(topks)

    run_i = 0
    t_all = time.time()
    for qa, qb in qa_qb_pairs:
        if not topk_only:
            assert local_flat is not None and global_flat is not None
            model.set_quantiles_from_maps(local_flat, global_flat, float(qa), float(qb))
        for tk in topks:
            run_i += 1
            model.set_score_params(alpha=float(alpha), agg="topk_mean", topk_ratio=float(tk))
            t_inf = time.time()
            scores, loc_s, glo_s, labels, _, _ = run_inference(model, test_loader, device, use_fp16, store_maps=False)
            inf_s = time.time() - t_inf
            m = compute_metrics(scores, labels, threshold=None)
            auc_l = float(roc_auc_score(labels, loc_s))
            auc_g = float(roc_auc_score(labels, glo_s))
            row = {
                "q_a": qa if qa is not None else float("nan"),
                "q_b": qb if qb is not None else float("nan"),
                "topk_ratio": tk,
                "alpha": alpha,
                "auc": m["auc"],
                "auc_local": auc_l,
                "auc_global": auc_g,
                "f1": m["f1"],
                "accuracy": m["accuracy"],
                "threshold": m["threshold"],
                "fp": m.get("false_positive", -1),
                "fn": m.get("false_negative", -1),
                "infer_sec": round(inf_s, 2),
                "topk_only": int(topk_only),
            }
            rows.append(row)
            qa_s = "ckpt" if topk_only else f"{qa}"
            qb_s = "ckpt" if topk_only else f"{qb}"
            print(
                f"[{run_i}/{total}] q_a={qa_s} q_b={qb_s} topk={tk} "
                f"AUC={m['auc']:.4f} F1={m['f1']:.4f} FP={row['fp']} FN={row['fn']} ({inf_s:.1f}s)",
                flush=True,
            )

    print(f"\n전체 그리드 소요: {time.time()-t_all:.1f}s")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=str, default=str(PROJECT_ROOT / "configs" / "config_efficientad.yaml"))
    ap.add_argument("--alpha", type=float, default=None, help="미지정 시 config inference.score_alpha")
    ap.add_argument(
        "--max_per_class",
        type=int,
        default=500,
        help="클래스당 최대 평가 장수 (실험 로그: 500).",
    )
    ap.add_argument(
        "--full_test",
        action="store_true",
        help="max_per_class 비활성화(전체 test).",
    )
    ap.add_argument("--quick", action="store_true", help="3×2×2 소형 그리드")
    ap.add_argument(
        "--calib_max_batches",
        type=int,
        default=None,
        metavar="N",
        help="캘리브레이션 val forward 최대 배치 수(미지정=전체). CPU 탐색용으로만 권장.",
    )
    ap.add_argument(
        "--topk_only",
        action="store_true",
        help="1b만: 체크포인트 q 유지하고 topk_ratio만 스윕(캘리브레이션 생략).",
    )
    ap.add_argument(
        "--output",
        type=str,
        default="",
        help="CSV 경로 (기본: results/grid_efficientad_<timestamp>.csv)",
    )
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    alpha = args.alpha
    if alpha is None:
        alpha = float(cfg.get("inference", {}).get("score_alpha", 0.4))

    if args.quick:
        grids = {
            "q_a": [0.85, 0.9, 0.95],
            "q_b": [0.99, 0.995],
            "topk_ratio": [0.01, 0.03],
        }
    else:
        grids = {
            "q_a": [0.85, 0.9, 0.95],
            "q_b": [0.99, 0.995, 0.999],
            "topk_ratio": [0.01, 0.03, 0.05],
        }

    max_pc = None if args.full_test else int(args.max_per_class)
    rows = run_grid(
        cfg,
        grids,
        alpha=alpha,
        max_per_class=max_pc,
        calib_max_batches=args.calib_max_batches,
        topk_only=args.topk_only,
    )

    rows.sort(key=lambda r: (r["auc"], r["f1"]), reverse=True)
    print("\n=== 상위 10 (AUC, F1 내림차순) ===", flush=True)
    for r in rows[:10]:
        print(
            f"  q_a={r['q_a']} q_b={r['q_b']} topk={r['topk_ratio']} "
            f"AUC={r['auc']:.4f} F1={r['f1']:.4f} FP={r['fp']} FN={r['fn']}",
            flush=True,
        )

    if not rows:
        print("결과 행이 없습니다.", flush=True)
        return

    out = args.output
    if not out:
        out = str(PROJECT_ROOT / "results" / f"grid_efficientad_q_topk_{int(time.time())}.csv")
    outp = Path(out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    keys = list(rows[0].keys())
    with open(outp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(f"\nCSV 저장: {outp}")


if __name__ == "__main__":
    main()

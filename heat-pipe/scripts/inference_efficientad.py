"""
EfficientAD 추론 및 평가 (Algorithm 2).

파이프라인:
  1. 체크포인트 로드 (train_efficientad.py 산출물)
  2. Test 데이터 (normal + anomaly) 로드
  3. 이미지별 추론 → combined anomaly map → image-level score
  4. Threshold 결정 (auto: F1 최대화 / 수동 지정)
  5. 메트릭 출력 (AUC, F1, Precision, Recall, Accuracy, Confusion Matrix)
  6. Heatmap 저장 (local / global / combined anomaly map)

사용법:
  python scripts/inference_efficientad.py --config configs/config_efficientad.yaml
  python scripts/inference_efficientad.py --config configs/config_efficientad.yaml --threshold 0.05
  python scripts/inference_efficientad.py --config configs/config_efficientad.yaml --image path/to/single.jpg

기존 AE baseline과 동일 test 데이터(data/test)로 비교 가능.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
import torch
import yaml
from torch.utils.data import DataLoader, Subset
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datasets.dataset import ThermalImageDataset, get_efficientad_transforms  # noqa: E402
from models.efficientad import EfficientAD  # noqa: E402
from utils.metrics import compute_metrics, find_optimal_threshold, print_metrics  # noqa: E402
from utils.visualization import (  # noqa: E402
    plot_efficientad_maps,
    plot_score_distribution,
    plot_roc_curve,
)


# =====================================================================
# 데이터 로드 (RGB 유지 — grayscale 변환 없이)
# =====================================================================

class EfficientADTestDataset(ThermalImageDataset):
    """
    ThermalImageDataset 상속, EfficientAD 입력 규격(3ch)으로 로드.
    학습 경로와 동일하게 grayscale → 3채널 복제로 처리하여 train/test 도메인 일치 보장.
    """

    def __getitem__(self, idx):
        img_path = self.image_paths[idx]
        label = self.labels[idx]
        image = Image.open(img_path)
        if image.mode != "L":
            image = image.convert("L")
        if self.transform:
            image = self.transform(image)
        return image, label, str(img_path)


def _get_test_transform(image_size: int = 256):
    return get_efficientad_transforms(image_size=image_size, three_channel_from_gray=True)


def _subsample_by_class(dataset: EfficientADTestDataset, max_per_class: int, seed: int = 42) -> Subset | EfficientADTestDataset:
    """normal/anomaly 각각 max_per_class 장으로 균형 샘플링."""
    rng = np.random.default_rng(seed)
    by_label: dict[int, list[int]] = {0: [], 1: []}
    for i, label in enumerate(dataset.labels):
        by_label[label].append(i)
    selected: list[int] = []
    for label in (0, 1):
        idxs = by_label[label]
        if len(idxs) > max_per_class:
            idxs = rng.choice(idxs, size=max_per_class, replace=False).tolist()
        selected.extend(idxs)
    selected.sort()
    return Subset(dataset, selected)


def _build_test_loader(cfg: dict, input_dir: Optional[str] = None) -> DataLoader:
    data_cfg = cfg["data"]
    image_size = int(data_cfg.get("image_size", 256))
    inf_cfg = cfg.get("inference", {})
    test_dir = input_dir if input_dir else data_cfg["test_dir"]
    dataset = EfficientADTestDataset(
        root_dir=test_dir,
        transform=_get_test_transform(image_size),
        is_train=False,
    )
    max_per_class = inf_cfg.get("max_per_class")
    if max_per_class is not None:
        seed = int(cfg.get("seed", 42))
        dataset = _subsample_by_class(dataset, int(max_per_class), seed=seed)
        print(f"  max_per_class={max_per_class} (seed={seed}) → {len(dataset)} images")
    return DataLoader(dataset, batch_size=8, shuffle=False, num_workers=4, pin_memory=torch.cuda.is_available(),)


# =====================================================================
# 체크포인트 로드
# =====================================================================

def _load_model(cfg: dict, device: torch.device) -> EfficientAD:
    m = cfg["model"]
    model = EfficientAD.build_default(
        variant=m.get("variant", "S"),
        in_channels=int(m.get("in_channels", 3)),
        teacher_out=int(m.get("teacher_out_channels", 384)),
        student_out=int(m.get("student_out_channels", 768)),
        with_bn=bool(m.get("with_bn", False)),
    ).to(device)

    inf_cfg = cfg.get("inference", {})
    ckpt_path = inf_cfg.get("checkpoint", "")
    if not ckpt_path or not Path(ckpt_path).is_file():
        save_dir = cfg.get("training", {}).get("save_dir", "results/checkpoints/efficientad")
        ckpt_path = str(Path(save_dir) / "efficientad.pth")

    if not Path(ckpt_path).is_file():
        raise FileNotFoundError(
            f"체크포인트를 찾을 수 없습니다: {ckpt_path}\n"
            "train_efficientad.py로 학습을 먼저 실행하세요."
        )

    print(f"체크포인트 로드: {ckpt_path}")
    ckpt = torch.load(ckpt_path, map_location=device, weights_only=False)

    model.teacher.load_state_dict(ckpt["teacher"])
    model.student.load_state_dict(ckpt["student"])
    model.autoencoder.load_state_dict(ckpt["autoencoder"])

    if "teacher_feat_mu" in ckpt:
        model.set_teacher_feature_normalization(
            ckpt["teacher_feat_mu"].to(device),
            ckpt["teacher_feat_sigma"].to(device),
        )
    for key in ["q_a_st", "q_b_st", "q_a_ae", "q_b_ae", "calibrated",
                "score_alpha", "_score_agg_mode", "_score_topk_ratio"]:
        if key in ckpt:
            getattr(model, key).copy_(ckpt[key].to(device))

    print(f"  iteration={ckpt.get('iteration', '?')}, "
          f"calibrated={'Yes' if model.calibrated.item() else 'No'}")
    model.eval()
    return model


# =====================================================================
# 추론: 전체 test 데이터
# =====================================================================

@torch.no_grad()
def run_inference(model: EfficientAD, test_loader: DataLoader, device: torch.device, use_fp16: bool = False, store_maps: bool = True,) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[str], list[dict]]:
    """
    전체 test 데이터에 대해 추론.

    Returns:
        scores:        (N,) combined image-level anomaly scores
        local_scores:  (N,) local-only scores (S-T branch)
        global_scores: (N,) global_only scores (AE branch)
        labels:        (N,) ground truth (0=normal, 1=anomaly)
        paths:         (N,) file paths of test images
        map_list:      (N,) 각 이미지의 anomaly map dict
    """
    model.eval()
    all_scores, all_local, all_global = [], [], []
    all_labels, all_paths, map_list = [], [], []

    for images, labels, paths in test_loader:
        images = images.to(device)

        if use_fp16 and device.type == "cuda":
            with torch.amp.autocast(device_type="cuda"):
                out = model(images)
        else:
            out = model(images)

        all_scores.extend(out["image_score"].cpu().numpy())
        all_local.extend(out["local_score"].cpu().numpy())
        all_global.extend(out["global_score"].cpu().numpy())
        all_labels.extend(labels.numpy())
        all_paths.extend(paths)

        if store_maps:
            for i in range(images.shape[0]):
                map_list.append({
                    "local_map":    out["local_map"][i, 0].cpu().numpy(),
                    "global_map":   out["global_map"][i, 0].cpu().numpy(),
                    "combined_map": out["combined_map"][i, 0].cpu().numpy(),
                })

    return (
        np.array(all_scores),
        np.array(all_local),
        np.array(all_global),
        np.array(all_labels),
        all_paths,
        map_list,
    )


# =====================================================================
# 추론: 단일 이미지
# =====================================================================

@torch.no_grad()
def run_single_inference(model: EfficientAD, image_path: str, device: torch.device, image_size: int = 256,) -> dict:
    model.eval()
    transform = _get_test_transform(image_size)
    image = Image.open(image_path)
    if image.mode != "L":
        image = image.convert("L")
    tensor = transform(image).unsqueeze(0).to(device)

    out = model(tensor)
    return {
        "score":        out["image_score"].item(),
        "local_score":  out["local_score"].item(),
        "global_score": out["global_score"].item(),
        "maps": {
            "local_map":   out["local_map"][0, 0].cpu().numpy(),
            "global_map":  out["global_map"][0, 0].cpu().numpy(),
            "combined_map": out["combined_map"][0, 0].cpu().numpy(),
        },
        "image": np.array(image),
    }


# =====================================================================
# Heatmap 저장
# =====================================================================

def save_heatmaps(map_list: list[dict], scores: np.ndarray, labels: np.ndarray, paths: list[str], output_dir: Path, num_samples: int = 20,) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    sorted_idx = np.argsort(scores)[::-1]
    saved = 0
    for rank, idx in enumerate(sorted_idx):
        if saved >= num_samples:
            break
        label = "anomaly" if labels[idx] == 1 else "normal"
        try:
            orig = np.array(Image.open(paths[idx]).convert("RGB"))
        except Exception:
            continue
        save_path = output_dir / f"rank{rank:03d}_{label}_score{scores[idx]:.4f}.png"
        plot_efficientad_maps(orig, map_list[idx], save_path=str(save_path))
        saved += 1
    print(f"  Heatmap {saved}개 저장: {output_dir}")


def save_fp_fn_heatmaps(map_list: list[dict], scores: np.ndarray, labels: np.ndarray, paths: list[str],
                        threshold: float, output_dir: Path, n_each: int = 20,) -> None:
    """
    FP(정상→이상 오분류) / FN(이상→정상 오분류) heatmap을 분리 저장.

    FP 폴더: 정상인데 score가 높은 순 (threshold 초과)
    FN 폴더: 이상인데 score가 낮은 순 (threshold 미만)
    → local/global map에서 어느 브랜치가 오반응하는지 시각 확인용.
    """
    fp_dir = output_dir / "fp_false_positive"
    fn_dir = output_dir / "fn_false_negative"
    fp_dir.mkdir(parents=True, exist_ok=True)
    fn_dir.mkdir(parents=True, exist_ok=True)

    pred = (scores >= threshold).astype(int)
    fp_idx = np.where((labels == 0) & (pred == 1))[0]
    fn_idx = np.where((labels == 1) & (pred == 0))[0]
    fp_sorted = fp_idx[np.argsort(scores[fp_idx])[::-1]]
    fn_sorted = fn_idx[np.argsort(scores[fn_idx])]

    def _save_cases(indices, save_dir, prefix):
        saved = 0
        for rank, idx in enumerate(indices[:n_each]):
            score = scores[idx]
            maps = map_list[idx]
            try:
                orig = np.array(Image.open(paths[idx]).convert("RGB"))
            except Exception:
                continue
            sp = save_dir / f"{prefix}_rank{rank:03d}_score{score:.4f}.png"
            plot_efficientad_maps(orig, maps, save_path=str(sp))
            saved += 1
        return saved

    n_fp = _save_cases(fp_sorted, fp_dir, "fp")
    n_fn = _save_cases(fn_sorted, fn_dir, "fn")
    print(f"  FP heatmap {n_fp}개 (전체 FP={len(fp_idx)}): {fp_dir}")
    print(f"  FN heatmap {n_fn}개 (전체 FN={len(fn_idx)}): {fn_dir}")


def print_threshold_sweep(scores: np.ndarray, labels: np.ndarray, n_steps: int = 20,) -> None:
    """
    threshold를 sweep하며 FP/FN/Precision/Recall 변화 출력.
    AUC가 낮을 때 임계값·스코어 스케일 문제 파악에 유용.
    """
    thresholds = np.linspace(scores.min(), scores.max(), n_steps + 2)[1:-1]
    print("\n--- Threshold Sweep ---")
    print(f"  {'Threshold':>10}  {'Acc':>6}  {'Prec':>6}  {'Rec':>6}  {'F1':>6}  {'FP':>5}  {'FN':>5}")
    print(f"  {'-'*62}")
    for thr in thresholds:
        pred = (scores >= thr).astype(int)
        tp = int(((pred == 1) & (labels == 1)).sum())
        fp = int(((pred == 1) & (labels == 0)).sum())
        fn = int(((pred == 0) & (labels == 1)).sum())
        tn = int(((pred == 0) & (labels == 0)).sum())
        acc  = (tp + tn) / max(len(labels), 1)
        prec = tp / max(tp + fp, 1)
        rec  = tp / max(tp + fn, 1)
        f1   = 2 * prec * rec / max(prec + rec, 1e-8)
        print(f"  {thr:>10.4f}  {acc:>6.3f}  {prec:>6.3f}  {rec:>6.3f}  {f1:>6.3f}  {fp:>5d}  {fn:>5d}")


# =====================================================================
# 메인
# =====================================================================

def evaluate(cfg: dict, threshold_override: Optional[float] = None, alpha: Optional[float] = None, agg: Optional[str] = None,
             topk_ratio: float = 0.01, save_fp_heatmaps: bool = False, threshold_sweep: bool = False, input_dir: Optional[str] = None,) -> dict:
    """전체 평가 파이프라인."""
    device = torch.device("cuda" if cfg.get("device") == "cuda" and torch.cuda.is_available() else "cpu")
    print(f"Device: {device}\n")

    model = _load_model(cfg, device)

    # 스코어 집계 파라미터: CLI > config > 모델 기본값 순
    inf_cfg_score = cfg.get("inference", {})
    _alpha = alpha if alpha is not None else inf_cfg_score.get("score_alpha", None)
    _agg   = agg   if agg   is not None else inf_cfg_score.get("score_agg",   None)
    _topk  = topk_ratio if (agg is not None) else float(inf_cfg_score.get("score_topk_ratio", topk_ratio))
    if _alpha is not None or _agg is not None:
        model.set_score_params(
            alpha=float(_alpha) if _alpha is not None else 0.5,
            agg=_agg if _agg is not None else "max",
            topk_ratio=_topk,
        )
        _agg_name = {0: "max", 1: "topk_mean", 2: "mean"}.get(
            int(model._score_agg_mode.item()), "max"
        )
        print(f"  Score params: alpha={model.score_alpha.item():.2f}, "
              f"agg={_agg_name}, topk_ratio={model._score_topk_ratio.item():.3f}")

    print("\n--- Test 데이터 로드 ---")
    test_loader = _build_test_loader(cfg, input_dir=input_dir)
    print(f"  Test images: {len(test_loader.dataset)}")

    print("\n--- 추론 ---")
    inf_cfg = cfg.get("inference", {})
    use_fp16    = bool(inf_cfg.get("use_fp16",    False))
    save_heatmap = bool(inf_cfg.get("save_heatmap", True))
    need_maps = save_heatmap or save_fp_heatmaps
 
    t0 = time.time()
    scores, local_scores, global_scores, labels, paths, map_list = run_inference(
        model, test_loader, device, use_fp16, store_maps=need_maps
    )
    elapsed = time.time() - t0
    n_images = len(scores)
    print(f"  {n_images}개 이미지, {elapsed:.2f}초 ({n_images/elapsed:.1f} img/s)")
    print(f"  Score 범위: combined=[{scores.min():.6f}, {scores.max():.6f}]  "
          f"local=[{local_scores.min():.6f}, {local_scores.max():.6f}]  "
          f"global=[{global_scores.min():.6f}, {global_scores.max():.6f}]")

    print("\n--- 평가 (Combined) ---")
    eval_cfg = cfg.get("evaluation", {})
    if threshold_override is not None:
        threshold = threshold_override
    elif eval_cfg.get("threshold") == "auto" or eval_cfg.get("threshold") is None:
        threshold = None
    else:
        threshold = float(eval_cfg["threshold"])
 
    metrics = compute_metrics(scores, labels, threshold=threshold)
    print_metrics(metrics)

    # Local / Global / Combined AUC 분리
    if len(np.unique(labels)) > 1:
        from sklearn.metrics import roc_auc_score
        auc_local    = float(roc_auc_score(labels, local_scores))
        auc_global   = float(roc_auc_score(labels, global_scores))
        auc_combined = metrics.get("auc", float("nan"))
        print("\n--- Score 분리 AUC (원인 진단용) ---")
        print(f"  Local  (S-T branch):   {auc_local:.4f}")
        print(f"  Global (AE-ST branch): {auc_global:.4f}")
        print(f"  Combined (0.5+0.5):    {auc_combined:.4f}")
        metrics["auc_local"]  = auc_local
        metrics["auc_global"] = auc_global

    # 시각화 저장
    viz_dir = Path(eval_cfg.get("visualize", {}).get("save_dir", "results/visualizations/efficientad"))
    viz_dir.mkdir(parents=True, exist_ok=True)

    normal_scores  = scores[labels == 0]
    anomaly_scores = scores[labels == 1]
    if len(normal_scores) > 0 and len(anomaly_scores) > 0:
        plot_score_distribution(
            normal_scores, anomaly_scores,
            threshold=metrics["threshold"],
            save_path=str(viz_dir / "score_distribution.png"),
        )

    # ROC curve
    if len(np.unique(labels)) > 1:
        plot_roc_curve(labels, scores, save_path=str(viz_dir / "roc_curve.png"))

    # Heatmap (score 높은 순)
    heatmap_dir = Path(inf_cfg.get("output_dir", "results/predictions")) / "efficientad_heatmaps"
    if save_heatmap:
        if map_list:
            num_samples = eval_cfg.get("visualize", {}).get("num_samples", 20)
            save_heatmaps(map_list, scores, labels, paths, heatmap_dir, num_samples)
        else:
            print("  [경고] save_heatmap=True 이지만 map_list가 비어있습니다.")

    # FP/FN 분리 heatmap
    if save_fp_heatmaps:
        if map_list:
            print("\n--- FP / FN Heatmap 저장 ---")
            save_fp_fn_heatmaps(
                map_list, scores, labels, paths,
                threshold=metrics["threshold"],
                output_dir=heatmap_dir,
                n_each=20,
            )
        else:
            print("  [경고] save_fp_heatmaps=True 이지만 map_list가 비어있습니다.")
 
    if threshold_sweep and len(np.unique(labels)) > 1:
        print_threshold_sweep(scores, labels)

    return {
        **metrics,
        "scores": scores,
        "local_scores": local_scores,
        "global_scores": global_scores,
        "labels": labels,
        "paths": paths,
        "map_list": map_list,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="EfficientAD Inference & Evaluation")
    parser.add_argument(
        "--config", type=str,
        default=str(PROJECT_ROOT / "configs" / "config_efficientad.yaml"),
    )
    parser.add_argument(
        "--checkpoint", type=str, default=None,
        help="평가할 체크포인트 경로 오버라이드 (예: results/checkpoints/efficientad/efficientad_roi.pth). "
             "미지정 시 config의 inference.checkpoint 사용.",
    )
    parser.add_argument(
        "--threshold", type=float, default=None,
        help="수동 threshold 지정 (미지정 시 auto: F1 최대화)",
    )
    parser.add_argument(
        "--image", type=str, default=None,
        help="단일 이미지 추론 모드 (test 평가 대신)",
    )
    parser.add_argument(
        "--max_per_class", type=int, default=None,
        help="normal/anomaly 각각 최대 몇 장만 평가할지 (파이프라인 체크용)",
    )
    parser.add_argument(
        "--alpha", type=float, default=None,
        help="local 브랜치 가중치 (0.0~1.0). combined = alpha*local + (1-alpha)*global",
    )
    parser.add_argument(
        "--agg", type=str, default=None,
        choices=["max", "topk_mean", "mean"],
        help="이미지 스코어 집계 방식. max(기본)|topk_mean(상위k 평균)|mean(전체평균)",
    )
    parser.add_argument(
        "--topk_ratio", type=float, default=0.01,
        help="--agg topk_mean 시 상위 비율 픽셀 평균 (기본 0.01 = 상위 1%%)",
    )
    parser.add_argument(
        "--save_fp_heatmaps", action="store_true",
        help="FP/FN heatmap 분리 저장 (어느 브랜치가 오반응하는지 시각 확인)",
    )
    parser.add_argument(
        "--threshold_sweep", action="store_true",
        help="threshold를 sweep하며 FP/FN/Precision/Recall 변화 출력",
    )
    parser.add_argument(
        "--input_dir", type=str, default=None,
        help="평가할 데이터 디렉토리 (미지정 시 config의 data.test_dir 사용)",
    )
    args = parser.parse_args()
 
    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    print(f"Config: {args.config}\n")
 
    if args.max_per_class is not None:
        cfg.setdefault("inference", {})
        cfg["inference"]["max_per_class"] = int(args.max_per_class)

    if args.checkpoint:
        cfg.setdefault("inference", {})
        cfg["inference"]["checkpoint"] = args.checkpoint
        print(f"Checkpoint override: {args.checkpoint}")
 
    if args.image:
        device = torch.device("cuda" if cfg.get("device") == "cuda" and torch.cuda.is_available() else "cpu")
        model = _load_model(cfg, device)
        result = run_single_inference(
            model, args.image, device,
            image_size=int(cfg["data"].get("image_size", 256)),
        )
        print(f"\n이미지: {args.image}")
        print(f"Anomaly Score (combined): {result['score']:.6f}")
        print(f"  Local  score (S-T):     {result['local_score']:.6f}")
        print(f"  Global score (AE-ST):   {result['global_score']:.6f}")
 
        out_dir = Path(cfg.get("inference", {}).get("output_dir", "results/predictions"))
        out_dir.mkdir(parents=True, exist_ok=True)
        plot_efficientad_maps(
            result["image"], result["maps"],
            save_path=str(out_dir / f"single_{Path(args.image).stem}.png"),
        )
    else:
        evaluate(
            cfg,
            threshold_override=args.threshold,
            alpha=args.alpha,
            agg=args.agg,
            topk_ratio=args.topk_ratio,
            save_fp_heatmaps=args.save_fp_heatmaps,
            threshold_sweep=args.threshold_sweep,
            input_dir=args.input_dir,
        )
 
 
if __name__ == "__main__":
    main()
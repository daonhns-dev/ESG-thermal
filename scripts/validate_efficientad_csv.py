"""
EfficientAD 추론 결과를 CSV 온도 데이터와 대조해 검증합니다.

모델 히트맵이 실제 고온 영역과 얼마나 일치하는지, 온도만으로 분류할 때와
비교해 AUC·상관계수·FP/FN 온도 프로필을 출력합니다.

사용법 (inference와 동일 인자):
  python scripts/validate_efficientad_csv.py --config configs/config_efficientad.yaml
  python scripts/validate_efficientad_csv.py --config configs/config_efficientad.yaml \\
      --max_per_class 5000 --agg topk_mean --topk_ratio 0.15 --alpha 0.3 --threshold -0.001159

또는 inference에 --validate_csv 플래그 사용.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
import yaml
from PIL import Image
from sklearn.metrics import roc_auc_score

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datasets.dataset import load_thermal_csv  # noqa: E402
from scripts.inference_efficientad import evaluate  # noqa: E402


def resolve_csv_path(image_path: str | Path, data_dir: Path, csv_dir: Path) -> Optional[Path]:
    """data/test/anomaly/foo.jpg → data/csv/test/anomaly/foo.csv"""
    image_path = Path(image_path).resolve()
    data_dir = data_dir.resolve()
    csv_dir = csv_dir.resolve()

    try:
        rel = image_path.relative_to(data_dir)
        parts = rel.parts
        if len(parts) >= 3 and parts[0] in ("train", "val", "test"):
            candidate = csv_dir / parts[0] / parts[1] / f"{image_path.stem}.csv"
            if candidate.is_file():
                return candidate
    except ValueError:
        pass

    flat = csv_dir / f"{image_path.stem}.csv"
    return flat if flat.is_file() else None


def _resize_temp(temp: np.ndarray, size: int = 256) -> np.ndarray:
    arr = temp.astype(np.float32)
    if np.isnan(arr).any():
        fill = float(np.nanmedian(arr))
        arr = np.where(np.isnan(arr), fill, arr)

    lo, hi = float(arr.min()), float(arr.max())
    if hi - lo < 1e-6:
        return np.full((size, size), lo, dtype=np.float32)

    norm = (arr - lo) / (hi - lo)
    img = Image.fromarray((norm * 255).astype(np.uint8))
    resized = np.array(img.resize((size, size), Image.BILINEAR), dtype=np.float32)
    return resized / 255.0 * (hi - lo) + lo


def _temp_hotspot_map(temp_256: np.ndarray) -> np.ndarray:
    """중앙값 대비 양의 편차를 0~1로 정규화한 온도 이상 맵."""
    med = float(np.median(temp_256))
    std = float(np.std(temp_256)) + 1e-6
    excess = np.clip((temp_256 - med) / std, 0.0, None)
    peak = float(excess.max())
    if peak <= 0:
        return excess
    return excess / peak


def _aggregate_score(values: np.ndarray, topk_ratio: float) -> float:
    flat = values.reshape(-1)
    k = max(1, int(len(flat) * topk_ratio))
    return float(np.sort(flat)[-k:].mean())


def _spatial_correlation(a: np.ndarray, b: np.ndarray) -> float:
    x = a.reshape(-1).astype(np.float64)
    y = b.reshape(-1).astype(np.float64)
    if x.std() < 1e-8 or y.std() < 1e-8:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def _load_gray_preview(image_path: str, size: int = 256) -> np.ndarray:
    img = Image.open(image_path).convert("L")
    return np.array(img.resize((size, size), Image.BILINEAR))


def plot_csv_validation(
    gray: np.ndarray,
    model_map: np.ndarray,
    temp_256: np.ndarray,
    temp_hotspot: np.ndarray,
    title: str,
    save_path: Path,
) -> None:
    fig, axes = plt.subplots(1, 5, figsize=(22, 4.5))

    axes[0].imshow(gray, cmap="gray")
    axes[0].set_title("Input (gray)")
    axes[0].axis("off")

    im1 = axes[1].imshow(model_map, cmap="jet")
    axes[1].set_title("Model Combined")
    axes[1].axis("off")
    plt.colorbar(im1, ax=axes[1], fraction=0.046)

    im2 = axes[2].imshow(temp_256, cmap="inferno")
    axes[2].set_title("CSV Temp (°C)")
    axes[2].axis("off")
    plt.colorbar(im2, ax=axes[2], fraction=0.046)

    im3 = axes[3].imshow(temp_hotspot, cmap="jet")
    axes[3].set_title("Temp Hotspot")
    axes[3].axis("off")
    plt.colorbar(im3, ax=axes[3], fraction=0.046)

    overlay = 0.5 * model_map + 0.5 * temp_hotspot
    im4 = axes[4].imshow(overlay, cmap="jet")
    axes[4].set_title("Overlay")
    axes[4].axis("off")
    plt.colorbar(im4, ax=axes[4], fraction=0.046)

    fig.suptitle(title, fontsize=10)
    plt.tight_layout()
    save_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close()


def run_csv_validation(
    cfg: dict,
    *,
    threshold_override: Optional[float] = None,
    alpha: Optional[float] = None,
    agg: Optional[str] = None,
    topk_ratio: float = 0.15,
    input_dir: Optional[str] = None,
    csv_dir: Optional[str] = None,
    data_dir: Optional[str] = None,
    num_viz: int = 20,
    save_fp_fn_viz: bool = True,
) -> dict:
    """추론 실행 후 CSV 온도 기반 검증."""
    data_root = Path(data_dir or cfg.get("data", {}).get("train_dir", "data/train")).parent
    csv_root = Path(csv_dir or data_root / "csv")

    print("\n" + "=" * 60)
    print("  EfficientAD + CSV 온도 검증")
    print("=" * 60)

    result = evaluate(
        cfg,
        threshold_override=threshold_override,
        alpha=alpha,
        agg=agg,
        topk_ratio=topk_ratio,
        input_dir=input_dir,
        save_fp_heatmaps=False,
    )

    scores = result["scores"]
    labels = np.array(result["labels"])
    paths = result["paths"]
    map_list = result["map_list"]
    threshold = float(result["threshold"])
    preds = (scores >= threshold).astype(int)

    rows = []
    missing_csv = 0
    spatial_corrs = []

    for i, path in enumerate(paths):
        csv_path = resolve_csv_path(path, data_root, csv_root)
        row = {
            "path": path,
            "label": int(labels[i]),
            "model_score": float(scores[i]),
            "model_pred": int(preds[i]),
            "csv_path": str(csv_path) if csv_path else "",
            "temp_max": float("nan"),
            "temp_mean": float("nan"),
            "temp_p95": float("nan"),
            "temp_score": float("nan"),
            "spatial_corr": float("nan"),
        }

        if csv_path is None:
            missing_csv += 1
            rows.append(row)
            continue

        temp = load_thermal_csv(csv_path)
        if temp.size == 0:
            missing_csv += 1
            rows.append(row)
            continue

        temp_256 = _resize_temp(temp, size=256)
        temp_hotspot = _temp_hotspot_map(temp_256)
        temp_score = _aggregate_score(temp_hotspot, topk_ratio)

        model_map = map_list[i]["combined_map"] if map_list else None
        spatial = float("nan")
        if model_map is not None:
            if model_map.shape != temp_hotspot.shape:
                mh, mw = model_map.shape
                temp_hotspot_rs = np.array(
                    Image.fromarray(temp_hotspot).resize((mw, mh), Image.BILINEAR)
                )
            else:
                temp_hotspot_rs = temp_hotspot
            spatial = _spatial_correlation(model_map, temp_hotspot_rs)
            spatial_corrs.append(spatial)

        row.update({
            "temp_max": float(np.nanmax(temp_256)),
            "temp_mean": float(np.nanmean(temp_256)),
            "temp_p95": float(np.nanpercentile(temp_256, 95)),
            "temp_score": temp_score,
            "spatial_corr": spatial,
        })
        rows.append(row)

    matched = [r for r in rows if r["csv_path"]]
    temp_scores = np.array([r["temp_score"] for r in matched])
    matched_labels = np.array([r["label"] for r in matched])
    model_scores_m = np.array([r["model_score"] for r in matched])

    print("\n--- CSV 매칭 ---")
    print(f"  전체 이미지: {len(paths)}")
    print(f"  CSV 매칭:    {len(matched)}")
    print(f"  CSV 없음:    {missing_csv}")

    summary = {
        "threshold": threshold,
        "n_images": len(paths),
        "n_csv_matched": len(matched),
        "n_csv_missing": missing_csv,
        "model_auc": result.get("auc"),
        "temp_auc": None,
        "score_corr_model_temp": None,
        "mean_spatial_corr": None,
    }

    if len(matched) > 1 and len(np.unique(matched_labels)) > 1:
        temp_auc = float(roc_auc_score(matched_labels, temp_scores))
        score_corr = float(np.corrcoef(model_scores_m, temp_scores)[0, 1])
        mean_spatial = float(np.nanmean(spatial_corrs)) if spatial_corrs else float("nan")

        print("\n--- 온도 vs 모델 비교 ---")
        print(f"  Model AUC (combined):     {summary['model_auc']:.4f}")
        print(f"  Temp hotspot AUC:         {temp_auc:.4f}")
        print(f"  Score corr (model↔temp):  {score_corr:.4f}")
        print(f"  Mean spatial corr (map):  {mean_spatial:.4f}")

        normal_t = temp_scores[matched_labels == 0]
        anomaly_t = temp_scores[matched_labels == 1]
        if len(normal_t) and len(anomaly_t):
            print(f"  Temp score normal mean:   {normal_t.mean():.4f}")
            print(f"  Temp score anomaly mean:  {anomaly_t.mean():.4f}")

        fp_rows = [r for r in matched if r["label"] == 0 and r["model_pred"] == 1]
        fn_rows = [r for r in matched if r["label"] == 1 and r["model_pred"] == 0]
        if fp_rows:
            fp_t = np.array([r["temp_p95"] for r in fp_rows])
            print(f"\n--- FP ({len(fp_rows)}건) 온도 프로필 ---")
            print(f"  temp_p95 mean: {np.nanmean(fp_t):.2f}°C")
        if fn_rows:
            fn_t = np.array([r["temp_p95"] for r in fn_rows])
            print(f"\n--- FN ({len(fn_rows)}건) 온도 프로필 ---")
            print(f"  temp_p95 mean: {np.nanmean(fn_t):.2f}°C")

        summary.update({
            "temp_auc": temp_auc,
            "score_corr_model_temp": score_corr,
            "mean_spatial_corr": mean_spatial,
            "fp_count": len(fp_rows),
            "fn_count": len(fn_rows),
        })

    out_dir = Path(cfg.get("inference", {}).get("output_dir", "results/predictions")).parent / "csv_validation"
    out_dir.mkdir(parents=True, exist_ok=True)

    report_csv = out_dir / "per_sample.csv"
    if matched:
        with report_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(matched[0].keys()))
            writer.writeheader()
            writer.writerows(matched)
        print(f"\n  per-sample CSV: {report_csv}")

    summary_path = out_dir / "summary.json"
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"  summary JSON:   {summary_path}")

    if map_list and num_viz > 0:
        viz_dir = out_dir / "samples"
        # 모델-온도 스코어 차이가 큰 순 + FP/FN 우선
        def _priority(r):
            if not r["csv_path"]:
                return -1
            gap = abs(r["model_score"] - r["temp_score"])
            bonus = 0.0
            if r["label"] == 0 and r["model_pred"] == 1:
                bonus += 1.0
            if r["label"] == 1 and r["model_pred"] == 0:
                bonus += 1.0
            return gap + bonus

        ranked = sorted(enumerate(rows), key=lambda x: _priority(x[1]), reverse=True)
        saved = 0
        for idx, row in ranked:
            if saved >= num_viz or not row["csv_path"]:
                continue
            csv_path = Path(row["csv_path"])
            temp = load_thermal_csv(csv_path)
            if temp.size == 0:
                continue
            temp_256 = _resize_temp(temp, 256)
            temp_hotspot = _temp_hotspot_map(temp_256)
            gray = _load_gray_preview(row["path"], 256)
            model_map = map_list[idx]["combined_map"]
            label = "anomaly" if row["label"] == 1 else "normal"
            pred = "anomaly" if row["model_pred"] == 1 else "normal"
            title = (
                f"{label}/{pred}  model={row['model_score']:.4f}  "
                f"temp={row['temp_score']:.4f}  corr={row['spatial_corr']:.3f}"
            )
            fname = f"rank{saved:03d}_{label}_pred{pred}_gap{abs(row['model_score']-row['temp_score']):.4f}.png"
            plot_csv_validation(gray, model_map, temp_256, temp_hotspot, title, viz_dir / fname)
            saved += 1
        print(f"  비교 heatmap:   {viz_dir} ({saved}개)")

    print("\n해석 가이드:")
    print("  - spatial_corr 높음: 모델 히트맵이 CSV 고온 영역과 공간적으로 일치")
    print("  - temp AUC << model AUC: 패턴 기반 탐지가 단순 온도보다 유효")
    print("  - FP인데 temp_p95 낮음: 구조/패턴 오경보 (온도로는 정상)")
    print("  - FN인데 temp_p95 높음: 온도는 높지만 모델이 놓침 → subtle anomaly")

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="EfficientAD 추론 + CSV 온도 검증")
    parser.add_argument("--config", type=str, default=str(PROJECT_ROOT / "configs" / "config_efficientad.yaml"))
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--input_dir", type=str, default=None)
    parser.add_argument("--csv_dir", type=str, default=None, help="CSV 루트 (기본: data/csv)")
    parser.add_argument("--data_dir", type=str, default=None, help="데이터 루트 (기본: data)")
    parser.add_argument("--max_per_class", type=int, default=None)
    parser.add_argument("--alpha", type=float, default=None)
    parser.add_argument("--agg", type=str, default=None, choices=["max", "topk_mean", "mean"])
    parser.add_argument("--topk_ratio", type=float, default=0.15)
    parser.add_argument("--num_viz", type=int, default=20, help="CSV 비교 시각화 저장 개수")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    if args.max_per_class is not None:
        cfg.setdefault("inference", {})
        cfg["inference"]["max_per_class"] = int(args.max_per_class)

    run_csv_validation(
        cfg,
        threshold_override=args.threshold,
        alpha=args.alpha,
        agg=args.agg,
        topk_ratio=args.topk_ratio,
        input_dir=args.input_dir,
        csv_dir=args.csv_dir,
        data_dir=args.data_dir,
        num_viz=args.num_viz,
    )


if __name__ == "__main__":
    main()

"""temp_anomaly_synthetic_sensitivity.py 의 로컬 z-score 탐지 로직을 픽셀맵 전체로
확장해, 실제로 "어디를 이상으로 짚는지" 시각화.

주의: 이건 학습된(가중치가 있는) 모델이 아니라 순수 통계 공식(배경 대비 잔차의robust z-score)이다. 
"추론"이라기보단 "같은 공식을 프레임의 모든 픽셀에 적용"에 가깝다 — 그래도 결과 해석 방식은 학습 기반 이상탐지의 히트맵과 동일하게 보면 된다.

모드 두 가지:
  --mode synthetic : 정상 프레임에 합성 hotspot(delta, radius)을 주입하고,
                      z-score 히트맵이 실제 주입 위치를 잘 짚는지 확인 (정답 위치를 아는 검증용).
  --mode danger    : 117 hv_motor의 실제 status=danger 라벨 프레임에 그대로 적용해,
                      z-score 히트맵이 라벨된 bbox와 얼마나 겹치는지 확인 (정답 '이유'는
                      모르지만( §8-18-1 ) 위치 정합 정도는 볼 수 있음).

결과: results/local_anomaly_maps/ 에 프레임별 PNG
  [원본 온도맵 | z-score 히트맵 | z-score>threshold 오버레이(+실제 bbox 있으면 빨간 박스)]

사용법 (thermal/image/ 에서 실행):
    python CNN/visualize_local_anomaly_map.py --mode synthetic --n 8 --delta 15
    python CNN/visualize_local_anomaly_map.py --mode danger --n 8
"""
import argparse
import glob
import json
import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from temp_anomaly_synthetic_sensitivity import (
    IMAGE_ROOT as DEFAULT_IMAGE_ROOT,
    LABEL_ROOT as DEFAULT_LABEL_ROOT,
    bg_config_label,
    compute_background,
    find_normal_csv_paths,
    make_bump,
    parse_bg_configs,
    parse_temp_csv,
)

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "results", "local_anomaly_maps")


def find_danger_samples(label_root, image_root, limit=None):
    """status=danger 프레임의 (csv경로, bbox리스트) 반환."""
    samples = []
    for ann_path in glob.glob(os.path.join(label_root, "*.json")):
        try:
            with open(ann_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if data.get("metadata", {}).get("status") != "danger":
            continue
        csv_name = data.get("csv", {}).get("filename_csv")
        if not csv_name:
            continue
        csv_path = os.path.join(image_root, csv_name)
        if not os.path.exists(csv_path):
            continue
        boxes = []
        for annotation in data.get("annotations", []):
            if isinstance(annotation, dict) and isinstance(annotation.get("data"), dict):
                boxes.append(annotation["data"])
        samples.append((csv_path, boxes))
        if limit and len(samples) >= limit:
            break
    return samples


def z_score_map(grid, method, param):
    background = compute_background(grid, method, param)
    residual = grid - background
    med = np.median(residual)
    mad = np.median(np.abs(residual - med)) * 1.4826 + 1e-6
    return (residual - med) / mad


def save_panel(grid, zmaps, z_thresh, out_path, title, boxes=None, inject_center=None, inject_radius=None, roi_center=None, roi_half=80, dpi=130):
    """
    zmaps: [(label, zmap), ...] — 배경 추정 방식별로 한 행씩 비교.
    roi_center 주면 4번째 열에 그 주변을 확대(zoom)한 z-score crop을 추가로 보여줌
    -> 작은 원(반경 15px)이 640px 프레임에서 너무 작아 육안으로 안 보이는 문제 방지.
    """
    n = len(zmaps)
    ncols = 4 if roi_center is not None else 3
    fig, axes = plt.subplots(n, ncols, figsize=(5 * ncols, 5 * n), squeeze=False)

    if roi_center is not None:
        cy, cx = roi_center
        y0, y1 = max(0, cy - roi_half), min(grid.shape[0], cy + roi_half)
        x0, x1 = max(0, cx - roi_half), min(grid.shape[1], cx + roi_half)

    for row, (label, zmap) in enumerate(zmaps):
        im0 = axes[row][0].imshow(grid, cmap="inferno")
        axes[row][0].set_aspect("equal", adjustable="box")
        axes[row][0].set_title(f"[{label}] raw temperature (C)")
        plt.colorbar(im0, ax=axes[row][0], fraction=0.046)

        im1 = axes[row][1].imshow(zmap, cmap="coolwarm", vmin=-5, vmax=5)
        axes[row][1].set_aspect("equal", adjustable="box")
        axes[row][1].set_title(f"[{label}] local z-score map")
        plt.colorbar(im1, ax=axes[row][1], fraction=0.046)

        axes[row][2].imshow(grid, cmap="gray")
        axes[row][2].set_aspect("equal", adjustable="box")
        detected = zmap > z_thresh
        overlay = np.zeros((*zmap.shape, 4))
        overlay[detected] = [1, 0, 0, 0.5]  
        axes[row][2].imshow(overlay)
        axes[row][2].set_title(f"[{label}] detection overlay (z>{z_thresh})")

        if boxes:
            for b in boxes:
                for ax in (axes[row][0], axes[row][2]):
                    rect = plt.Rectangle((b["x"], b["y"]), b["width"], b["height"], fill=False, edgecolor="lime", linewidth=2)
                    ax.add_patch(rect)
        if inject_center is not None:
            for ax in (axes[row][0], axes[row][2]):
                circ = plt.Circle((inject_center[1], inject_center[0]), inject_radius, fill=False, edgecolor="cyan", linewidth=2, linestyle="--")
                ax.add_patch(circ)

        if roi_center is not None:
            zoom = zmap[y0:y1, x0:x1]
            im3 = axes[row][3].imshow(zoom, cmap="coolwarm", vmin=-5, vmax=5, extent=[x0, x1, y1, y0])
            axes[row][3].set_aspect("equal", adjustable="box")
            axes[row][3].set_title(f"[{label}] ROI zoom (z-score, "
                                    f"peak={zoom.max():.1f})")
            plt.colorbar(im3, ax=axes[row][3], fraction=0.046)
            if inject_center is not None:
                circ = plt.Circle((inject_center[1], inject_center[0]), inject_radius, fill=False, edgecolor="cyan", linewidth=2, linestyle="--")
                axes[row][3].add_patch(circ)
            if boxes:
                for b in boxes:
                    rect = plt.Rectangle((b["x"], b["y"]), b["width"], b["height"], fill=False, edgecolor="lime", linewidth=2)
                    axes[row][3].add_patch(rect)

    fig.suptitle(title)
    plt.tight_layout(rect=[0, 0, 1, 0.98])
    plt.savefig(out_path, dpi=dpi)
    plt.close(fig)


def run_synthetic(args, rng, bg_configs, label_root, image_root, out_dir):
    paths = find_normal_csv_paths(label_root, image_root, limit=args.n)
    print(f"normal CSV {len(paths)}개 사용")
    if not paths:
        print("사용 가능한 normal CSV가 없습니다.")
        return
    for i, p in enumerate(paths):
        grid = parse_temp_csv(p, allow_bad_rows=args.allow_bad_rows)
        if grid is None:
            continue
        h, w = grid.shape
        cy = rng.integers(args.radius, h - args.radius)
        cx = rng.integers(args.radius, w - args.radius)
        bump = make_bump(grid.shape, cy, cx, args.radius, args.delta)
        injected = grid + bump
        zmaps = [(bg_config_label(m, p_), z_score_map(injected, m, p_)) for m, p_ in bg_configs]
        out_path = os.path.join(out_dir, f"synthetic_{i:03d}_delta{args.delta:g}.png")
        save_panel(injected, zmaps, args.z_thresh, out_path,
                   title=f"synthetic +{args.delta}C @ ({cy},{cx})  [{os.path.basename(p)}]",
                   inject_center=(cy, cx), inject_radius=args.radius,
                   roi_center=(cy, cx), roi_half=max(60, args.radius * 4), dpi=args.dpi)
        print("saved:", out_path)


def run_danger(args, bg_configs, label_root, image_root, out_dir):
    samples = find_danger_samples(label_root, image_root, limit=args.n)
    print(f"danger CSV {len(samples)}개 사용")
    if not samples:
        print("사용 가능한 danger CSV가 없습니다.")
        return
    for i, (p, boxes) in enumerate(samples):
        grid = parse_temp_csv(p, allow_bad_rows=args.allow_bad_rows)
        if grid is None:
            continue
        zmaps = [(bg_config_label(m, p_), z_score_map(grid, m, p_)) for m, p_ in bg_configs]
        out_path = os.path.join(out_dir, f"danger_{i:03d}.png")
        roi_center = None
        if boxes:
            b0 = boxes[0]
            roi_center = (int(b0["y"] + b0["height"] / 2), int(b0["x"] + b0["width"] / 2))
        save_panel(grid, zmaps, args.z_thresh, out_path,
                   title=f"danger label (green=labeled bbox)  [{os.path.basename(p)}]",
                   boxes=boxes, roi_center=roi_center, roi_half=100, dpi=args.dpi)
        print("saved:", out_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["synthetic", "danger"], required=True)
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--radius", type=int, default=15, help="synthetic 모드: 주입 hotspot 반경(px)")
    ap.add_argument("--delta", type=float, default=15.0, help="synthetic 모드: 주입 온도(C)")
    ap.add_argument("--bg_configs", type=str, nargs="+",
                     default=["gaussian:45", "gaussian:120", "median:91"],
                     help="배경 추정 방식 비교 목록. 'method:param' 형식"
                          " (gaussian:sigma 또는 median:kernel_size)")
    ap.add_argument("--z_thresh", type=float, default=3.0, help="탐지 오버레이 임계 z-score")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--label_root", type=str, default=DEFAULT_LABEL_ROOT, help="라벨 JSON이 있는 루트 디렉터리")
    ap.add_argument("--image_root", type=str, default=DEFAULT_IMAGE_ROOT, help="CSV 온도 파일이 있는 루트 디렉터리")
    ap.add_argument("--out_dir", type=str, default=OUT_DIR, help="결과 PNG를 저장할 디렉터리")
    ap.add_argument("--dpi", type=int, default=130, help="저장할 이미지의 DPI")
    ap.add_argument("--allow_bad_rows", action="store_true", help="CSV 파싱 실패 행이 있어도 스킵하고 계속 진행")
    args = ap.parse_args()
    bg_configs = parse_bg_configs(args.bg_configs)

    label_root = os.path.abspath(os.path.expanduser(args.label_root))
    image_root = os.path.abspath(os.path.expanduser(args.image_root))
    out_dir = os.path.abspath(os.path.expanduser(args.out_dir))

    os.makedirs(out_dir, exist_ok=True)
    rng = np.random.default_rng(args.seed)

    if args.mode == "synthetic":
        run_synthetic(args, rng, bg_configs, label_root, image_root, out_dir)
    else:
        run_danger(args, bg_configs, label_root, image_root, out_dir)

    print(f"\n결과 -> {out_dir}")


if __name__ == "__main__":
    main()

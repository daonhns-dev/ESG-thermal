"""
차량 이동 촬영 열화상 세션(고정 배경 없음)에서, 프레임 하나하나 안에서 국소적으로
튀는 열원 후보를 찾아 "배관처럼 뾰족/원형(sharp)"인지 "그림자·반사 등 뭉툭한 확산
패턴(diffuse)"인지 형태 지표로 잠정 분류한다.

heat_pipe_shape_heuristic.py의 z_score_map/edge_sharpness/circularity 공식을 그대로
재사용하되, "이미 알고 있는 중심 좌표 하나"가 아니라 프레임 전체에서 z-score 임계값을
넘는 모든 연결영역(connected component)을 자동으로 찾도록 일반화했다.

주의: --z-thresh/--min-area/--sharp-*-thresh 기본값은 실측 검증 전 시작점일 뿐이다.
아직 정답 라벨(진짜 이상 위치)이 없으므로, 이 스크립트의 출력은 "후보"이지 "판정"이
아니다. candidates.csv를 사람이 --sample-viz로 뽑은 오버레이 PNG와 함께 검토해서
임계값을 조정해야 한다.

사용법 (heat-pipe/ 에서 실행):
    python scripts/experiments/detect_hotspot_candidates.py --session 20260813_133232
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import cv2
import numpy as np
from scipy.ndimage import label
from tqdm import tqdm

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CNN_DIR = PROJECT_ROOT / "CNN"
for p in (PROJECT_ROOT, CNN_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from visualize_local_anomaly_map import z_score_map  # noqa: E402
from heat_pipe_shape_heuristic import edge_sharpness, circularity  # noqa: E402
from datasets.att_atg_io import TEMP_SCALE  # noqa: E402

DEFAULT_DATASET_ROOT = r"E:\열수송관 모니터링 데이터\dataset"


def imwrite_unicode(path: Path, img: np.ndarray, ext: str) -> None:
    ok, buf = cv2.imencode(ext, img)
    if ok:
        path.write_bytes(buf.tobytes())


def colorize(frame_c: np.ndarray, lo: float, hi: float) -> np.ndarray:
    norm = np.clip((frame_c - lo) / max(hi - lo, 1e-6), 0, 1)
    return cv2.applyColorMap((norm * 255).astype(np.uint8), cv2.COLORMAP_JET)


def find_candidates(grid_c: np.ndarray, bg_method: str, bg_param: float, z_thresh: float, min_area: int, max_area_frac: float):
    z = z_score_map(grid_c, bg_method, bg_param)
    mask = z > z_thresh
    labeled, n = label(mask)
    max_area = grid_c.size * max_area_frac
    results = []
    for comp_id in range(1, n + 1):
        comp_mask = labeled == comp_id
        area = int(comp_mask.sum())
        if area < min_area or area > max_area:
            continue
        ys, xs = np.where(comp_mask)
        results.append({
            "bbox_x0": int(xs.min()), "bbox_y0": int(ys.min()),
            "bbox_x1": int(xs.max()), "bbox_y1": int(ys.max()),
            "area_px": area,
            "edge_sharpness": edge_sharpness(grid_c, comp_mask),
            "circularity": circularity(comp_mask),
            "mean_temp_c": float(grid_c[comp_mask].mean()),
            "peak_temp_c": float(grid_c[comp_mask].max()),
        })
    return results


def main():
    parser = argparse.ArgumentParser(description="세션 내 국소 열원 후보 탐지 및 형태(sharp/diffuse) 잠정 분류")
    parser.add_argument("--session", type=str, required=True, help="세션 이름 (dataset/<session>/thermal/*.npy 를 읽음)")
    parser.add_argument("--dataset-root", type=str, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--bg-method", choices=["gaussian", "median"], default="gaussian")
    parser.add_argument("--bg-param", type=float, default=45, help="gaussian=sigma, median=kernel size")
    parser.add_argument("--z-thresh", type=float, default=3.0, help="배경 대비 robust z-score 임계값 (미검증 시작값)")
    parser.add_argument("--min-area", type=int, default=20, help="이보다 작은 연결영역은 노이즈로 간주해 제외")
    parser.add_argument("--max-area-frac", type=float, default=0.25, help="프레임 전체 대비 이 비율보다 크면 제외 (조명 변화 등 프레임 전체성 아티팩트)")
    parser.add_argument("--sharp-circularity-thresh", type=float, default=0.3, help="미검증 시작값 - 이 이상이면 sharp 후보")
    parser.add_argument("--sharp-edge-thresh", type=float, default=1.0, help="미검증 시작값 (°C/px) - 이 이상이면 sharp 후보")
    parser.add_argument("--sample-viz", type=int, default=0, help="N프레임마다 후보 박스 오버레이 PNG 저장 (0=끔)")
    args = parser.parse_args()

    session_dir = Path(args.dataset_root) / args.session
    thermal_dir = session_dir / "thermal"
    if not thermal_dir.exists():
        raise FileNotFoundError(f"{thermal_dir} 없음 - build_rgb_thermal_dataset.py로 먼저 만들어야 함")

    npy_files = sorted(thermal_dir.glob("*.npy"), key=lambda p: int(p.stem))
    print(f"[{args.session}] {len(npy_files)}프레임 처리 (bg={args.bg_method}/{args.bg_param}, z>{args.z_thresh})")

    viz_dir = session_dir / "candidate_viz"
    if args.sample_viz > 0:
        viz_dir.mkdir(exist_ok=True)

    rows = []
    for npy_path in tqdm(npy_files, desc=args.session):
        idx = int(npy_path.stem)
        raw = np.load(npy_path)
        grid_c = raw.astype(np.float32) / TEMP_SCALE
        cands = find_candidates(grid_c, args.bg_method, args.bg_param, args.z_thresh, args.min_area, args.max_area_frac)
        for c in cands:
            shape_label = (
                "sharp" if c["circularity"] >= args.sharp_circularity_thresh and c["edge_sharpness"] >= args.sharp_edge_thresh
                else "diffuse"
            )
            rows.append({"frame_idx": idx, "shape_label": shape_label, **c})

        if args.sample_viz > 0 and idx % args.sample_viz == 0 and cands:
            lo, hi = np.percentile(grid_c, [1, 99])
            img = colorize(grid_c, lo, hi)
            for c in cands:
                color = (0, 0, 255) if c["circularity"] >= args.sharp_circularity_thresh and c["edge_sharpness"] >= args.sharp_edge_thresh else (255, 255, 0)
                cv2.rectangle(img, (c["bbox_x0"], c["bbox_y0"]), (c["bbox_x1"], c["bbox_y1"]), color, 1)
            imwrite_unicode(viz_dir / f"{idx:06d}.png", img, ".png")

    out_csv = session_dir / "candidates.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        if rows:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        else:
            f.write("")

    n_sharp = sum(1 for r in rows if r["shape_label"] == "sharp")
    print(f"완료: 후보 {len(rows)}개 (sharp {n_sharp} / diffuse {len(rows) - n_sharp}) -> {out_csv}")
    if args.sample_viz > 0:
        print(f"오버레이 시각화 -> {viz_dir}")


if __name__ == "__main__":
    main()

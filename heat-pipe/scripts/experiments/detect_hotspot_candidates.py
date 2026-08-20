"""
차량 이동 촬영 열화상 세션(고정 배경 없음)에서, 프레임 하나하나 안에서 국소적으로
튀는 열원 후보를 찾는다.

v1에서는 프레임 전체(하늘/나무/건물/다른 차량 포함)를 대상으로 "배관처럼 뾰족/원형
(sharp)인지 뭉툭한 확산(diffuse)인지"로 분류했는데, 실측 리뷰 결과 이 전제 자체가
틀렸다는 게 확인됐다:
  - 이 카메라는 배관을 근접 촬영하는 게 아니라 차량 앞유리에 달려 일반 도로를 찍는
    대시캠 형태다 (열수송관은 지하에 매설). 그래서 "sharp/원형" 기준이 신호등 알림판,
    차량 하부 엔진열, 차량 후미등 같은 도로 위 다른 물체를 그대로 잡아버렸다.
  - 실제로 찾아야 하는 신호는 아마 배관 결함이 아니라 "지하 배관 누수로 인해 데워진
    도로/지면의 국소 고온 패치"이고, 이건 sharp/원형이 아니라 오히려 경계가 흐릿하게
    퍼진 형태일 가능성이 높다.

v2 변경점:
  1. 프레임 하단(도로면으로 추정되는 부분)만 잘라서(ROI) 그 안에서만 분석 — 하늘/나무/
     신호등 등 도로가 아닌 영역을 애초에 대상에서 제외.
  2. 배경 추정(z_score_map)도 이 ROI 안에서만 계산 — 하늘처럼 아예 다른 온도대의
     배경과 섞여서 z-score가 왜곡되는 걸 방지.
  3. sharp/diffuse로 미리 걸러내지 않음 — 모양 지표(edge_sharpness/circularity)는
     참고용으로 계속 기록하되, ROI 내 고온 패치는 형태와 무관하게 전부 후보로 낸다.

heat_pipe_shape_heuristic.py의 z_score_map/edge_sharpness/circularity 공식은 그대로
재사용한다.

주의: ROI 비율/z-thresh/min-area 전부 아직 실측 검증 전 시작값이다. 정답 라벨이 없으므로
이 스크립트의 출력은 "후보"이지 "판정"이 아니다 — candidates.csv를 --sample-viz로 뽑은
오버레이 PNG와 함께 사람이 검토해서 계속 조정해야 한다.

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


def find_candidates(roi_grid_c: np.ndarray, roi_y0: int, bg_method: str, bg_param: float, z_thresh: float, min_area: int, max_area_frac: float):
    """roi_grid_c: 이미 하늘/나무 등을 잘라낸 ROI(도로면 추정 영역)의 섭씨 온도 배열.
    배경 추정도 이 ROI 안에서만 이뤄지므로, 반환되는 bbox의 y좌표는 ROI 기준 상대좌표다
    (전체 프레임 좌표가 필요하면 roi_y0을 더해야 함 - main()에서 시각화할 때 처리)."""
    z = z_score_map(roi_grid_c, bg_method, bg_param)
    mask = z > z_thresh
    labeled, n = label(mask)
    max_area = roi_grid_c.size * max_area_frac
    results = []
    for comp_id in range(1, n + 1):
        comp_mask = labeled == comp_id
        area = int(comp_mask.sum())
        if area < min_area or area > max_area:
            continue
        ys, xs = np.where(comp_mask)
        results.append({
            "bbox_x0": int(xs.min()), "bbox_y0": int(ys.min()) + roi_y0,
            "bbox_x1": int(xs.max()), "bbox_y1": int(ys.max()) + roi_y0,
            "area_px": area,
            "edge_sharpness": edge_sharpness(roi_grid_c, comp_mask),
            "circularity": circularity(comp_mask),
            "mean_temp_c": float(roi_grid_c[comp_mask].mean()),
            "peak_temp_c": float(roi_grid_c[comp_mask].max()),
        })
    return results


def main():
    parser = argparse.ArgumentParser(description="세션 내 도로면 ROI 국소 고온 패치 후보 탐지")
    parser.add_argument("--session", type=str, required=True, help="세션 이름 (dataset/<session>/thermal/*.npy 를 읽음)")
    parser.add_argument("--dataset-root", type=str, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--roi-top-frac", type=float, default=0.55, help="프레임 상단 이 비율까지는 제외(하늘/나무/신호등 등). 0.55면 하단 45%%만 분석 (미검증 시작값)")
    parser.add_argument("--bg-method", choices=["gaussian", "median"], default="gaussian")
    parser.add_argument("--bg-param", type=float, default=45, help="gaussian=sigma, median=kernel size")
    parser.add_argument("--z-thresh", type=float, default=3.0, help="배경(주변 도로면) 대비 robust z-score 임계값 (미검증 시작값)")
    parser.add_argument("--min-area", type=int, default=20, help="이보다 작은 연결영역은 노이즈로 간주해 제외")
    parser.add_argument("--max-area-frac", type=float, default=0.25, help="ROI 전체 대비 이 비율보다 크면 제외 (조명 변화 등 ROI 전체성 아티팩트)")
    parser.add_argument("--sharp-circularity-thresh", type=float, default=0.3, help="참고용 라벨 기준일 뿐, 후보 선정에는 안 씀")
    parser.add_argument("--sharp-edge-thresh", type=float, default=1.0, help="참고용 라벨 기준일 뿐, 후보 선정에는 안 씀")
    parser.add_argument("--sample-viz", type=int, default=0, help="N프레임마다 후보 박스 오버레이 PNG 저장 (0=끔)")
    args = parser.parse_args()

    session_dir = Path(args.dataset_root) / args.session
    thermal_dir = session_dir / "thermal"
    if not thermal_dir.exists():
        raise FileNotFoundError(f"{thermal_dir} 없음 - build_rgb_thermal_dataset.py로 먼저 만들어야 함")

    npy_files = sorted(thermal_dir.glob("*.npy"), key=lambda p: int(p.stem))
    print(f"[{args.session}] {len(npy_files)}프레임 처리 (ROI 하단 {1 - args.roi_top_frac:.0%}, bg={args.bg_method}/{args.bg_param}, z>{args.z_thresh})")

    viz_dir = session_dir / "candidate_viz"
    if args.sample_viz > 0:
        viz_dir.mkdir(exist_ok=True)

    rows = []
    roi_y0 = None
    n_empty = 0
    for npy_path in tqdm(npy_files, desc=args.session):
        idx = int(npy_path.stem)
        raw = np.load(npy_path)
        if not raw.any():
            # 녹화가 중간에 끊긴 세션은 .att 뒷부분이 0으로 패딩되어 있음 (실데이터 아님) - 건너뜀
            n_empty += 1
            continue
        grid_c = raw.astype(np.float32) / TEMP_SCALE
        if roi_y0 is None:
            roi_y0 = int(grid_c.shape[0] * args.roi_top_frac)
        roi = grid_c[roi_y0:, :]

        cands = find_candidates(roi, roi_y0, args.bg_method, args.bg_param, args.z_thresh, args.min_area, args.max_area_frac)
        for c in cands:
            # 참고용 라벨일 뿐 - 이제 sharp/diffuse로 후보를 걸러내지 않음 (지면 열패치는 diffuse일 수 있음)
            shape_label = ("sharp" if c["circularity"] >= args.sharp_circularity_thresh and c["edge_sharpness"] >= args.sharp_edge_thresh else "diffuse")
            rows.append({"frame_idx": idx, "shape_label": shape_label, **c})

        if args.sample_viz > 0 and idx % args.sample_viz == 0:
            lo, hi = np.percentile(grid_c, [1, 99])
            img = colorize(grid_c, lo, hi)
            cv2.line(img, (0, roi_y0), (grid_c.shape[1] - 1, roi_y0), (255, 255, 255), 1)
            for c in cands:
                cv2.rectangle(img, (c["bbox_x0"], c["bbox_y0"]), (c["bbox_x1"], c["bbox_y1"]), (0, 0, 255), 1)
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
    empty_note = f", 빈 프레임(0으로 패딩됨) {n_empty}개 건너뜀" if n_empty else ""
    print(f"완료: 후보 {len(rows)}개 (sharp {n_sharp} / diffuse {len(rows) - n_sharp}){empty_note} -> {out_csv}")
    if args.sample_viz > 0:
        print(f"오버레이 시각화 -> {viz_dir}")


if __name__ == "__main__":
    main()

"""
세션 하나를 분석한다: (1) 도로면 ROI 내 국소 열원 후보 탐지 -> (2) 프레임 간 추적으로
동행 차량(다른 패턴) vs 고정 지면 이상(다가가며 커지다 하단 이탈) 구분.
(구 detect_hotspot_candidates.py + track_hotspot_candidates.py 통합)

## 1단계: 후보 탐지 (find_candidates / detect_candidates)

차량 이동 촬영이라 고정 배경이 없다. 프레임 전체(하늘/나무/건물/다른 차량 포함)를 다
보면 신호등·차량 엔진열·후미등까지 다 잡히므로, 프레임 하단(도로면 추정)만 ROI로 잘라
그 안에서만 배경 추정(z_score_map, heat_pipe_shape_heuristic.py 재사용) 후 z-score
임계값을 넘는 연결영역을 후보로 낸다. sharp/diffuse 형태 라벨은 참고용일 뿐 후보
선정에는 안 씀 — 지하 배관 누수로 인한 지면 열패치는 오히려 경계가 흐릿한 diffuse에
가까울 수 있기 때문.

## 2단계: 프레임 간 추적 (build_tracks / track_candidates)

ROI만으로는 도로 위 다른 차량을 못 거른다. 진짜 지면 위 고정된 지점은 차가 다가갈수록
아래로 내려가며 커지다 화면 하단에서 사라지고, 동행 차량은 비슷한 높이/크기를 유지하다
방향을 틀며 사라진다 — 이 움직임 패턴으로 후보를 프레임 간에 이어붙여(track) 구분한다.
circularity(원형도)도 참고: 실측 확인 결과 낮은(길쭉한) track 대부분이 자전거도로
도색/속도표시 페인트였음.

주의: 전부 실측 검증 전 시작값 임계값이다. 정답 라벨이 없으므로 출력은 "후보"이지
"판정"이 아니다 — tracks.csv의 likely_ground_fixed로 걸러진 것도 실측(2026-08-20)해보면
차량이 다가가며 정지하는 경우(신호대기 등) 등 오탐이 여전히 섞여 있었다.
자세한 경과는 ../../docs/EXPERIMENT_SUMMARY.md 참고.

사용법 (heat-pipe/ 에서 실행):
    python scripts/experiments/analyze_session.py --session 20260813_133232
    python scripts/experiments/analyze_session.py --session 20260813_133232 --skip-detect   # candidates.csv 재사용, 추적만 다시
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import defaultdict
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
from utils.thermal_viz import imwrite_unicode, colorize  # noqa: E402

DEFAULT_DATASET_ROOT = r"E:\열수송관 모니터링 데이터\dataset"


# ── 1단계: 후보 탐지 ──────────────────────────────────────────────────────────

def find_candidates(roi_grid_c: np.ndarray, roi_y0: int, bg_method: str, bg_param: float, z_thresh: float, min_area: int, max_area_frac: float):
    """roi_grid_c: 이미 하늘/나무 등을 잘라낸 ROI(도로면 추정 영역)의 섭씨 온도 배열.
    배경 추정도 이 ROI 안에서만 이뤄지므로, 반환되는 bbox의 y좌표는 전체 프레임 기준으로
    맞추기 위해 roi_y0을 더해서 돌려준다."""
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


def detect_candidates(session_dir: Path, roi_top_frac: float, bg_method: str, bg_param: float,
                       z_thresh: float, min_area: int, max_area_frac: float,
                       sharp_circularity_thresh: float, sharp_edge_thresh: float, sample_viz: int) -> Path:
    thermal_dir = session_dir / "thermal"
    if not thermal_dir.exists():
        raise FileNotFoundError(f"{thermal_dir} 없음 - build_rgb_thermal_dataset.py로 먼저 만들어야 함")

    npy_files = sorted(thermal_dir.glob("*.npy"), key=lambda p: int(p.stem))
    print(f"[{session_dir.name}] 1단계 후보 탐지: {len(npy_files)}프레임 (ROI 하단 {1 - roi_top_frac:.0%}, bg={bg_method}/{bg_param}, z>{z_thresh})")

    viz_dir = session_dir / "candidate_viz"
    if sample_viz > 0:
        viz_dir.mkdir(exist_ok=True)

    rows = []
    roi_y0 = None
    n_empty = 0
    for npy_path in tqdm(npy_files, desc=session_dir.name):
        idx = int(npy_path.stem)
        raw = np.load(npy_path)
        if not raw.any():
            # 녹화가 중간에 끊긴 세션은 .att 뒷부분이 0으로 패딩되어 있음 (실데이터 아님) - 건너뜀
            n_empty += 1
            continue
        grid_c = raw.astype(np.float32) / TEMP_SCALE
        if roi_y0 is None:
            roi_y0 = int(grid_c.shape[0] * roi_top_frac)
        roi = grid_c[roi_y0:, :]

        cands = find_candidates(roi, roi_y0, bg_method, bg_param, z_thresh, min_area, max_area_frac)
        for c in cands:
            # 참고용 라벨일 뿐 - sharp/diffuse로 후보를 걸러내지 않음 (지면 열패치는 diffuse일 수 있음)
            shape_label = ("sharp" if c["circularity"] >= sharp_circularity_thresh and c["edge_sharpness"] >= sharp_edge_thresh else "diffuse")
            rows.append({"frame_idx": idx, "shape_label": shape_label, **c})

        if sample_viz > 0 and idx % sample_viz == 0:
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
    print(f"  완료: 후보 {len(rows)}개 (sharp {n_sharp} / diffuse {len(rows) - n_sharp}){empty_note} -> {out_csv}")
    if sample_viz > 0:
        print(f"  오버레이 시각화 -> {viz_dir}")
    return out_csv


# ── 2단계: 프레임 간 추적 ──────────────────────────────────────────────────────

def load_candidates_by_frame(session_dir: Path) -> dict:
    csv_path = session_dir / "candidates.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"{csv_path} 없음 - 먼저 1단계(후보 탐지)를 실행해야 함")
    by_frame = defaultdict(list)
    with open(csv_path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row["frame_idx"] = int(row["frame_idx"])
            for k in ("bbox_x0", "bbox_y0", "bbox_x1", "bbox_y1", "area_px"):
                row[k] = int(row[k])
            for k in ("edge_sharpness", "circularity", "mean_temp_c", "peak_temp_c"):
                row[k] = float(row[k])
            by_frame[row["frame_idx"]].append(row)
    return by_frame


def centroid(c):
    return ((c["bbox_x0"] + c["bbox_x1"]) / 2, (c["bbox_y0"] + c["bbox_y1"]) / 2)


def build_tracks(by_frame: dict, max_dist: float, max_gap: int):
    """그리디 최근접 매칭. 각 track은 [(frame_idx, candidate_dict), ...]"""
    active = []  # [{"points": [...], "last_frame": int}]
    finished = []
    frames = sorted(by_frame.keys())
    for f in frames:
        cands = list(by_frame[f])
        used = set()
        for track in active:
            last_frame, last_c = track["points"][-1]
            if f - last_frame > max_gap:
                continue  # 아래에서 gap 초과 track은 정리
            lx, ly = centroid(last_c)
            best, best_dist = None, max_dist
            for i, c in enumerate(cands):
                if i in used:
                    continue
                cx, cy = centroid(c)
                d = math.hypot(cx - lx, cy - ly)
                if d < best_dist:
                    best, best_dist = i, d
            if best is not None:
                track["points"].append((f, cands[best]))
                track["last_frame"] = f
                used.add(best)

        still_active = []
        for track in active:
            if f - track["last_frame"] > max_gap:
                finished.append(track)
            else:
                still_active.append(track)
        active = still_active

        for i, c in enumerate(cands):
            if i not in used:
                active.append({"points": [(f, c)], "last_frame": f})

    finished.extend(active)
    return finished


def summarize_track(track: dict, track_id: int, frame_height: int, bottom_margin: int, growth_thresh: float, min_circularity: float):
    points = track["points"]
    frames = [p[0] for p in points]
    first_c, last_c = points[0][1], points[-1][1]
    area0, area1 = first_c["area_px"], last_c["area_px"]
    y0 = centroid(first_c)[1]
    y1 = centroid(last_c)[1]
    exits_bottom = last_c["bbox_y1"] >= frame_height - 1 - bottom_margin
    growth = area1 / area0 if area0 > 0 else float("nan")
    mean_circularity = float(np.mean([p[1]["circularity"] for p in points]))
    # circularity 낮음(=길쭉하게 퍼진 모양) -> 도색 차선(자전거도로/속도표시) 페인트일 가능성이 높음(실측으로 확인).
    # 높음(=뭉친/원형에 가까움) -> 국소적인 지점형 이상일 가능성. min_circularity도 아직 미검증 시작값.
    likely_ground_fixed = (
        len(points) >= 3 and growth >= growth_thresh and exits_bottom and y1 > y0
        and mean_circularity >= min_circularity
    )
    return {
        "track_id": track_id,
        "n_frames": len(points),
        "start_frame": frames[0],
        "end_frame": frames[-1],
        "area_start": area0,
        "area_end": area1,
        "mean_circularity": round(mean_circularity, 3),
        "area_growth_ratio": round(growth, 3) if not math.isnan(growth) else "",
        "y_center_start": round(y0, 1),
        "y_center_end": round(y1, 1),
        "moved_down": y1 > y0,
        "exits_bottom": exits_bottom,
        "mean_peak_temp_c": round(float(np.mean([p[1]["peak_temp_c"] for p in points])), 2),
        "likely_ground_fixed": likely_ground_fixed,
    }


def track_candidates(session_dir: Path, max_dist: float, max_gap: int, bottom_margin: int,
                      growth_thresh: float, min_circularity: float) -> Path:
    by_frame = load_candidates_by_frame(session_dir)
    total_cands = sum(len(v) for v in by_frame.values())
    print(f"[{session_dir.name}] 2단계 프레임 간 추적: {len(by_frame)}개 프레임, 후보 {total_cands}개")

    thermal_dir = session_dir / "thermal"
    sample_npy = next(thermal_dir.glob("*.npy"))
    frame_height = np.load(sample_npy).shape[0]

    tracks = build_tracks(by_frame, max_dist, max_gap)
    rows = [
        summarize_track(t, i, frame_height, bottom_margin, growth_thresh, min_circularity)
        for i, t in enumerate(tracks)
    ]
    rows.sort(key=lambda r: -r["n_frames"])

    out_csv = session_dir / "tracks.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        if rows:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    n_ground = sum(1 for r in rows if r["likely_ground_fixed"])
    n_multi = sum(1 for r in rows if r["n_frames"] >= 3)
    print(f"  완료: track {len(rows)}개 (3프레임+ 지속 {n_multi}개, likely_ground_fixed {n_ground}개) -> {out_csv}")
    if n_ground:
        print("  likely_ground_fixed track (frame 범위) - candidate_viz에서 이 구간 프레임들 대조해서 확인:")
        for r in rows:
            if r["likely_ground_fixed"]:
                print(f"    track {r['track_id']}: frame {r['start_frame']}~{r['end_frame']} "
                      f"(면적 {r['area_start']}->{r['area_end']}px, circularity {r['mean_circularity']}, peak {r['mean_peak_temp_c']}°C)")
    return out_csv


def main():
    parser = argparse.ArgumentParser(description="세션 분석: 도로면 ROI 후보 탐지 + 프레임 간 추적")
    parser.add_argument("--session", type=str, required=True, help="세션 이름 (dataset/<session>/thermal/*.npy 를 읽음)")
    parser.add_argument("--dataset-root", type=str, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--skip-detect", action="store_true", help="기존 candidates.csv 재사용, 1단계(탐지) 생략하고 추적만")
    parser.add_argument("--skip-track", action="store_true", help="1단계(탐지)만 하고 2단계(추적) 생략")
    # 1단계 파라미터
    parser.add_argument("--roi-top-frac", type=float, default=0.55, help="프레임 상단 이 비율까지는 제외(하늘/나무/신호등 등). 0.55면 하단 45%%만 분석 (미검증 시작값)")
    parser.add_argument("--bg-method", choices=["gaussian", "median"], default="gaussian")
    parser.add_argument("--bg-param", type=float, default=45, help="gaussian=sigma, median=kernel size")
    parser.add_argument("--z-thresh", type=float, default=3.0, help="배경(주변 도로면) 대비 robust z-score 임계값 (미검증 시작값)")
    parser.add_argument("--min-area", type=int, default=20, help="이보다 작은 연결영역은 노이즈로 간주해 제외")
    parser.add_argument("--max-area-frac", type=float, default=0.25, help="ROI 전체 대비 이 비율보다 크면 제외 (조명 변화 등 ROI 전체성 아티팩트)")
    parser.add_argument("--sharp-circularity-thresh", type=float, default=0.3, help="참고용 라벨 기준일 뿐, 후보 선정에는 안 씀")
    parser.add_argument("--sharp-edge-thresh", type=float, default=1.0, help="참고용 라벨 기준일 뿐, 후보 선정에는 안 씀")
    parser.add_argument("--sample-viz", type=int, default=0, help="N프레임마다 후보 박스 오버레이 PNG 저장 (0=끔)")
    # 2단계 파라미터
    parser.add_argument("--max-dist", type=float, default=25.0, help="프레임 간 같은 물체로 볼 최대 중심좌표 이동거리(px, 미검증 시작값)")
    parser.add_argument("--max-gap", type=int, default=1, help="이 프레임 수 이상 안 이어지면 track 종료")
    parser.add_argument("--bottom-margin", type=int, default=5, help="이 픽셀 이내로 화면 하단에 닿으면 '하단 이탈'로 간주")
    parser.add_argument("--growth-thresh", type=float, default=1.3, help="likely_ground_fixed 판정용 면적 증가율 기준 (미검증)")
    parser.add_argument("--min-circularity", type=float, default=0.35, help="이보다 낮으면 길쭉한 도색 차선 등으로 보고 제외 (실측 확인: circularity 낮은 track 대부분이 자전거도로 도색/속도표시였음, 미검증 시작값)")
    args = parser.parse_args()

    session_dir = Path(args.dataset_root) / args.session

    if not args.skip_detect:
        detect_candidates(
            session_dir, args.roi_top_frac, args.bg_method, args.bg_param, args.z_thresh,
            args.min_area, args.max_area_frac, args.sharp_circularity_thresh, args.sharp_edge_thresh, args.sample_viz,
        )

    if not args.skip_track:
        track_candidates(
            session_dir, args.max_dist, args.max_gap, args.bottom_margin, args.growth_thresh, args.min_circularity,
        )


if __name__ == "__main__":
    main()
